"""USERPASS X (native user/pass/2FA + fallback mã email Hotmail) qua FakeLoginPage (không cần browser).

Bất biến kiểm: happy-path TOTP → LOGGED_IN mà KHÔNG đụng Hotmail; màn 'mã email' (LoginAcid) → đọc mã Hotmail
rồi điền; không lấy được mã → OTP_REQUIRED (báo ra, không đoán — INV-1); arkose → BLOCKED (không DEAD); sai mật
khẩu → BAD_CREDENTIAL; thiếu password → LoginError. Reader Hotmail được inject (không mô phỏng Outlook ở đây).
"""

from __future__ import annotations

import pytest

from fastcheck_worker.login import Credential, LoginError, LoginMethod
from fastcheck_worker.login.base import LoginOutcome
from fastcheck_worker.login.forms import TWITTER_LOGIN
from fastcheck_worker.login.x_userpass_login import XUserPassLogin

_UNAME = TWITTER_LOGIN.username_selector
_PWD = TWITTER_LOGIN.password_selector
_OTP = TWITTER_LOGIN.otp_selectors[0]
_VERIFY = TWITTER_LOGIN.verify_selectors[0]
_ARKOSE = 'iframe[src*="arkoselabs"]'
_ENTRY = '[data-testid="loginButton"]'  # nút "Sign in" ở landing x.com
_TOTP_SECRET = "JBSWY3DPEHPK3PXP"


class _XFake:
    """Máy trạng thái màn hình X giả: mỗi màn = (bộ selector hiển thị, bộ text hiển thị). Enter/click sang màn
    kế (đổi URL để wait_url_change bắt) TRỪ khi màn nằm trong `sticky` (mô phỏng kẹt: sai mật khẩu / mã bị từ chối)."""

    def __init__(
        self,
        screens: list[tuple[set[str], set[str]]],
        *,
        sticky: frozenset[int] = frozenset(),
        start_url: str = "https://x.com",
    ) -> None:
        self._screens = screens
        self._i = 0
        self._url = start_url
        self._sticky = sticky
        self.filled: dict[str, str] = {}

    @property
    def current_url(self) -> str:
        return self._url

    def goto(self, url: str) -> None:
        self._url = url

    def has_element(self, *selectors: str) -> bool:
        cur = self._screens[self._i][0]
        return any(s in cur for s in selectors if s)

    def has_text(self, *needles: str) -> bool:
        texts = [t.lower() for t in self._screens[self._i][1]]
        return any(any(n.lower() in t for t in texts) for n in needles if n)

    def read_text(self, selector: str) -> str:  # noqa: ARG002 — reader Hotmail được inject riêng
        return ""

    def set_cookies(self, cookie: str, target_url: str) -> None:  # noqa: ARG002
        return None

    def open_new_tab(self, url: str) -> None:  # noqa: ARG002
        return None

    def close_current_tab(self) -> None:
        return None

    def cookie_names(self) -> set[str]:
        return set()

    def fill(self, selector: str, text: str) -> bool:
        if selector in self._screens[self._i][0]:
            self.filled[selector] = text
            return True
        return False

    def _advance(self) -> None:
        if self._i not in self._sticky and self._i < len(self._screens) - 1:
            self._i += 1
            self._url = f"{self._url}#{self._i}"

    def click(self, selector: str) -> bool:  # noqa: ARG002 — nút submit → sang màn kế
        self._advance()
        return True

    def press_enter(self, selector: str) -> bool:  # noqa: ARG002
        self._advance()
        return True

    def click_text(self, text: str) -> bool:  # noqa: ARG002
        self._advance()
        return True

    def wait_present(self, selector: str, timeout: float) -> bool:  # noqa: ARG002
        return True

    def wait_url_change(self, old_url: str, timeout: float) -> bool:  # noqa: ARG002
        return self._url != old_url

    def wait_url_contains(self, substring: str, timeout: float) -> bool:  # noqa: ARG002
        return substring in self._url

    def cookies_string(self) -> str:
        return '[{"name":"auth_token","value":"fresh"}]'


