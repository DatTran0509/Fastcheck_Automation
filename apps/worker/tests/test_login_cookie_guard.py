"""Login-by-cookie GUARD COOKIE-FIRST (INV-8): đủ cookie session → LOGGED_IN, không phụ thuộc DOM giòn.

Bối cảnh thật: YouTube/FB tiếng Việt/SPA khiến guard DOM báo COOKIE_DEAD giả dù cookie đăng nhập đã nạp đủ.
Giờ cookie_login kiểm cookie trước (như detector), DOM chỉ là fallback.
"""

from __future__ import annotations

from collections.abc import Iterable

from fastcheck_worker.contracts import Platform
from fastcheck_worker.login import Credential, LoginMethod, get_login_strategy
from fastcheck_worker.login.base import LoginOutcome


class _CookiePage:
    """Trang giả: KHÔNG có selector DOM guard, chỉ có cookie (mô phỏng SPA đa ngôn ngữ)."""

    def __init__(self, url: str, cookies: Iterable[str]) -> None:
        self._url = url
        self._cookies = set(cookies)

    @property
    def current_url(self) -> str:
        return self._url

    def goto(self, url: str) -> None:
        self._url = url

    def has_element(self, *selectors: str) -> bool:
        return False  # KHÔNG dựa DOM

    def cookie_names(self) -> set[str]:
        return self._cookies

    def cookies_string(self) -> str:
        return '[{"name":"x","value":"y"}]'

    def fill(self, selector: str, text: str) -> bool:
        return True

    def click(self, selector: str) -> bool:
        return True

    def wait_present(self, selector: str, timeout: float) -> bool:
        return True


def test_youtube_cookie_login_passes_by_cookie_not_dom() -> None:
    # Đủ LOGIN_INFO + SAPISID (có trên .youtube.com) → LOGGED_IN dù KHÔNG có DOM guard.
    page = _CookiePage("https://www.youtube.com/", {"LOGIN_INFO", "SAPISID", "SSID"})
    r = get_login_strategy(Platform.YOUTUBE, LoginMethod.COOKIE).login(
        page, Credential(method=LoginMethod.COOKIE, cookie="x")
    )
    assert r.outcome == LoginOutcome.LOGGED_IN


def test_youtube_cookie_login_dead_when_missing_core_cookie() -> None:
    # Thiếu LOGIN_INFO → cookie-guard không đủ → fallback DOM (không có) → COOKIE_DEAD (không đoán bừa).
    page = _CookiePage("https://www.youtube.com/", {"SAPISID"})
    r = get_login_strategy(Platform.YOUTUBE, LoginMethod.COOKIE).login(
        page, Credential(method=LoginMethod.COOKIE, cookie="x")
    )
    assert r.outcome == LoginOutcome.COOKIE_DEAD
