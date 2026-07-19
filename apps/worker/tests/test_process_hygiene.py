"""Phase 4 — Test 4: process hygiene (INV-9).

- kill_process_tree: mở "browser giả" (cây tiến trình THẬT) rồi kill → KHÔNG còn tiến trình con mồ côi.
- ResourceMonitor: browser vượt ngưỡng RAM → kill cây + callback giải phóng.
- CheckRunner: job quá hạn → hard timeout → INCONCLUSIVE (KHÔNG DEAD — INV-1), dọn nhanh (không treo).
"""

from __future__ import annotations

import asyncio
import time

import psutil
import pytest

from fastcheck_worker.browser.adapter import FakeGemLoginAdapter
from fastcheck_worker.process.kill import kill_process_tree
from fastcheck_worker.process.monitor import ResourceMonitor, child_pids


def _wait_children(pid: int, want: int, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    n = 0
    while time.monotonic() < deadline:
        try:
            n = len(psutil.Process(pid).children(recursive=True))
        except psutil.NoSuchProcess:
            return 0
        if n >= want:
            return n
        time.sleep(0.05)
    return n


def _wait_gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        time.sleep(0.05)
    return not psutil.pid_exists(pid)


def test_kill_process_tree_leaves_no_orphans() -> None:
    adapter = FakeGemLoginAdapter(fake_browser_ttl_seconds=60.0)
    handle = adapter.open_browser("p-kill")
    try:
        assert _wait_children(handle.pid, 1) >= 1, "browser giả phải có tiến trình con để test kill cây"
        children = child_pids(handle.pid)
        assert children, "phải chụp được PID con TRƯỚC khi kill"

        killed = kill_process_tree(handle.pid)
        assert killed >= 1

        # Cha + toàn bộ con biến mất — không mồ côi (nguồn rò RAM âm thầm nếu sót con — INV-9).
        assert _wait_gone(handle.pid), "tiến trình cha còn sống sau kill cây"
        for child in children:
            assert _wait_gone(child), f"tiến trình con {child} mồ côi sau kill cây"
    finally:
        kill_process_tree(handle.pid)


def test_resource_monitor_kills_over_ram_and_frees() -> None:
    adapter = FakeGemLoginAdapter(fake_browser_ttl_seconds=60.0)
    handle = adapter.open_browser("p-ram")
    try:
        _wait_children(handle.pid, 1)
        monitor = ResourceMonitor(ram_limit_mb=0.0)  # mọi RAM > 0 → vượt ngưỡng
        monitor.track("p-ram", handle.pid)

        breached: list[str] = []
        killed = monitor.sweep(breached.append)

        assert "p-ram" in killed
        assert breached == ["p-ram"]  # callback giải phóng slot/profile được gọi
        assert monitor.tracked() == {}  # bỏ theo dõi sau khi kill
        assert _wait_gone(handle.pid), "browser vượt RAM phải bị kill cả cây"
    finally:
        kill_process_tree(handle.pid)


def test_runner_hard_timeout_is_inconclusive_not_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    import fastcheck_worker.runner as runner_mod

    def _hang(
        payload: runner_mod.CheckPayload,
        adapter: object | None = None,
        on_progress: object | None = None,
    ) -> runner_mod.CheckOutcome:
        time.sleep(5.0)  # job "treo" lâu hơn timeout
        return runner_mod.CheckOutcome(
            url_status="LIVE",
            profile_health="OK",
            block_reason=None,
            response_time_ms=1,
            fresh_cookie=None,
        )

    monkeypatch.setattr(runner_mod, "run_check", _hang)
    runner = runner_mod.CheckRunner(max_concurrency=2, job_timeout_seconds=0.5)
    try:
        start = time.monotonic()
        outcome = asyncio.run(
            runner.run(
                {
                    "platform": "TIKTOK",
                    "target_url": "x",
                    "cookie": "",
                    "fixture_base_url": None,
                    "gemlogin_profile_id": None,
                }
            )
        )
        elapsed = time.monotonic() - start
        # INV-9/INV-1: quá hạn cứng → INCONCLUSIVE (KHÔNG DEAD), dọn NHANH (không chờ hết 5s).
        assert outcome["url_status"] == "INCONCLUSIVE"
        assert outcome["block_reason"] == "timeout"
        assert elapsed < 3.0
    finally:
        runner.shutdown()
