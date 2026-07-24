"""Login module (§7): cookie login (4 platform) + info login (TT/X) qua FakeLoginPage (không cần browser).

Bất biến kiểm: cookie chết → COOKIE_DEAD (không đoán logged-in); captcha → BLOCKED; OTP không secret →
OTP_REQUIRED (báo ra, INV-1); login-by-info FB/YT không hỗ trợ → LoginError; phiên OK → thu fresh_cookie.
"""

from __future__ import annotations

import pytest

from fastcheck_worker.contracts import Platform
from fastcheck_worker.login import (
    Credential,
    InfoLogin,
    LoginError,
    LoginMethod,
    get_login_strategy,
)
from fastcheck_worker.login.base import LoginOutcome, LoginStrategy
from fastcheck_worker.login.forms import TIKTOK_LOGIN, TWITTER_LOGIN, YOUTUBE_LOGIN


def _x_native_info() -> LoginStrategy:
    # X GỐC (user/pass trên x.com) qua InfoLogin — KHÔNG còn nối tuyến trong get_login_strategy (X+INFO giờ
    # đi qua Google). Dựng thẳng để vẫn kiểm được logic InfoLogin (code vẫn giữ, có thể nối lại sau).
    return InfoLogin(TWITTER_LOGIN)


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

    def has_text(self, *needles: str) -> bool:  # noqa: ARG002 — flow cũ không dùng text
        return False

    def read_text(self, selector: str) -> str:  # noqa: ARG002
        return ""

    def set_cookies(self, cookie: str, target_url: str) -> None:  # noqa: ARG002 — no-op ở fake
        return None

    def open_new_tab(self, url: str) -> None:  # noqa: ARG002
        return None

    def close_current_tab(self) -> None:
        return None

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

    def click_text(self, text: str) -> bool:
        # Mô phỏng: bấm "Continue with Google" = redirect OAuth sang Google (để wait_url_contains bắt được);
        # các nút khác coi như bấm được.
        if "Continue with Google" in text:
            self._url = "https://accounts.google.com/v3/signin/identifier"
        return True

    def use_latest_tab(self) -> bool:
        return False

    def use_main_tab(self) -> None:
        return None

    def wait_present(self, selector: str, timeout: float) -> bool:
        return True

    def wait_url_change(self, old_url: str, timeout: float) -> bool:
        return self._url != old_url

    def wait_url_contains(self, substring: str, timeout: float) -> bool:  # noqa: ARG002
        return substring in self._url

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
    result = _x_native_info().login(
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
        _x_native_info().login(
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
    result = _x_native_info().login(
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
    result = _x_native_info().login(
        page, Credential(method=LoginMethod.INFO, username="me@mail.com", password="p")
    )
    assert result.outcome == LoginOutcome.LOGGED_IN
    assert page.filled[TWITTER_LOGIN.password_selector] == "p"


def test_info_login_captcha_is_blocked() -> None:
    page = FakeLoginPage(
        states=[set(), {TWITTER_LOGIN.block_selectors[0]}],
        advance_on=(TWITTER_LOGIN.next_selector, TWITTER_LOGIN.submit_selector),
    )
    result = _x_native_info().login(
        page, Credential(method=LoginMethod.INFO, username="u", otp_secret="JBSWY3DPEHPK3PXP")
    )
    assert result.outcome == LoginOutcome.BLOCKED


def test_info_login_otp_without_secret_requires_otp() -> None:
    # Có password (fallback) nhưng màn kế là OTP mà KHÔNG có otp_secret → OTP_REQUIRED (cần người — INV-1).
    page = FakeLoginPage(
        states=[set(), {TWITTER_LOGIN.otp_selectors[0]}],
        advance_on=(TWITTER_LOGIN.next_selector, TWITTER_LOGIN.submit_selector),
    )
    result = _x_native_info().login(
        page, Credential(method=LoginMethod.INFO, username="u", password="p")
    )
    assert result.outcome == LoginOutcome.OTP_REQUIRED


def test_info_login_x_form_error_when_field_missing() -> None:
    # X: ô username không khớp → fill False → FORM_ERROR + bước hỏng rõ (không COOKIE_DEAD oan).
    class _NoFieldPage(FakeLoginPage):
        def fill(self, selector: str, text: str) -> bool:  # noqa: ARG002
            return False

    page = _NoFieldPage(states=[set()], url="https://x.com")
    result = _x_native_info().login(
        page, Credential(method=LoginMethod.INFO, username="u", password="p")
    )
    assert result.outcome == LoginOutcome.FORM_ERROR
    assert result.detail == "username_field_not_found"


# ── login-by-info: TikTok & YouTube qua tài khoản Google (GoogleLogin) ──────
# Nút Next của Google: click(Next) trong _advance → chuyển bước + đổi URL để wait_url_change xác minh đã sang bước.
def _google_next() -> tuple[str, ...]:
    from fastcheck_worker.login.google_login import (
        _GOOGLE_EMAIL_NEXT,
        _GOOGLE_OTP_NEXT,
        _GOOGLE_PASSWORD_NEXT,
    )

    return (_GOOGLE_EMAIL_NEXT, _GOOGLE_PASSWORD_NEXT, _GOOGLE_OTP_NEXT)


@pytest.mark.parametrize("platform", [Platform.TIKTOK, Platform.TWITTER, Platform.YOUTUBE])
def test_info_login_via_google_logs_in(platform: Platform) -> None:
    # X, TikTok, YouTube: method INFO → QUA GOOGLE (email → Next → sang bước pwd → password → Next → verify guard).
    spec = {
        Platform.TIKTOK: TIKTOK_LOGIN,
        Platform.TWITTER: TWITTER_LOGIN,
        Platform.YOUTUBE: YOUTUBE_LOGIN,
    }[platform]
    page = FakeLoginPage(
        states=[set(), set(), {spec.verify_selectors[0]}],
        url="https://accounts.google.com/",
        advance_on=_google_next(),
    )
    result = get_login_strategy(platform, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="me@gmail.com", password="p")
    )
    assert result.outcome == LoginOutcome.LOGGED_IN


def test_google_login_2fa_chooser_selects_authenticator_then_logs_in() -> None:
    # 2FA có màn CHỌN phương thức (tài khoản X qua Google bật nhiều cách): sau mật khẩu CHƯA hiện ô mã → phải
    # chọn "Google Authenticator app" → mới hiện ô mã → tự sinh TOTP từ otp_secret → LOGGED_IN.
    from fastcheck_worker.login.google_login import _GOOGLE_OTP

    class _ChooserPage(FakeLoginPage):
        def click_text(self, text: str) -> bool:
            if "Google Authenticator" in text:
                self._advance_state()  # chọn phương thức Authenticator → hiện ô nhập mã TOTP
                return True
            return super().click_text(text)  # base: "Continue with Google" → mô phỏng redirect sang Google

    # states: email → password → CHỌN phương thức (chưa có ô mã) → ô mã TOTP → verify guard X.
    page = _ChooserPage(
        states=[set(), set(), set(), {_GOOGLE_OTP}, {TWITTER_LOGIN.verify_selectors[0]}],
        url="https://x.com/",
        advance_on=_google_next(),
    )
    result = get_login_strategy(Platform.TWITTER, LoginMethod.INFO).login(
        page,
        Credential(
            method=LoginMethod.INFO, username="me@gmail.com", password="p", otp_secret="JBSWY3DPEHPK3PXP"
        ),
    )
    assert result.outcome == LoginOutcome.LOGGED_IN


def test_info_login_google_email_step_stuck_is_blocked() -> None:
    # Bấm Next ở bước email nhưng KHÔNG chuyển bước (URL không đổi) = Google chặn/từ chối email → BLOCKED,
    # TUYỆT ĐỐI không gõ mật khẩu vào ô email (bug email+password dính liền). advance_on rỗng → click không đổi URL.
    page = FakeLoginPage(states=[set()], url="https://accounts.google.com/", advance_on=())
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="me@gmail.com", password="p")
    )
    assert result.outcome == LoginOutcome.BLOCKED
    assert result.detail == "google_email_step_stuck"


