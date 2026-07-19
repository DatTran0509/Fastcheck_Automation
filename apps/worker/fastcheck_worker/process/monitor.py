"""Giám sát RAM/PID tiến trình browser (INV-9, psutil).

RAM bò lên từ từ là dấu hiệu kinh điển của rò tiến trình → máy trạm sập. Client App theo dõi RAM THỰC TẾ
của từng browser (cây tiến trình); vượt ngưỡng → kill cây → giải phóng slot → trả profile về pool.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import psutil

from .kill import kill_process_tree

logger = logging.getLogger("fastcheck.worker.monitor")


def process_tree_rss_mb(pid: int) -> float:
    """Tổng RAM (RSS, MB) của `pid` + toàn bộ con. 0 nếu tiến trình không còn."""
    try:
        parent = psutil.Process(pid)
        total: int = parent.memory_info().rss
        for child in parent.children(recursive=True):
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return float(total) / (1024 * 1024)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def child_pids(pid: int) -> list[int]:
    """Danh sách PID con (đệ quy). Dùng để kiểm tra 'không còn tiến trình con mồ côi' sau kill."""
    try:
        return [c.pid for c in psutil.Process(pid).children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


class ResourceMonitor:
    """Theo dõi các browser đang mở; vượt ngưỡng RAM → kill cây + gọi callback giải phóng slot/profile.

    `key` là định danh do caller đặt (vd profile_id) để callback biết browser nào bị dọn. Không giữ trạng
    thái pool ở đây (INV-5) — chỉ đo tài nguyên cục bộ máy trạm và ra quyết định kill.
    """

    def __init__(self, ram_limit_mb: float) -> None:
        self._ram_limit_mb = ram_limit_mb
        self._tracked: dict[str, int] = {}  # key -> pid

    def track(self, key: str, pid: int) -> None:
        self._tracked[key] = pid

    def untrack(self, key: str) -> None:
        self._tracked.pop(key, None)

    def tracked(self) -> dict[str, int]:
        return dict(self._tracked)

    def sweep(self, on_breach: Callable[[str], None]) -> list[str]:
        """Quét một vòng: kill mọi browser vượt ngưỡng RAM. Trả danh sách key đã kill."""
        killed: list[str] = []
        for key, pid in list(self._tracked.items()):
            rss = process_tree_rss_mb(pid)
            if rss > self._ram_limit_mb:
                logger.warning(
                    "browser %s vượt RAM %.0fMB > %.0f → kill cây tiến trình (INV-9)",
                    key,
                    rss,
                    self._ram_limit_mb,
                )
                kill_process_tree(pid)
                self._tracked.pop(key, None)
                on_breach(key)
                killed.append(key)
        return killed
