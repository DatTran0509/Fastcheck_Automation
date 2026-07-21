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

    def _advance_state(self) -> None:
        # Sang state kế + ĐỔI URL (mô phỏng X SPA đổi hash khi chuyển bước → wait_url_change bắt được).
        if self._i < len(self._states) - 1:
            self._i += 1
            self._url = f"{self._url}#{self._i}"

    def click(self, selector: str) -> bool:
        if selector in self._advance_on:
            self._advance_state()
        return True

    def press_enter(self, selector: str) -> bool:  # noqa: ARG002 — Enter = submit bước → sang state kế
        self._advance_state()
        return True

    def click_text(self, text: str) -> bool:  # noqa: ARG002 — nút "Continue with Google"/"Use password" coi như bấm được
        return True

    def use_latest_tab(self) -> bool:
        return False

    def use_main_tab(self) -> None:
        return None

    def wait_present(self, selector: str, timeout: float) -> bool:
        return True

    def wait_url_change(self, old_url: str, timeout: float) -> bool:
        return self._url != old_url

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


# ── login-by-info: X passwordless (email → @username → OTP) trên x.com ──────
def test_info_login_x_passwordless_otp_logs_in() -> None:
    # LUỒNG CHÍNH (user chỉ): email → Next → (confirm no-op) → màn OTP → tự gen TOTP → verify. KHÔNG password.
    # states: email → OTP → verify. _advance nhấn Enter → advance; advance_on cần cho click(submit) ở _handle_otp.
    page = FakeLoginPage(
        states=[set(), {TWITTER_LOGIN.otp_selectors[0]}, {TWITTER_LOGIN.verify_selectors[0]}],
        advance_on=(TWITTER_LOGIN.next_selector, TWITTER_LOGIN.submit_selector),
    )
    result = get_login_strategy(Platform.TWITTER, LoginMethod.INFO).login(
        page,
        Credential(method=LoginMethod.INFO, username="me@mail.com", otp_secret="JBSWY3DPEHPK3PXP"),
    )
    assert result.outcome == LoginOutcome.LOGGED_IN
    assert page.filled[TWITTER_LOGIN.username_selector] == "me@mail.com"
    # Passwordless: KHÔNG điền mật khẩu.
    assert TWITTER_LOGIN.password_selector not in page.filled


def test_info_login_x_requires_otp_secret_or_password() -> None:
    # Thiếu CẢ otp_secret LẪN password → không thể đăng nhập tự động → LoginError (báo ra, không đoán — INV-1).
    page = FakeLoginPage(states=[set()])
    with pytest.raises(LoginError):
        get_login_strategy(Platform.TWITTER, LoginMethod.INFO).login(
            page, Credential(method=LoginMethod.INFO, username="me@mail.com")
        )


def test_info_login_x_confirm_account_fills_username() -> None:
    # X hỏi "Confirm your account" (@username) sau khi nhập email → điền confirm_username → rồi OTP → verify.
    # states: email → confirm → OTP → verify (email & confirm advance qua Enter trong ô vừa điền).
    page = FakeLoginPage(
        states=[
            set(),
            set(),
            {TWITTER_LOGIN.otp_selectors[0]},
            {TWITTER_LOGIN.verify_selectors[0]},
        ],
        advance_on=(TWITTER_LOGIN.next_selector, TWITTER_LOGIN.submit_selector),
    )
    result = get_login_strategy(Platform.TWITTER, LoginMethod.INFO).login(
        page,
        Credential(
            method=LoginMethod.INFO,
            username="me@mail.com",
            otp_secret="JBSWY3DPEHPK3PXP",
            confirm_username="my_x_handle",
        ),
    )
    assert result.outcome == LoginOutcome.LOGGED_IN
    # @username xác nhận được điền vào ô "Confirm your account" (khác ô đầu = email đăng nhập).
    assert page.filled[TWITTER_LOGIN.confirm_username_selector] == "my_x_handle"
    assert page.filled[TWITTER_LOGIN.username_selector] == "me@mail.com"


