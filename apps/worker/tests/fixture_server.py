"""Static server phục vụ golden fixtures qua HTTP thật (yêu cầu: "load fixture qua static server").

Trả HTTP status THẬT theo tên file: `dead_404.html` → 404 (hard-404), còn lại → 200.
Dùng chung cho golden test (pytest) và integration E2E. Dừng bằng `.stop()`.
"""

from __future__ import annotations

import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# File nào server trả HTTP status khác 200 (mô phỏng hard-404 của nền tảng). Gồm cả per-platform.
_STATUS_OVERRIDES: dict[str, int] = {
    "/dead_404.html": 404,
    "/facebook/dead_404.html": 404,
    "/twitter/dead_404.html": 404,
    "/youtube/dead_404.html": 404,
}


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(FIXTURES_DIR), **kwargs)  # type: ignore[arg-type]

    def send_response(self, code: int, message: str | None = None) -> None:
        # Ép status cho các file cấu hình (vẫn trả nguyên body để detector đọc nội dung).
        override = _STATUS_OVERRIDES.get(self.path.split("?")[0])
        if override is not None and code == 200:
            code = override
        super().send_response(code, message)

    def log_message(self, *args: object) -> None:  # im lặng trong test
        return


class FixtureServer:
    def __init__(self) -> None:
        # Cổng 0 = OS cấp cổng trống → chạy song song nhiều test không đụng nhau.
        self._httpd = HTTPServer(("127.0.0.1", 0), partial(_Handler))
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> str:
        self._thread.start()
        host, port = self._httpd.server_address[0], self._httpd.server_address[1]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[0], self._httpd.server_address[1]
        return f"http://{host}:{port}"