class _FakeReader:
    """Reader Hotmail giả: trả `code` cố định, đếm số lần gọi (để khẳng định có/không mở Outlook)."""

    def __init__(self, code: str | None) -> None:
        self.code = code
        self.calls = 0

    def read_login_code(self, page: object, credential: Credential) -> str | None:  # noqa: ARG002
        self.calls += 1
        return self.code


def _cred(**kw: object) -> Credential:
    base: dict[str, object] = {"method": LoginMethod.USERPASS, "username": "KevinX", "password": "pw"}
    base.update(kw)
    return Credential(**base)  # type: ignore[arg-type]


def test_userpass_requires_username_and_password() -> None:
    reader = _FakeReader("123456")
    with pytest.raises(LoginError):
        XUserPassLogin(TWITTER_LOGIN, reader).login(
            _XFake([({_UNAME}, set())]), Credential(method=LoginMethod.USERPASS, username="x")
        )


def test_userpass_landing_clicks_signin_then_enters_username() -> None:
    # Landing x.com CHỈ có nút "Sign in" (chưa hiện ô nhập) → bấm mở luồng → identifier → password → 2FA → home.
    reader = _FakeReader(None)
    page = _XFake(
        [
            ({_ENTRY}, set()),  # landing: chưa có ô nhập, chỉ nút Sign in
            ({_UNAME}, set()),
            ({_PWD}, set()),
            ({_OTP}, set()),
            ({_VERIFY}, set()),
        ]
    )
    result = XUserPassLogin(TWITTER_LOGIN, reader).login(page, _cred(otp_secret=_TOTP_SECRET))
    assert result.outcome == LoginOutcome.LOGGED_IN
    assert page.filled[_UNAME] == "KevinX"  # đã điền tài khoản sau khi mở luồng từ landing
    assert page.filled[_PWD] == "pw"


def test_userpass_enters_username_first_even_if_password_field_preloaded() -> None:
    # HỒI QUY bug thật: X preload ô password ẩn NGAY ở màn nhập tài khoản → _classify trả 'password' ở bước 0.
    # Gate ép thứ tự phải điền USERNAME trước (không điền mật khẩu vào ô tài khoản). state0 có CẢ hai ô.
    reader = _FakeReader(None)
    page = _XFake(
        [
            ({_UNAME, _PWD}, set()),  # màn nhập tài khoản nhưng DOM đã có sẵn ô password (ẩn) → dễ nhầm 'password'
            ({_PWD}, set()),  # trang mật khẩu thật
            ({_VERIFY}, set()),
        ]
    )
    result = XUserPassLogin(TWITTER_LOGIN, reader).login(page, _cred())  # không 2FA
    assert result.outcome == LoginOutcome.LOGGED_IN
    # Ô tài khoản nhận USERNAME (không phải mật khẩu); ô mật khẩu nhận PASSWORD.
    assert page.filled[_UNAME] == "KevinX"
    assert page.filled[_PWD] == "pw"


def test_userpass_happy_path_totp_logs_in_without_hotmail() -> None:
    # identifier → password → 2FA(TOTP) → home. Vượt 2FA KHÔNG gặp 'mã email' → KHÔNG mở Outlook (calls == 0).
    reader = _FakeReader("999999")
    page = _XFake(
        [
            ({_UNAME}, set()),
            ({_PWD}, set()),
            ({_OTP}, set()),  # text rỗng → mặc định 2FA authenticator (không phải mã email)
            ({_VERIFY}, set()),
        ]
    )
    result = XUserPassLogin(TWITTER_LOGIN, reader).login(page, _cred(otp_secret=_TOTP_SECRET))
    assert result.outcome == LoginOutcome.LOGGED_IN
    assert result.fresh_cookie
    assert page.filled[_PWD] == "pw"
    assert _OTP in page.filled and page.filled[_OTP].isdigit()  # ô OTP đã điền mã TOTP tự sinh (6 số)
    assert reader.calls == 0  # KHÔNG đụng Hotmail


def test_userpass_2fa_without_secret_is_otp_required() -> None:
    reader = _FakeReader(None)
    page = _XFake([({_UNAME}, set()), ({_PWD}, set()), ({_OTP}, set())])
    result = XUserPassLogin(TWITTER_LOGIN, reader).login(page, _cred())  # không otp_secret, không hotmail
    assert result.outcome == LoginOutcome.OTP_REQUIRED
    assert result.detail == "otp_needed_no_secret"


