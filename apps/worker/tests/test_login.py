"""Login module (§7): cookie login (4 platform) + info login (TT/X) qua FakeLoginPage (không cần browser).

Bất biến kiểm: cookie chết → COOKIE_DEAD (không đoán logged-in); captcha → BLOCKED; OTP không secret →
OTP_REQUIRED (báo ra, INV-1); login-by-info FB/YT không hỗ trợ → LoginError; phiên OK → thu fresh_cookie.
"""

from __future__ import annotations

import pytest

from fastcheck_worker.contracts import Platform
from fastcheck_worker.login import (
    Credential,
    LoginError,
    LoginMethod,
    get_login_strategy,
)
from fastcheck_worker.login.base import LoginOutcome
from fastcheck_worker.login.forms import TIKTOK_LOGIN, TWITTER_LOGIN, YOUTUBE_LOGIN


class FakeLoginPage:
    """Máy trạng thái DOM giả: mỗi `click` vào selector trong `advance_on` sang state kế tiếp."""

    def __init__(
        self, states: list[set[str]], url: str = "https://example.test/", advance_on: tuple[str, ...] = ()
    ) -> None:
        self._states = states
        self._i = 0
        self._url = url
        self._advance_on = {s for s in advance_on if s}
        self.filled: dict[str, str] = {}

    @property
    def current_url(self) -> str:
        return self._url

    def goto(self, url: str) -> None:
        self._url = url

    def has_element(self, *selectors: str) -> bool:
        cur = self._states[self._i]
        return any(s in cur for s in selectors if s)

    def fill(self, selector: str, text: str) -> bool:
        self.filled[selector] = text
        return True

    def click(self, selector: str) -> bool:
        if selector in self._advance_on and self._i < len(self._states) - 1:
            self._i += 1
        return True

    def press_enter(self, selector: str) -> bool:  # noqa: ARG002 — Enter = submit bước → sang state kế
        if self._i < len(self._states) - 1:
            self._i += 1
        return True

    def click_text(self, text: str) -> bool:  # noqa: ARG002 — nút "Continue with Google"/"Use password" coi như bấm được
        return True

    def use_latest_tab(self) -> bool:
        return False

    def use_main_tab(self) -> None:
        return None

    def wait_present(self, selector: str, timeout: float) -> bool:
        return True

    def cookies_string(self) -> str:
        return '[{"name":"sessionid","value":"fresh"}]'


# ── login-by-cookie ────────────────────────────────────────────────────────
def test_cookie_login_logged_in_returns_fresh_cookie() -> None:
    page = FakeLoginPage(states=[{TIKTOK_LOGIN.verify_selectors[0]}], url="https://www.tiktok.com/")
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.COOKIE).login(
        page, Credential(method=LoginMethod.COOKIE, cookie="x")
    )
    assert result.outcome == LoginOutcome.LOGGED_IN
    assert result.fresh_cookie  # thu cookie mới để refresh (spec §4.4)


def test_cookie_login_dead_cookie_is_challenged_not_logged_in() -> None:
    # Không thấy guard → COOKIE_DEAD (KHÔNG đoán đã đăng nhập — INV-2).
    page = FakeLoginPage(states=[set()], url="https://www.tiktok.com/")
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.COOKIE).login(
        page, Credential(method=LoginMethod.COOKIE, cookie="dead")
    )
    assert result.outcome == LoginOutcome.COOKIE_DEAD


def test_cookie_login_block_is_blocked_not_dead() -> None:
    page = FakeLoginPage(states=[{TIKTOK_LOGIN.block_selectors[0]}], url="https://www.tiktok.com/")
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.COOKIE).login(
        page, Credential(method=LoginMethod.COOKIE, cookie="x")
    )
    assert result.outcome == LoginOutcome.BLOCKED


@pytest.mark.parametrize(
    "platform", [Platform.TIKTOK, Platform.FACEBOOK, Platform.TWITTER, Platform.YOUTUBE]
)
def test_cookie_login_supported_for_all_four_platforms(platform: Platform) -> None:
    # Login-by-cookie phải có cho CẢ 4 (yêu cầu tối thiểu Excel).
    strategy = get_login_strategy(platform, LoginMethod.COOKIE)
    assert strategy is not None


