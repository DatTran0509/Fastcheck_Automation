"""Kill CẢ CÂY tiến trình (INV-9). Kill mỗi PID cha để sót con = rò RAM âm thầm rồi máy sập.

Máy trạm GemLogin là Windows → `taskkill /PID x /T /F` là cách chuẩn (INV-9, ADR-0006). Để test tất định
chạy được trên mọi HĐH (CI), liệt kê cây con bằng **psutil** (`children(recursive=True)`) rồi terminate/kill.
Linux/macOS (nếu về sau chạy headless): SIGTERM cả cây → chờ ngắn → SIGKILL còn sót. Zombie: `kill -9` vô
tác dụng — cha phải `wait()`/reap; ở đây `psutil.wait_procs` poll cho tới khi tiến trình biến mất.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable

import psutil

logger = logging.getLogger("fastcheck.worker.kill")


def _safe(fn: Callable[[], None]) -> None:
    try:
        fn()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass


def kill_process_tree(pid: int, timeout: float = 5.0) -> int:
    """Kill `pid` + toàn bộ tiến trình con (đệ quy). Trả số tiến trình đã kết thúc.

    An toàn (không raise) khi `pid` không còn tồn tại. LUÔN chụp danh sách con TRƯỚC khi kill cha —
    kill cha xong sẽ không liệt kê được con nữa (nguồn sót con kinh điển).
    """
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return 0

    procs = parent.children(recursive=True)
    procs.append(parent)

    if sys.platform == "win32":
        # /T = cả cây con, /F = force. Cách đúng trên máy trạm GemLogin (INV-9).
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
    else:
        # Linux/macOS: cho cơ hội đóng sạch trước (SIGTERM), rồi SIGKILL kẻ còn sống.
        for proc in procs:
            _safe(proc.terminate)
        _, alive = psutil.wait_procs(procs, timeout=timeout)
        for proc in alive:
            _safe(proc.kill)

    # Xác nhận đã sạch cây (poll tới khi biến mất) — kể cả Windows sau taskkill.
    gone, alive = psutil.wait_procs(procs, timeout=timeout)
    for proc in alive:
        _safe(proc.kill)

    killed = sum(1 for proc in procs if not _is_alive(proc))
    if alive:
        logger.warning("kill_process_tree(%d): còn %d tiến trình sống sau timeout", pid, len(alive))
    return killed


def _is_alive(proc: psutil.Process) -> bool:
    """Còn sống thực sự? Zombie (đã chết, chờ cha reap) KHÔNG tính là sống."""
    try:
        return bool(proc.is_running()) and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