def test_info_login_google_blocked_when_no_password_field() -> None:
    # Đã sang bước pwd (URL đổi) nhưng ô mật khẩu KHÔNG hiện → Google bắt xác minh → BLOCKED (không đoán — INV-1).
    class _NoPasswordPage(FakeLoginPage):
        def wait_present(self, selector: str, timeout: float) -> bool:  # noqa: ARG002
            return False  # ô mật khẩu không bao giờ hiện (wait_url_change vẫn dùng bản gốc → email vẫn sang bước)

    page = _NoPasswordPage(states=[set(), set()], url="https://accounts.google.com/", advance_on=_google_next())
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="me@gmail.com", password="p")
    )
    assert result.outcome == LoginOutcome.BLOCKED
    assert result.detail == "google_blocked_or_verify"


def test_google_login_otp_without_secret_requires_otp() -> None:
    # Tài khoản Google bật 2FA nhưng không có otp_secret → OTP_REQUIRED (cần người, không đoán — INV-1).
    from fastcheck_worker.login.google_login import _GOOGLE_OTP

    page = FakeLoginPage(
        states=[set(), set(), {_GOOGLE_OTP}], url="https://accounts.google.com/", advance_on=_google_next()
    )
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
        advance_on=_google_next(),
    )
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.INFO).login(
        page,
        Credential(
            method=LoginMethod.INFO, username="me@gmail.com", password="p", otp_secret="JBSWY3DPEHPK3PXP"
        ),
    )
    assert result.outcome == LoginOutcome.LOGGED_IN


