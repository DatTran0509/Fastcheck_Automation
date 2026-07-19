"""Tiến trình 'browser giả' cho FakeGemLoginAdapter.

Sinh MỘT tiến trình con rồi ngủ → tạo cây tiến trình THẬT để test process hygiene (kill cây →
`psutil.children` rỗng, không sót con mồ côi — INV-9). KHÔNG phải browser thật; đường thật dùng GemLogin +
DrissionPage. Nhận TTL (giây) qua argv để tự chết nếu bị quên đóng (chống rò khi test lỗi).

Chạy:  python -m fastcheck_worker.browser._fake_browser [ttl_seconds]
"""

from __future__ import annotations

import subprocess
import sys
import time


def main() -> None:
    ttl = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
    # Một "renderer" con để cây có tiến trình con — điểm mấu chốt của test kill cây.
    child = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", f"import time; time.sleep({ttl})"],
    )
    try:
        time.sleep(ttl)
    finally:
        child.terminate()


if __name__ == "__main__":
    main()