def test_userpass_email_code_reads_hotmail_then_logs_in() -> None:
    # Màn LoginAcid (text 'check your email') → mở Outlook lấy mã 6 số → điền → home.
    reader = _FakeReader("246813")
    page = _XFake(
        [
            ({_UNAME}, set()),
            ({_PWD}, set()),
            ({_OTP}, {"We sent you a code. Check your email."}),
            ({_VERIFY}, set()),
        ]
    )
    result = XUserPassLogin(TWITTER_LOGIN, reader).login(
        page, _cred(hotmail_email="a@hotmail.com", hotmail_password="hp")
    )
    assert result.outcome == LoginOutcome.LOGGED_IN
    assert reader.calls == 1
    assert page.filled[_OTP] == "246813"


def test_userpass_email_code_unavailable_is_otp_required() -> None:
    # X đòi mã email nhưng không lấy được (reader trả None) → OTP_REQUIRED (không đoán mã — INV-1).
    reader = _FakeReader(None)
    page = _XFake(
        [
            ({_UNAME}, set()),
            ({_PWD}, set()),
            ({_OTP}, {"check your email"}),
        ]
    )
    result = XUserPassLogin(TWITTER_LOGIN, reader).login(
        page, _cred(hotmail_email="a@hotmail.com", hotmail_password="hp")
    )
    assert result.outcome == LoginOutcome.OTP_REQUIRED
    assert result.detail == "email_code_unavailable"


def test_userpass_totp_rejected_escalates_to_hotmail_email() -> None:
    # Ô số nhưng TOTP không qua (màn kẹt) VÀ có hotmail → escalate sang nhánh mã email (LoginAcid không lộ text).
    reader = _FakeReader("112233")
    # Màn 2FA (index 2) sticky: điền TOTP không chuyển được → vòng sau escalate email; reader trả mã → sau đó cho qua.
    # Dùng một fake mở khoá sticky sau khi reader được gọi để mô phỏng mã email được chấp nhận.
    class _Escalate(_XFake):
        def __init__(self) -> None:
            super().__init__(
                [
                    ({_UNAME}, set()),
                    ({_PWD}, set()),
                    ({_OTP}, set()),
                    ({_VERIFY}, set()),
                ],
                sticky=frozenset({2}),
            )
            self._unlocked = False

        def fill(self, selector: str, text: str) -> bool:
            ok = super().fill(selector, text)
            # Sau khi điền mã email (reader đã chạy) → mở khoá để _advance qua home.
            if selector == _OTP and reader.calls >= 1:
                self._sticky = frozenset()
            return ok

    page = _Escalate()
    result = XUserPassLogin(TWITTER_LOGIN, reader).login(
        page, _cred(otp_secret=_TOTP_SECRET, hotmail_email="a@hotmail.com", hotmail_password="hp")
    )
    assert result.outcome == LoginOutcome.LOGGED_IN
    assert reader.calls == 1  # đã mở Outlook lấy mã email khi TOTP rớt


def test_userpass_arkose_is_blocked_not_dead() -> None:
    reader = _FakeReader(None)
    page = _XFake([({_UNAME}, set()), ({_ARKOSE}, set())])
    result = XUserPassLogin(TWITTER_LOGIN, reader).login(page, _cred(otp_secret=_TOTP_SECRET))
    assert result.outcome == LoginOutcome.BLOCKED
    assert result.detail == "captcha_or_challenge"


def test_userpass_wrong_password_is_bad_credential() -> None:
    # Màn mật khẩu kẹt (điền xong không rời màn) → sai mật khẩu / bị chặn → BAD_CREDENTIAL (không đoán — INV-1).
    reader = _FakeReader(None)
    page = _XFake([({_UNAME}, set()), ({_PWD}, set())], sticky=frozenset({1}))
    result = XUserPassLogin(TWITTER_LOGIN, reader).login(page, _cred(otp_secret=_TOTP_SECRET))
    assert result.outcome == LoginOutcome.BAD_CREDENTIAL
    assert result.detail == "password_not_accepted"