def test_google_login_wrong_password_is_bad_credential_not_logged_in() -> None:
    # SAI mật khẩu Google: ô mật khẩu VẪN còn + có thông báo lỗi sau khi submit → BAD_CREDENTIAL, TUYỆT ĐỐI
    # không LOGGED_IN (INV-1/INV-2). Nếu không chặn ở đây, cookie session cũ còn sót sẽ tạo LOGGED_IN giả.
    from fastcheck_worker.login.google_login import _GOOGLE_PASSWORD, _GOOGLE_PASSWORD_ERROR

    page = FakeLoginPage(
        states=[set(), {_GOOGLE_PASSWORD}, {_GOOGLE_PASSWORD, _GOOGLE_PASSWORD_ERROR[0]}],
        url="https://accounts.google.com/",
        advance_on=_google_next(),
    )
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="me@gmail.com", password="sai")
    )
    assert result.outcome == LoginOutcome.BAD_CREDENTIAL
    assert result.detail == "google_wrong_password"
    assert result.fresh_cookie is None  # KHÔNG chụp cookie chết làm "fresh"


def test_google_login_password_step_stuck_is_blocked() -> None:
    # Kẹt ở bước mật khẩu, không rời màn được, KHÔNG có tín hiệu lỗi rõ (Google chặn/bắt xác minh) → BLOCKED
    # (không LOGGED_IN, không DEAD — INV-1). Đường tin cậy vẫn là login-by-cookie.
    from fastcheck_worker.login.google_login import _GOOGLE_PASSWORD

    page = FakeLoginPage(
        states=[set(), {_GOOGLE_PASSWORD}, {_GOOGLE_PASSWORD}],
        url="https://accounts.google.com/",
        advance_on=_google_next(),
    )
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="me@gmail.com", password="p")
    )
    assert result.outcome == LoginOutcome.BLOCKED
    assert result.detail == "google_password_step_stuck"


def test_google_login_stale_cookie_without_guard_is_dead_not_logged_in() -> None:
    # HỒI QUY bug thật: qua bước mật khẩu nhưng OAuth KHÔNG lập được phiên (không thấy guard DOM), trong khi
    # profile GemLogin còn cookie `sessionid` CŨ. _verify KHÔNG được tin "tên cookie có mặt" → phải COOKIE_DEAD,
    # KHÔNG LOGGED_IN (nếu không, "báo login thành công nhưng nạp pool test guard fail").
    class _StaleCookiePage(FakeLoginPage):
        def cookie_names(self) -> set[str]:
            return {"sessionid"}  # cookie CŨ còn sót — KHÔNG chứng minh phiên còn sống server-side

    page = _StaleCookiePage(
        states=[set(), set(), set()], url="https://www.tiktok.com/", advance_on=_google_next()
    )
    result = get_login_strategy(Platform.TIKTOK, LoginMethod.INFO).login(
        page, Credential(method=LoginMethod.INFO, username="me@gmail.com", password="p")
    )
    assert result.outcome == LoginOutcome.COOKIE_DEAD
    assert result.detail == "google_verify_guard_failed"
    assert result.fresh_cookie is None


def test_info_login_not_supported_for_facebook() -> None:
    # Facebook chỉ login-by-cookie → yêu cầu info → LoginError (báo ra, không đoán).
    with pytest.raises(LoginError):
        get_login_strategy(Platform.FACEBOOK, LoginMethod.INFO)
