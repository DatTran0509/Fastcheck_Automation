"""Kill cây tiến trình (INV-9). Máy trạm GemLogin là Windows → taskkill /T /F.

Kill mỗi PID cha để sót con = rò RAM âm thầm rồi máy sập. Luôn kill CẢ CÂY.
"""

from __future__ import annotations

import subprocess
import sys


def kill_process_tree(pid: int) -> None:
    if sys.platform == "win32":
        # /T = cả cây con, /F = force. Đây là cách đúng trên máy trạm GemLogin (INV-9, ADR-0006).
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
        return
    raise NotImplementedError(
        "kill_process_tree hiện chỉ hỗ trợ Windows (máy trạm GemLogin). "
        "Nếu về sau chạy headless trên Linux: kill process group + reap.",
    )
