"""Test 6 (Phase 3) — Multi-profile song song: bounded thread pool KHÔNG vượt max_concurrency.

Chạy N job song song qua `CheckRunner` (pool size = max_concurrency) tới một server CHẬM có đếm số
request đồng thời. Khẳng định: số "browser" (request) chạy cùng lúc ≤ max_concurrency (INV-10 +
ADR-0007) và có song song thật (>1). Mỗi job đi qua một lần fetch độc lập (1 job = 1 context — INV-6).
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fastcheck_worker.contracts import Platform, UrlStatus
from fastcheck_worker.runner import CheckRunner

LIVE_HTML = (Path(__file__).resolve().parent / "fixtures" / "live.html").read_bytes()


class _Counter:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.current = 0
        self.max_seen = 0


def _make_handler(counter: _Counter, sleep_s: float) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            with counter.lock:
                counter.current += 1
                counter.max_seen = max(counter.max_seen, counter.current)
            try:
                time.sleep(sleep_s)  # giữ request "mở" đủ lâu để đo song song
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(LIVE_HTML)
            finally:
                with counter.lock:
                    counter.current -= 1

        def log_message(self, *args: object) -> None:
            return

    return Handler


@pytest.fixture()
def slow_server() -> Iterator[tuple[str, _Counter]]:
    counter = _Counter()
    handler_cls = _make_handler(counter, sleep_s=0.3)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield f"http://{host}:{port}", counter
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_bounded_pool_does_not_exceed_max_concurrency(
    slow_server: tuple[str, _Counter],
) -> None:
    base, counter = slow_server
    max_concurrency = 3
    runner = CheckRunner(max_concurrency=max_concurrency, job_timeout_seconds=30.0)
    assert runner.max_concurrency == max_concurrency

    async def run_many() -> list[dict[str, object]]:
        payloads = [
            {
                "platform": Platform.TIKTOK.value,
                "target_url": f"{base}/live.html",
                "cookie": "fake",
                "fixture_base_url": None,
                "gemlogin_profile_id": None,
            }
            for _ in range(9)
        ]
        return await asyncio.gather(*(runner.run(p) for p in payloads))  # type: ignore[arg-type]

    try:
        outcomes = asyncio.run(run_many())
    finally:
        runner.shutdown()

    # Tất cả job hoàn tất và cho kết quả LIVE (mỗi job một lần fetch độc lập — INV-6).
    assert len(outcomes) == 9
    assert all(o["url_status"] == UrlStatus.LIVE.value for o in outcomes)
    # Cốt lõi INV-10: số request đồng thời KHÔNG vượt max_concurrency.
    assert counter.max_seen <= max_concurrency, f"observed {counter.max_seen} > {max_concurrency}"
    # Sanity: có song song thật (không phải chạy tuần tự từng cái).
    assert counter.max_seen >= 2
