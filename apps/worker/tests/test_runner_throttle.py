"""Phân loại lỗi MỞ BROWSER (GemLoginError) → THROTTLED, không phải OK (chống vòng hammer).

Bối cảnh thật: dồn nhiều link vào 1 profile → GemLogin kẹt "being opened" → mở không được. Trước đây worker
báo profile_health=OK → orchestrator giữ AVAILABLE → re-queue → hammer → treo. Giờ: GemLoginError khi mở →
THROTTLED → orchestrator cho profile NGHỈ NGẮN (cắt hammer, GemLogin hồi), KHÔNG DEAD. url_status vẫn
INCONCLUSIVE (INV-1). Lỗi hạ tầng KHÁC (sau khi đã mở) vẫn OK (re-queue thường).
"""

from __future__ import annotations

from fastcheck_worker.browser.adapter import BrowserHandle, GemLoginError
from fastcheck_worker.contracts import ProfileHealth, UrlStatus
from fastcheck_worker.runner import run_check

_PAYLOAD = {
    "platform": "TIKTOK",
    "target_url": "https://www.tiktok.com/@user/video/1",
    "cookie": "",
    "fixture_base_url": None,
    "gemlogin_profile_id": "4",
}


class _OpenFailsAdapter:
    """Adapter giả: MỞ browser luôn ném GemLoginError (mô phỏng GemLogin kẹt 'being opened')."""

    def __init__(self) -> None:
        self.closed: list[str] = []

    def open_browser(self, gemlogin_profile_id: str, cookie: str = "") -> BrowserHandle:
        raise GemLoginError("Profile is currently being opened. Please wait.")

    def close_browser(self, gemlogin_profile_id: str) -> None:
        self.closed.append(gemlogin_profile_id)

    # các method khác không dùng trong test này
    def create_profile(self, spec: object) -> str:  # pragma: no cover
        return "x"

    def update_profile(self, gid: str, changes: dict[str, object]) -> None:  # pragma: no cover
        return None

    def delete_profile(self, gid: str) -> None:  # pragma: no cover
        return None

    def list_profiles(self) -> list[object]:  # pragma: no cover
        return []


def test_browser_open_failure_is_throttled_not_ok() -> None:
    adapter = _OpenFailsAdapter()
    outcome = run_check(dict(_PAYLOAD), adapter=adapter)  # type: ignore[arg-type]
    # url_status: KHÔNG kết luận target (INV-1).
    assert outcome["url_status"] == UrlStatus.INCONCLUSIVE.value
    # profile_health: THROTTLED → orchestrator cho nghỉ ngắn (cắt hammer), KHÔNG DEAD/không kết tội.
    assert outcome["profile_health"] == ProfileHealth.THROTTLED.value
    assert outcome["block_reason"] == "browser_open_failed:GemLoginError"
    # Vẫn dọn (đóng) để chống rò tiến trình (INV-9).
    assert adapter.closed == ["4"]
