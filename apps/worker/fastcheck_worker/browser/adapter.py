"""Adapter GemLogin. DrissionPage sẽ attach vào CDP endpoint GemLogin phơi ra (ADR-0006).

Phase 0: chỉ có interface + FakeGemLoginAdapter (chưa mở browser thật). Chọn qua GEMLOGIN_MODE.
"""

from __future__ import annotations

from typing import Protocol


class GemLoginAdapter(Protocol):
    def open_browser(self, profile_id: str) -> str:
        """Inject cookie → mở GemLogin (vân tay + proxy sticky) → trả địa chỉ CDP endpoint."""
        ...

    def close_browser(self, profile_id: str) -> None:
        """Đóng browser + kill cây tiến trình (INV-9)."""
        ...


class FakeGemLoginAdapter:
    """Dev/test: KHÔNG mở browser thật (Phase 0). Cho phép chạy end-to-end không cần GemLogin."""

    def open_browser(self, profile_id: str) -> str:
        raise NotImplementedError(
            f"FakeGemLoginAdapter chưa mở browser ở Phase 0 (profile={profile_id}, ADR-0006).",
        )

    def close_browser(self, profile_id: str) -> None:
        return None
