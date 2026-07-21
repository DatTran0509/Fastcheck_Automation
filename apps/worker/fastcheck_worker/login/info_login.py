"""Login-by-info cho X (spec §4.4): PASSWORDLESS thích ứng — email → @username → OTP (2FA).

Luồng người dùng mong muốn (X đời mới, tài khoản bật 2FA):
  1. Điền TÀI KHOẢN (email) → Next.
  2. "Confirm your account": X hỏi @username để xác minh (chống bot) → điền confirm_username → Next.
  3. Xác thực bằng MÃ OTP (tự sinh TOTP từ otp_secret) → Enter. KHÔNG cần mật khẩu.

THÍCH ỨNG: sau bước 2, code tự nhìn màn X hiện ra thay vì ép một luồng cứng —
  - hiện ô OTP        → điền TOTP (luồng passwordless mặc định); không có otp_secret → OTP_REQUIRED (cần người).
  - hiện ô MẬT KHẨU   → chỉ điền nếu credential CÓ password (fallback khi X vẫn bắt mk); không có → báo RÕ.
  - captcha/challenge → BLOCKED (đổi profile / can thiệp).
  - kẹt ở "Confirm your account" → BLOCKED (identity_confirmation_required).
Gõ "mô phỏng người" (delay/nhiễu từng ký tự) do `LoginPage.fill` lo — tách khỏi flow để test được. Bắt mọi
tín hiệu RÕ RÀNG, KHÔNG đoán (INV-1). Không log cookie/credential (INV-12). Đăng nhập OK → thu cookie mới (§4.4).
"""

from __future__ import annotations

import logging

from .base import (
    Credential,
    LoginError,
    LoginMethod,
    LoginOutcome,
    LoginPage,
    LoginResult,
    generate_totp,
)
from .forms import LoginFormSpec

logger = logging.getLogger("fastcheck.worker.login")

# Chờ tối đa cho mỗi bước (giây) — tổng vẫn < timeout job (INV-9). Chờ selector, không sleep mù.
_STEP_TIMEOUT = 15.0
# Bước "Confirm your account" là TÙY CHỌN (X không phải lúc nào cũng hỏi) → chờ ngắn để không kéo dài mọi
# lần login khi X sang thẳng mật khẩu.
_CONFIRM_TIMEOUT = 5.0
# Chờ URL đổi sau khi Enter/click để XÁC MINH đã chuyển bước (X SPA đổi hash '#/s/...'). Poll trả sớm khi đổi;
# hết giờ = bước chưa chuyển (vd Enter không submit) → thử cách khác. Ngắn để không phí thời gian khi Enter fail.
_ADVANCE_TIMEOUT = 4.0