def test_info_login_x_password_fallback_logs_in() -> None:
    # FALLBACK: X vẫn hiện ô mật khẩu → điền password (nếu có) → submit → verify.
    # states: email → password → verify.
    page = FakeLoginPage(
        states=[set(), {TWITTER_LOGIN.password_selector}, {TWITTER_LOGIN.verify_selectors[0]}],
        advance_on=(TWITTER_LOGIN.next_selector, TWITTER_LOGIN.submit_selector),
    )
    result = get_login_strategy(Platform.TWITTER, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="me@mail.com", password="p")
    )
    assert result.outcome == LoginOutcome.LOGGED_IN
    assert page.filled[TWITTER_LOGIN.password_selector] == "p"


def test_info_login_captcha_is_blocked() -> None:
    page = FakeLoginPage(
        states=[set(), {TWITTER_LOGIN.block_selectors[0]}],
        advance_on=(TWITTER_LOGIN.next_selector, TWITTER_LOGIN.submit_selector),
    )
    result = get_login_strategy(Platform.TWITTER, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="u", otp_secret="JBSWY3DPEHPK3PXP")
    )
    assert result.outcome == LoginOutcome.BLOCKED


def test_info_login_otp_without_secret_requires_otp() -> None:
    # Có password (fallback) nhưng màn kế là OTP mà KHÔNG có otp_secret → OTP_REQUIRED (cần người — INV-1).
    page = FakeLoginPage(
        states=[set(), {TWITTER_LOGIN.otp_selectors[0]}],
        advance_on=(TWITTER_LOGIN.next_selector, TWITTER_LOGIN.submit_selector),
    )
    result = get_login_strategy(Platform.TWITTER, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="u", password="p")
    )
    assert result.outcome == LoginOutcome.OTP_REQUIRED


def test_info_login_x_form_error_when_field_missing() -> None:
    # X: ô username không khớp → fill False → FORM_ERROR + bước hỏng rõ (không COOKIE_DEAD oan).
    class _NoFieldPage(FakeLoginPage):
        def fill(self, selector: str, text: str) -> bool:  # noqa: ARG002
            return False

    page = _NoFieldPage(states=[set()], url="https://x.com/i/jf/onboarding/web?mode=login")
    result = get_login_strategy(Platform.TWITTER, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="u", password="p")
    )
    assert result.outcome == LoginOutcome.FORM_ERROR
    assert result.detail == "username_field_not_found"


# ── login-by-info: TikTok & YouTube qua tài khoản Google (GoogleLogin) ──────
@pytest.mark.parametrize("platform", [Platform.TIKTOK, Platform.YOUTUBE])
def test_info_login_via_google_logs_in(platform: Platform) -> None:
    # TikTok & YouTube: method INFO → đăng nhập QUA GOOGLE (email → enter → password → enter → verify guard).
    spec = {Platform.TIKTOK: TIKTOK_LOGIN, Platform.YOUTUBE: YOUTUBE_LOGIN}[platform]
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
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="me@gmail.com", password="p")
    )
    assert result.outcome == LoginOutcome.BLOCKED
    assert result.detail == "google_blocked_or_verify"


def test_google_login_otp_without_secret_requires_otp() -> None:
    # Tài khoản Google bật 2FA nhưng không có otp_secret → OTP_REQUIRED (cần người, không đoán — INV-1).
    from fastcheck_worker.login.google_login import _GOOGLE_OTP

    page = FakeLoginPage(states=[set(), set(), {_GOOGLE_OTP}], url="https://accounts.google.com/")
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="me@gmail.com", password="p")
    )
    assert result.outcome == LoginOutcome.OTP_REQUIRED
    assert result.detail == "google_otp_needed_no_secret"


def test_google_login_otp_with_secret_logs_in() -> None:
    # Tài khoản Google bật 2FA + có otp_secret → tự sinh TOTP điền tiếp → LOGGED_IN.
    from fastcheck_worker.login.google_login import _GOOGLE_OTP

    page = FakeLoginPage(
        states=[set(), set(), {_GOOGLE_OTP}, {TIKTOK_LOGIN.verify_selectors[0]}],
        url="https://accounts.google.com/",
    )
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.INFO).login(
        page,
        Credential(
            method=LoginMethod.INFO, username="me@gmail.com", password="p", otp_secret="JBSWY3DPEHPK3PXP"
        ),
    )
    assert result.outcome == LoginOutcome.LOGGED_IN


def test_info_login_not_supported_for_facebook() -> None:
    # Facebook chỉ login-by-cookie → yêu cầu info → LoginError (báo ra, không đoán).
    with pytest.raises(LoginError):
        get_login_strategy(Platform.FACEBOOK, LoginMethod.INFO)
