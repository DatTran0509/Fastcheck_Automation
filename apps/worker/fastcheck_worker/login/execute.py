"""Thực thi một lệnh `login.run` (§7): Server GỌI, Client chạy kịch bản login trên browser GemLogin.

Real mode: mở browser GemLogin qua adapter → attach DrissionPage → chạy strategy (cookie ×4 / info TT&X) →
ĐÓNG browser (INV-9). Fake mode: chạy CHÍNH strategy đó trên `_FakeLoginPage` tất định (chứng minh đường
lệnh mà không cần GemLogin — logic login đã test ở test_login.py). KHÔNG log cookie/credential (INV-12).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..contracts import Platform
from . import get_login_strategy
from .base import Credential, LoginMethod, LoginResult, LoginStrategy
from .forms import (
    FACEBOOK_LOGIN,
    LoginFormSpec,
    TIKTOK_LOGIN,
    TWITTER_LOGIN,
    YOUTUBE_LOGIN,
)

if TYPE_CHECKING:
    from ..browser.adapter import GemLoginAdapter

logger = logging.getLogger("fastcheck.worker.login")

_FORMS: dict[Platform, LoginFormSpec] = {
    Platform.TIKTOK: TIKTOK_LOGIN,
    Platform.FACEBOOK: FACEBOOK_LOGIN,
    Platform.TWITTER: TWITTER_LOGIN,
    Platform.YOUTUBE: YOUTUBE_LOGIN,
}


def execute_login(
    *,
    adapter: GemLoginAdapter,
    gemlogin_mode: str,
    platform: Platform,
    method: str,
    gemlogin_profile_id: str,
    cookie: str | None,
    username: str | None,
    password: str | None,
    otp_secret: str | None,
) -> LoginResult:
    """Chạy kịch bản login → LoginResult. Ném LoginError nếu (platform, method) không hỗ trợ (fail loud)."""
    login_method = LoginMethod(method)
    credential = Credential(
        method=login_method,
        cookie=cookie or "",
        username=username,
        password=password,
        otp_secret=otp_secret,
    )
    strategy = get_login_strategy(platform, login_method)  # FB/YT + INFO → LoginError (đúng phạm vi)
    spec = _FORMS[platform]

    if gemlogin_mode == "real":
        return _run_real(adapter, gemlogin_profile_id, spec, strategy, credential)
    return strategy.login(_FakeLoginPage.for_credential(spec, credential), credential)


def _run_real(
    adapter: GemLoginAdapter,
    gemlogin_profile_id: str,
    spec: LoginFormSpec,
    strategy: LoginStrategy,
    credential: Credential,
) -> LoginResult:
    from DrissionPage import ChromiumOptions, ChromiumPage  # noqa: PLC0415 — chỉ real mode

    from .drission_page import DrissionLoginPage

    handle = adapter.open_browser(gemlogin_profile_id, credential.cookie)
    try:
        page = ChromiumPage(ChromiumOptions().set_address(handle.cdp_address))
        login_page = DrissionLoginPage(page)
        # login-by-cookie: nạp cookie TRƯỚC khi strategy điều hướng (INV-2).
        if credential.method == LoginMethod.COOKIE and credential.cookie:
            login_page.set_cookies(credential.cookie, spec.home_url)
        return strategy.login(login_page, credential)
    finally:
        # Đóng browser sau login (INV-9) — session đã lưu ở profile GemLogin, không cần giữ mở.
        try:
            adapter.close_browser(gemlogin_profile_id)
        except Exception as exc:  # noqa: BLE001 — không nuốt: log, không chặn trả kết quả login
            logger.warning("đóng browser sau login lỗi (%s)", type(exc).__name__)


class _FakeLoginPage:
    """Trang login GIẢ tất định cho fake mode: máy trạng thái DOM, `click(submit)` sang state kế tiếp.

    Seed theo credential để chạy đúng NHÁNH của strategy thật (chứng minh đường lệnh login.run end-to-end).
    """

    def __init__(self, states: list[set[str]], url: str, advance_on: tuple[str, ...]) -> None:
        self._states = states
        self._i = 0
        self._url = url
        self._advance_on = {s for s in advance_on if s}

    @classmethod
    def for_credential(cls, spec: LoginFormSpec, credential: Credential) -> _FakeLoginPage:
        verify = spec.verify_selectors[0] if spec.verify_selectors else "verify"
        if credential.method == LoginMethod.COOKIE:
            # cookie có → guard pass (logged in); cookie rỗng → không thấy guard (COOKIE_DEAD).
            states = [{verify}] if credential.cookie else [set()]
            return cls(states, spec.home_url, ())
        # INFO: state đầu có ô user/pass/next để fill/click; sau submit → thấy guard (LOGGED_IN).
        first = {spec.username_selector, spec.password_selector, spec.next_selector} - {""}
        return cls([first, {verify}], spec.home_url, (spec.submit_selector,))

    @property
    def current_url(self) -> str:
        return self._url

    def goto(self, url: str) -> None:
        self._url = url

    def set_cookies(self, cookie: str, target_url: str) -> None:  # noqa: ARG002 — no-op ở fake
        return None

    def has_element(self, *selectors: str) -> bool:
        cur = self._states[self._i]
        return any(s in cur for s in selectors if s)

    def cookie_names(self) -> set[str]:
        # Fake: không mô phỏng cookie → rỗng → cookie_login dùng fallback DOM (state đã seed sẵn guard).
        return set()

    def fill(self, selector: str, text: str) -> bool:  # noqa: ARG002 — không lưu text (INV-12)
        return selector in self._states[self._i]

    def click(self, selector: str) -> bool:
        if selector in self._advance_on and self._i < len(self._states) - 1:
            self._i += 1
        return True

    def wait_present(self, selector: str, timeout: float) -> bool:  # noqa: ARG002
        return True

    def cookies_string(self) -> str:
        return '[{"name":"sessionid","value":"fresh-fake"}]'