class InfoLogin:
    """Đăng nhập X passwordless (email → @username → OTP) trực tiếp trên x.com. Chỉ X đăng ký chiến lược này
    (registry) — TikTok đăng nhập qua tài khoản Google (xem `google_login.py`)."""

    def __init__(self, spec: LoginFormSpec) -> None:
        if not spec.supports_info:
            raise LoginError("LoginFormSpec này không cấu hình login-by-info")
        self._spec = spec

    def login(self, page: LoginPage, credential: Credential) -> LoginResult:
        if not credential.username:
            raise LoginError("login-by-info cần username (tài khoản đăng nhập — email)")
        # Passwordless: xác thực bằng mã 2FA (otp_secret). Cho phép password như FALLBACK nếu X vẫn bắt mật
        # khẩu. Không có CẢ HAI → không thể đăng nhập tự động (báo ra, không đoán — INV-1).
        if not credential.otp_secret and not credential.password:
            raise LoginError("login-by-info X cần otp_secret (mã 2FA) hoặc password")
        spec = self._spec
        # Marker phiên bản để xác nhận đang chạy code MỚI (nếu vẫn thấy log cũ → chưa restart worker).
        logger.info("info-login v4 (X passwordless: email → @username → OTP): goto %s", spec.login_url)
        page.goto(spec.login_url)

        # Mỗi bước KIỂM tra kết quả — KHÔNG nuốt (INV-1): không tìm thấy ô/nút = lỗi tự động hoá → báo RÕ bước hỏng.
        if not page.fill(spec.username_selector, credential.username):  # gõ mô phỏng người (page.fill lo)
            return self._form_error("username_field_not_found")

        # Email → Next. _advance CLICK nút "Next" (X đời mới không submit bằng Enter; Enter chỉ là fallback cuối).
        if not self._advance(page, spec.next_selector, spec.next_texts, spec.username_selector):
            return self._form_error("next_button_not_found")

        # "Confirm your account": X hỏi @username để xác minh (chống bot) — điền confirm_username (best-effort;
        # nếu X không hỏi thì bỏ qua). KHÔNG bấm "Use password" (luồng mặc định là OTP, không phải mật khẩu).
        self._handle_confirm_account(page, credential)

        # THÍCH ỨNG: X hiện ô MẬT KHẨU (fallback) hay đi thẳng OTP? Chỉ điền mật khẩu khi X hiện ô mật khẩu VÀ
        # credential có password. Không có ô mật khẩu → passwordless, sang thẳng OTP ở _resolve_after_submit.
        self._maybe_fill_password(page, credential)
        return self._resolve_after_submit(page, credential)

    def _handle_confirm_account(self, page: LoginPage, credential: Credential) -> None:
        """Điền @username ở bước "Confirm your account" của X (hỏi @handle để chống bot) khi nó xuất hiện.

        Best-effort, KHÔNG ép: X không phải lúc nào cũng hỏi (không hiện → bỏ qua, không phải lỗi). Có ô mà
        thiếu confirm_username → không điền được; nếu vì thế mà kẹt, `_resolve_after_submit` báo RÕ
        (identity_confirmation_required) — không đoán (INV-1)."""
        spec = self._spec
        if not spec.confirm_username_selector:
            return
        # Chờ ngắn cho màn "Confirm your account" render. Không hiện → X sang thẳng OTP/mật khẩu (không phải lỗi).
        if not page.wait_present(spec.confirm_username_selector, _CONFIRM_TIMEOUT):
            return
        if not credential.confirm_username:
            logger.info(
                "info-login: X hỏi 'Confirm your account' nhưng thiếu confirm_username — có thể kẹt (xem resolve)"
            )
            return
        if page.fill(spec.confirm_username_selector, credential.confirm_username):
            self._advance(page, spec.next_selector, spec.next_texts, spec.confirm_username_selector)
            logger.info("info-login: đã điền @username xác nhận ('Confirm your account')")

    def _maybe_fill_password(self, page: LoginPage, credential: Credential) -> None:
        """Điền mật khẩu CHỈ KHI X hiện ô mật khẩu (fallback — luồng mặc định là passwordless/OTP).

        Không hiện ô mật khẩu → X đi thẳng OTP (đúng luồng người dùng) → bỏ qua. Có ô nhưng credential KHÔNG có
        password → bỏ qua (không đoán); `_resolve_after_submit` sẽ báo RÕ nếu vì thế mà không vào được (INV-1)."""
        spec = self._spec
        if not page.wait_present(spec.password_selector, _CONFIRM_TIMEOUT):
            return  # passwordless: X không hỏi mật khẩu → sang OTP
        if not credential.password:
            logger.info("info-login: X hiện ô mật khẩu nhưng credential không có password (luồng passwordless)")
            return
        if page.fill(spec.password_selector, credential.password):
            self._advance(page, spec.submit_selector, spec.submit_texts, spec.password_selector)

    def _advance(
        self, page: LoginPage, selector: str, texts: tuple[str, ...], enter_field: str
    ) -> bool:
        """Chuyển bước, có XÁC MINH đã sang bước mới bằng URL đổi (X là SPA hash-routing '#/s/...'; X giữ input
        cũ trong DOM nên 'ô còn/mất' KHÔNG đáng tin để biết đã chuyển bước).

        Thứ tự (đúng ý user — Enter trước): NHẤN ENTER trong ô → nếu URL không đổi (vd màn 'Confirm your account'
        của X KHÔNG submit bằng Enter) → click nút submit (selector testid → text CHÍNH XÁC, né 'Continue with
        Google/phone/Apple'). Trả True khi URL đã đổi (đã sang bước mới)."""
        before = page.current_url
        page.press_enter(enter_field)
        if page.wait_url_change(before, _ADVANCE_TIMEOUT):
            return True
        # Enter không forward → click nút submit thật (X bắt click ở màn 'Confirm your account').
        if selector and page.click(selector) and page.wait_url_change(before, _ADVANCE_TIMEOUT):
            return True
        for t in texts:
            if page.click_text(t) and page.wait_url_change(before, _ADVANCE_TIMEOUT):
                return True
        return page.current_url != before

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

        # OTP TRƯỚC block: X dùng CHUNG testid OCF (`ocfEnterTextTextInput`) cho cả bước "confirm your
        # identity" (challenge — cũng nằm trong block_selectors của detector) LẪN bước nhập mã 2FA. otp_selectors
        # chỉ còn selector CÓ inputmode="numeric" (tín hiệu hẹp, đúng riêng cho OTP) nên ưu tiên hơn — nếu
        # check block trước, MỌI màn OTP thật sẽ bị coi nhầm là BLOCKED và không bao giờ tới được _handle_otp.
        if spec.otp_selectors and page.has_element(*spec.otp_selectors):
            return self._handle_otp(page, credential)

        if spec.block_selectors and page.has_element(*spec.block_selectors):
            return LoginResult(LoginOutcome.BLOCKED, LoginMethod.INFO, detail="captcha_or_challenge")

        if page.has_element(*spec.verify_selectors):
            return LoginResult(LoginOutcome.LOGGED_IN, LoginMethod.INFO, fresh_cookie=page.cookies_string())

        # Vẫn kẹt ở "Confirm your account" (chưa qua xác minh danh tính) → báo RÕ, KHÔNG COOKIE_DEAD oan (INV-1).
        # Phân biệt: thiếu confirm_username (operator chưa nhập @username) vs. có nhập mà vẫn kẹt (sai @username/
        # selector đổi/challenge) — để operator biết ĐÚNG việc cần làm.
        if spec.confirm_username_selector and page.has_element(spec.confirm_username_selector):
            detail = (
                "confirm_username_required"  # cấp @username của X vào form
                if not credential.confirm_username
                else "identity_confirmation_required"  # @username sai / bị challenge → dùng cookie/can thiệp
            )
            return LoginResult(LoginOutcome.BLOCKED, LoginMethod.INFO, detail=detail)

        # Còn ô mật khẩu = X BẮT nhập mật khẩu nhưng luồng passwordless không cấp (credential thiếu password)
        # → báo RÕ để operator thêm password hoặc dùng cookie (INV-1). COOKIE_DEAD (session chưa lập), không đoán.
        if page.has_element(spec.password_selector):
            return LoginResult(
                LoginOutcome.COOKIE_DEAD, LoginMethod.INFO, detail="password_step_required"
            )

        if spec.error_selectors and page.has_element(*spec.error_selectors):
            return LoginResult(LoginOutcome.BAD_CREDENTIAL, LoginMethod.INFO, detail="login_error_shown")

        # Không xác nhận được đã đăng nhập → coi như session chưa lập (lỗi profile, KHÔNG kết luận target).
        return LoginResult(LoginOutcome.COOKIE_DEAD, LoginMethod.INFO, detail="login_not_confirmed")

    def _handle_otp(self, page: LoginPage, credential: Credential) -> LoginResult:
        spec = self._spec
        if not credential.otp_secret:
            # Cần OTP mà không có secret → báo ra để người can thiệp, KHÔNG đoán (INV-1).
            return LoginResult(LoginOutcome.OTP_REQUIRED, LoginMethod.INFO, detail="otp_needed_no_secret")
        code = generate_totp(credential.otp_secret)
        if not page.fill(spec.otp_selectors[0], code):
            return LoginResult(LoginOutcome.OTP_REQUIRED, LoginMethod.INFO, detail="otp_field_not_found")
        # Submit OTP qua _advance (Enter → nếu không forward thì click nút "Next"/"Continue"/"Log in" + xác minh
        # URL đổi) — nút màn OTP của X không có testid nên click theo text chính xác, giống bước confirm.
        self._advance(page, spec.submit_selector, spec.submit_texts, spec.otp_selectors[0])
        page.wait_present(spec.verify_selectors[0] if spec.verify_selectors else "", _STEP_TIMEOUT)
        if page.has_element(*spec.verify_selectors):
            return LoginResult(LoginOutcome.LOGGED_IN, LoginMethod.INFO, fresh_cookie=page.cookies_string())
        return LoginResult(LoginOutcome.OTP_REQUIRED, LoginMethod.INFO, detail="otp_rejected")
