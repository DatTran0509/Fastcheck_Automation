"""Login-by-info cho TikTok & X (spec §4.4): gõ mô phỏng người, xử lý captcha/OTP.

Chỉ chạy khi cookie chết mà profile có credential info. Gõ "mô phỏng người" (delay/nhiễu từng ký tự) do
`LoginPage.fill` lo — tách khỏi flow để test được. Bắt captcha/OTP như tín hiệu RÕ RÀNG, không đoán:
  - captcha/challenge  → BLOCKED (cần đổi profile / can thiệp).
  - cần OTP + có otp_secret → tự sinh TOTP điền tiếp; không có secret → OTP_REQUIRED (cần người).
  - sai user/pass       → BAD_CREDENTIAL.
Không log cookie/credential (INV-12). Đăng nhập OK → thu cookie mới để refresh session (spec §4.4).
"""

from __future__ import annotations

import base64
import hmac
import logging
import struct
import time

from .base import Credential, LoginError, LoginMethod, LoginOutcome, LoginPage, LoginResult
from .forms import LoginFormSpec

logger = logging.getLogger("fastcheck.worker.login")

# Chờ tối đa cho mỗi bước (giây) — tổng vẫn < timeout job (INV-9). Chờ selector, không sleep mù.
_STEP_TIMEOUT = 15.0


def _totp(secret_b32: str) -> str:
    """Sinh mã TOTP 6 số (RFC 6238, bước 30s, SHA1) từ TOTP secret base32 (2FA)."""
    padded = secret_b32.strip().upper().replace(" ", "")
    padded += "=" * ((8 - len(padded) % 8) % 8)
    key = base64.b32decode(padded)
    counter = int(time.time()) // 30
    digest = hmac.new(key, struct.pack(">Q", counter), "sha1").digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


class InfoLogin:
    """Đăng nhập bằng username/password. Chỉ TikTok & X đăng ký chiến lược này (registry)."""

    def __init__(self, spec: LoginFormSpec) -> None:
        if not spec.supports_info:
            raise LoginError("LoginFormSpec này không cấu hình login-by-info")
        self._spec = spec

    def login(self, page: LoginPage, credential: Credential) -> LoginResult:
        if not credential.username or not credential.password:
            raise LoginError("login-by-info cần username + password")
        spec = self._spec
        page.goto(spec.login_url)

        page.fill(spec.username_selector, credential.username)  # gõ mô phỏng người (page.fill lo)
        # X: bước trung gian "Next" trước khi hiện ô mật khẩu.
        if spec.next_selector:
            page.click(spec.next_selector)
            page.wait_present(spec.password_selector, _STEP_TIMEOUT)
        page.fill(spec.password_selector, credential.password)
        page.click(spec.submit_selector)

        return self._resolve_after_submit(page, credential)

    def _resolve_after_submit(self, page: LoginPage, credential: Credential) -> LoginResult:
        spec = self._spec
        # Chờ MỘT trong các tín hiệu kết thúc xuất hiện (đăng nhập xong / captcha / OTP).
        page.wait_present(spec.verify_selectors[0] if spec.verify_selectors else "", _STEP_TIMEOUT)

        if spec.block_selectors and page.has_element(*spec.block_selectors):
            return LoginResult(LoginOutcome.BLOCKED, LoginMethod.INFO, detail="captcha_or_challenge")

        if spec.otp_selectors and page.has_element(*spec.otp_selectors):
            return self._handle_otp(page, credential)

        if page.has_element(*spec.verify_selectors):
            return LoginResult(LoginOutcome.LOGGED_IN, LoginMethod.INFO, fresh_cookie=page.cookies_string())

        if spec.error_selectors and page.has_element(*spec.error_selectors):
            return LoginResult(LoginOutcome.BAD_CREDENTIAL, LoginMethod.INFO, detail="login_error_shown")

        # Không xác nhận được đã đăng nhập → coi như session chưa lập (lỗi profile, KHÔNG kết luận target).
        return LoginResult(LoginOutcome.COOKIE_DEAD, LoginMethod.INFO, detail="login_not_confirmed")

    def _handle_otp(self, page: LoginPage, credential: Credential) -> LoginResult:
        if not credential.otp_secret:
            # Cần OTP mà không có secret → báo ra để người can thiệp, KHÔNG đoán (INV-1).
            return LoginResult(LoginOutcome.OTP_REQUIRED, LoginMethod.INFO, detail="otp_needed_no_secret")
        code = _totp(credential.otp_secret)
        page.fill(self._spec.otp_selectors[0], code)
        if self._spec.submit_selector:
            page.click(self._spec.submit_selector)
        page.wait_present(self._spec.verify_selectors[0] if self._spec.verify_selectors else "", _STEP_TIMEOUT)
        if page.has_element(*self._spec.verify_selectors):
            return LoginResult(LoginOutcome.LOGGED_IN, LoginMethod.INFO, fresh_cookie=page.cookies_string())
        return LoginResult(LoginOutcome.OTP_REQUIRED, LoginMethod.INFO, detail="otp_rejected")
