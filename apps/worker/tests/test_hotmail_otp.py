"""HotmailOtpReader (USERPASS — lấy mã xác minh email của X từ Outlook) qua fake page (không cần browser).

Bất biến kiểm: không có creds → None (không lấy được, worker → OTP_REQUIRED); token vào thẳng hộp thư → đọc mã;
token fail → fallback email/mật khẩu → đọc mã; vào được nhưng không thấy mail → None (không đoán mã — INV-1);
LUÔN đóng tab Outlook (quay về tab X). Bóc mã: 6 số gần từ khoá, không bắt số bất kỳ.
"""

from __future__ import annotations

import pytest

from fastcheck_worker.login import hotmail_otp
from fastcheck_worker.login.base import Credential, LoginMethod
from fastcheck_worker.login.hotmail_otp import HotmailOtpReader


class _OutlookFake:
    """Outlook giả theo pha: pha 0 = ô email, pha 1 = ô mật khẩu, ≥2 = đã vào hộp thư. Token (nếu cấu hình)
    vào thẳng hộp thư sau khi inject + goto. `code_text` = text danh sách mail khi đã đăng nhập."""

    def __init__(self, *, token_logs_in: bool = False, password_logs_in: bool = False, code_text: str = "") -> None:
        self._token_logs_in = token_logs_in
        self._password_logs_in = password_logs_in
        self._code_text = code_text
        self._logged_in = False
        self._token_injected = False
        self._phase = 0
        self.tabs_opened = 0
        self.tabs_closed = 0
        self._url = "about:blank"

    def open_new_tab(self, url: str) -> None:
        self.tabs_opened += 1
        self._url = url

    def close_current_tab(self) -> None:
        self.tabs_closed += 1

    def set_cookies(self, cookie: str, target_url: str) -> None:  # noqa: ARG002
        self._token_injected = True

    def goto(self, url: str) -> None:
        self._url = url
        if self._token_injected and self._token_logs_in:
            self._logged_in = True

    def wait_present(self, selector: str, timeout: float) -> bool:  # noqa: ARG002
        if selector == hotmail_otp._INBOX_GUARD:
            return self._logged_in
        if selector == hotmail_otp._MS_EMAIL:
            return not self._logged_in and self._phase == 0
        if selector == hotmail_otp._MS_PASSWORD:
            return not self._logged_in and self._phase == 1
        return True

    def fill(self, selector: str, text: str) -> bool:  # noqa: ARG002
        return True

    def click(self, selector: str) -> bool:
        # Nút "Next"/"Sign in" (cùng id) → sang pha kế; sau pha mật khẩu (≥2) → đăng nhập (nếu cấu hình).
        if selector == hotmail_otp._MS_SUBMIT:
            self._phase += 1
            if self._phase >= 2 and self._password_logs_in:
                self._logged_in = True
        return True

    def click_text(self, text: str) -> bool:  # noqa: ARG002
        return True

    def read_text(self, selector: str) -> str:  # noqa: ARG002
        return self._code_text if self._logged_in else ""


def _cred(**kw: object) -> Credential:
    base: dict[str, object] = {"method": LoginMethod.USERPASS, "username": "u", "password": "p"}
    base.update(kw)
    return Credential(**base)  # type: ignore[arg-type]


def test_reader_no_creds_returns_none() -> None:
    page = _OutlookFake()
    assert HotmailOtpReader().read_login_code(page, _cred()) is None
    assert page.tabs_opened == 0  # không có gì để làm → không mở tab


def test_reader_token_path_reads_code_and_closes_tab() -> None:
    page = _OutlookFake(token_logs_in=True, code_text="Your X confirmation code is 481920. Enter it below.")
    code = HotmailOtpReader().read_login_code(page, _cred(hotmail_token="M.C550_x$$"))
    assert code == "481920"
    assert page.tabs_opened == 1 and page.tabs_closed == 1  # LUÔN đóng tab Outlook


def test_reader_token_fails_falls_back_to_password() -> None:
    page = _OutlookFake(
        token_logs_in=False, password_logs_in=True, code_text="123456 is your X verification code"
    )
    code = HotmailOtpReader().read_login_code(
        page, _cred(hotmail_token="dead", hotmail_email="a@hotmail.com", hotmail_password="hp")
    )
    assert code == "123456"


def test_reader_password_path_reads_code() -> None:
    page = _OutlookFake(password_logs_in=True, code_text="Confirmation code: 778899")
    code = HotmailOtpReader().read_login_code(page, _cred(hotmail_email="a@hotmail.com", hotmail_password="hp"))
    assert code == "778899"


def test_reader_logged_in_but_no_code_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # Vào được hộp thư nhưng không có mail mã X (poll hết giờ) → None (không đoán — INV-1). Poll timeout = 0 để nhanh.
    monkeypatch.setattr(hotmail_otp, "_CODE_POLL_TIMEOUT", 0.0)
    page = _OutlookFake(token_logs_in=True, code_text="")
    code = HotmailOtpReader().read_login_code(page, _cred(hotmail_token="M.C_x$$"))
    assert code is None
    assert page.tabs_closed == 1


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Your X confirmation code is 481920.", "481920"),
        ("123456 is your verification code", "123456"),
        ("single-use code 246813 to sign in", "246813"),
        ("Meeting at 123456 people online right now", None),  # số bất kỳ, không ngữ cảnh mã → không bắt
        ("no digits here", None),
        ("", None),
    ],
)
def test_extract_code(text: str, expected: str | None) -> None:
    assert HotmailOtpReader._extract_code(text) == expected
