"""Phase 4 — FakeGemLoginAdapter: CRUD profile + mở/đóng browser + đồng bộ danh sách (§3, §4).

open_browser idempotent theo profile (INV-6: 1 profile = 1 browser) — quan trọng để "gửi lệnh mở 2 lần
chỉ mở 1 browser" (Test 1) đúng cả ở tầng adapter, không chỉ dựa command_id.
"""

from __future__ import annotations

from fastcheck_worker.browser.adapter import FakeGemLoginAdapter, ProfileSpec
from fastcheck_worker.process.kill import kill_process_tree


def test_crud_and_list() -> None:
    adapter = FakeGemLoginAdapter()
    gid = adapter.create_profile(ProfileSpec(platform="TIKTOK", name="acc-1"))
    assert gid.startswith("fake-")
    ids = [p.gemlogin_profile_id for p in adapter.list_profiles()]
    assert gid in ids

    adapter.update_profile(gid, {"account_label": "acc-1-renamed"})
    summary = next(p for p in adapter.list_profiles() if p.gemlogin_profile_id == gid)
    assert summary.name == "acc-1-renamed"
    assert summary.platform == "TIKTOK"

    adapter.delete_profile(gid)
    assert gid not in [p.gemlogin_profile_id for p in adapter.list_profiles()]


def test_open_browser_idempotent_per_profile() -> None:
    adapter = FakeGemLoginAdapter(fake_browser_ttl_seconds=60.0)
    try:
        h1 = adapter.open_browser("p1")
        h2 = adapter.open_browser("p1")  # mở lại cùng profile → KHÔNG spawn browser thứ 2 (INV-6)
        assert h1.pid == h2.pid
    finally:
        adapter.close_browser("p1")


def test_open_then_close_kills_tree() -> None:
    import psutil

    adapter = FakeGemLoginAdapter(fake_browser_ttl_seconds=60.0)
    handle = adapter.open_browser("p-close")
    assert psutil.pid_exists(handle.pid)
    adapter.close_browser("p-close")
    import time

    deadline = time.monotonic() + 5
    while psutil.pid_exists(handle.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not psutil.pid_exists(handle.pid)
    kill_process_tree(handle.pid)  # dọn nếu còn sót (an toàn)