# ── login-by-info (TikTok & X) ───────────────────────────────────────────────
def test_info_login_tiktok_happy_path() -> None:
    page = FakeLoginPage(
        states=[set(), {TIKTOK_LOGIN.verify_selectors[0]}],
        advance_on=(TIKTOK_LOGIN.submit_selector,),
    )
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="u", password="p")
    )
    assert result.outcome == LoginOutcome.LOGGED_IN
    assert page.filled[TIKTOK_LOGIN.username_selector] == "u"
    assert page.filled[TIKTOK_LOGIN.password_selector] == "p"


def test_info_login_captcha_is_blocked() -> None:
    page = FakeLoginPage(
        states=[set(), {TIKTOK_LOGIN.block_selectors[0]}],
        advance_on=(TIKTOK_LOGIN.submit_selector,),
    )
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="u", password="p")
    )
    assert result.outcome == LoginOutcome.BLOCKED


def test_info_login_otp_without_secret_requires_otp() -> None:
    page = FakeLoginPage(
        states=[set(), {TIKTOK_LOGIN.otp_selectors[0]}],
        advance_on=(TIKTOK_LOGIN.submit_selector,),
    )
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="u", password="p")
    )
    assert result.outcome == LoginOutcome.OTP_REQUIRED


def test_info_login_otp_with_secret_logs_in() -> None:
    # state0 (form) → submit → state1 (OTP) → handle_otp submit → state2 (verify).
    page = FakeLoginPage(
        states=[set(), {TIKTOK_LOGIN.otp_selectors[0]}, {TIKTOK_LOGIN.verify_selectors[0]}],
        advance_on=(TIKTOK_LOGIN.submit_selector,),
    )
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.INFO).login(
        page,
        Credential(method=LoginMethod.INFO, username="u", password="p", otp_secret="JBSWY3DPEHPK3PXP"),
    )
    assert result.outcome == LoginOutcome.LOGGED_IN


@pytest.mark.parametrize("platform", [Platform.TWITTER, Platform.YOUTUBE])
def test_info_login_via_google_logs_in(platform: Platform) -> None:
    # X & YouTube: method INFO → đăng nhập QUA GOOGLE (email → enter → password → enter → verify guard).
    spec = {Platform.TWITTER: TWITTER_LOGIN, Platform.YOUTUBE: YOUTUBE_LOGIN}[platform]
    page = FakeLoginPage(
        states=[set(), set(), {spec.verify_selectors[0]}], url="https://accounts.google.com/"
    )
    result = get_login_strategy(platform, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="me@gmail.com", password="p")
    )
    assert result.outcome == LoginOutcome.LOGGED_IN


def test_info_login_google_blocked_when_no_password_field() -> None:
    # Google chặn browser tự động → sau email KHÔNG hiện ô mật khẩu → BLOCKED (báo rõ, không đoán — INV-1).
    class _NoPasswordPage(FakeLoginPage):
        def wait_present(self, selector: str, timeout: float) -> bool:  # noqa: ARG002
            return False  # ô mật khẩu Google không bao giờ hiện

    page = _NoPasswordPage(states=[set()], url="https://accounts.google.com/")
    result = get_login_strategy(Platform.TWITTER, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="me@gmail.com", password="p")
    )
    assert result.outcome == LoginOutcome.BLOCKED
    assert result.detail == "google_blocked_or_verify"


def test_info_login_tiktok_form_error_when_field_missing() -> None:
    # TikTok (login gốc): ô không khớp → fill False → FORM_ERROR + bước hỏng rõ (không COOKIE_DEAD oan).
    class _NoFieldPage(FakeLoginPage):
        def fill(self, selector: str, text: str) -> bool:  # noqa: ARG002
            return False

    page = _NoFieldPage(states=[set()], url="https://www.tiktok.com/login")
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="u", password="p")
    )
    assert result.outcome == LoginOutcome.FORM_ERROR
    assert result.detail == "username_field_not_found"


def test_info_login_not_supported_for_facebook() -> None:
    # Facebook chỉ login-by-cookie → yêu cầu info → LoginError (báo ra, không đoán).
    with pytest.raises(LoginError):
        get_login_strategy(Platform.FACEBOOK, LoginMethod.INFO)
