"""Guard đăng nhập cookie-first (INV-2/INV-8): cookie session là tín hiệu chắc chắn, locale-independent.

Bối cảnh thật (đã kiểm trên GemLogin): FB tiếng Việt/SPA KHÔNG render selector DOM tiếng Anh trên trang
group → guard DOM dương tính giả 'chưa đăng nhập'. Cookie c_user+xs thì luôn có → guard đúng.
"""

from __future__ import annotations

from collections.abc import Iterable

from fastcheck_worker.detectors.base import verify_logged_in
from fastcheck_worker.detectors.facebook import FACEBOOK_SPEC


class _FakePage:
    def __init__(self, url: str, cookies: Iterable[str] = (), elements: Iterable[str] = ()) -> None:
        self._url = url
        self._cookies = set(cookies)
        self._elements = set(elements)

    @property
    def http_status(self) -> int | None:
        return 200

    @property
    def final_url(self) -> str:
        return self._url

    def has_element(self, *selectors: str) -> bool:
        return any(s in self._elements for s in selectors if s)

    def text_contains(self, *needles: str) -> bool:
        return False

    def cookie_names(self) -> set[str]:
        return self._cookies


def test_cookie_guard_logged_in_without_any_dom_marker() -> None:
    # FB tiếng Việt trang group: không selector DOM khớp, nhưng đủ cookie session → ĐÃ đăng nhập.
    page = _FakePage(
        "https://www.facebook.com/groups/123/?locale=vi_VN", cookies={"c_user", "xs", "datr"}
    )
    assert verify_logged_in(page, FACEBOOK_SPEC) is True


def test_cookie_guard_needs_all_core_cookies() -> None:
    # Chỉ c_user (thiếu xs) → không đủ → fallback DOM → không có avatar → CHƯA đăng nhập (không dương tính giả).
    page = _FakePage("https://www.facebook.com/groups/123/", cookies={"c_user"})
    assert verify_logged_in(page, FACEBOOK_SPEC) is False


def test_dom_fallback_still_works_without_cookie() -> None:
    # Fake/golden (cookie rỗng) vẫn dựa DOM avatar như cũ — không phá hành vi cũ.
    page = _FakePage("https://www.facebook.com/", elements={FACEBOOK_SPEC.login_selectors[0]})
    assert verify_logged_in(page, FACEBOOK_SPEC) is True


def test_redirect_to_login_overrides_cookie() -> None:
    # Bị đẩy về /login → CHƯA đăng nhập dù có cookie sót lại (INV-2).
    page = _FakePage("https://www.facebook.com/login/", cookies={"c_user", "xs"})
    assert verify_logged_in(page, FACEBOOK_SPEC) is False
