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
        # Marker phiên bản để xác nhận đang chạy code MỚI (nếu vẫn thấy log cũ → chưa restart worker).
        logger.info("info-login v2 (luồng use-password): goto %s", spec.login_url)
        page.goto(spec.login_url)

        # Mỗi bước KIỂM tra kết quả — KHÔNG nuốt (INV-1): không tìm thấy ô/nút = lỗi tự động hoá → báo RÕ bước hỏng.
        if not page.fill(spec.username_selector, credential.username):  # gõ mô phỏng người (page.fill lo)
            return self._form_error("username_field_not_found")

        # X: THỨ TỰ ĐÚNG (người dùng chỉ) — nhập tk → Continue → "Use password" → mới hiện ô mật khẩu.
        # KHÔNG cố điền mật khẩu trước khi bấm "Use password" (đó là lỗi khiến kẹt ở "Confirm your account").
        if spec.next_selector or spec.next_texts:
            if not self._advance(page, spec.next_selector, spec.next_texts, spec.username_selector):
                return self._form_error("next_button_not_found")
            # X hiện "Confirm your account" → BẤM "Use password" để sang bước nhập mật khẩu.
            if spec.use_password_text and page.click_text(spec.use_password_text):
                logger.info("info-login: đã bấm 'Use password'")
            page.wait_present(spec.password_selector, _STEP_TIMEOUT)

        if not page.fill(spec.password_selector, credential.password):
            # Không vào được ô mật khẩu: X vẫn kẹt ở bước xác minh danh tính (challenge chống bot) — không "Use
            # password" được → BÁO RÕ (INV-1, không COOKIE_DEAD oan). Đường tin cậy khi bị challenge: cookie.
            if page.has_element(*spec.otp_selectors):
                logger.warning(
                    "info-login: X vẫn bắt xác minh danh tính (không vào được ô mật khẩu) — cần cookie sống hoặc can thiệp tay"
                )
                return LoginResult(
                    LoginOutcome.BLOCKED, LoginMethod.INFO, detail="identity_confirmation_required"
                )
            return self._form_error("password_field_not_found")
        # Submit (nút "Continue"/"Log in"/"Đăng nhập"): selector → text → Enter.
        if not self._advance(page, spec.submit_selector, spec.submit_texts, spec.password_selector):
            return self._form_error("submit_button_not_found")

        return self._resolve_after_submit(page, credential)

    def _advance(
        self, page: LoginPage, selector: str, texts: tuple[str, ...], enter_field: str
    ) -> bool:
        """Chuyển bước bằng ENTER trong `enter_field` TRƯỚC (X submit form bằng Enter — nhanh & chắc, khỏi tìm
        nút Continue/Login vốn đổi testid liên tục). Không Enter được (ô không có) → mới thử nút theo selector/text."""
        if page.press_enter(enter_field):
            return True
        if selector and page.click(selector):
            return True
        return any(page.click_text(t) for t in texts)

    def _form_error(self, step: str) -> LoginResult:
        """Không thao tác được form (selector không khớp). Báo RÕ bước hỏng — execute._run_real sẽ log DIAG
        cấu trúc form thật để cập nhật selector. KHÔNG phải COOKIE_DEAD (cookie không liên quan info-login)."""
        logger.warning(
            "info-login DỪNG ở '%s' (không khớp selector) — X/TikTok đổi DOM hoặc chặn bot; xem DIAG để sửa selector",
            step,
        )
        return LoginResult(LoginOutcome.FORM_ERROR, LoginMethod.INFO, detail=step)

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
