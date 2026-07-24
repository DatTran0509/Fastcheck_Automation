"""Login-by-USERPASS cho X (spec §4.4): username + password + 2FA(TOTP), fallback mã email qua Hotmail.

Khác `InfoLogin` (passwordless email→@username→OTP) và `GoogleLogin` (đăng nhập qua tài khoản Google): đây là
ĐĂNG NHẬP X NATIVE bằng username + password. X trả các bước ĐỘNG:
    identifier → [alternate identifier] → password → [AccountDuplicationCheck] → [2FA] → [LoginAcid mã email]
    → [Arkose] → home
và bước 'mã email' (LoginAcid) có thể chen vào BẤT KỲ vị trí nào tuỳ X đánh giá rủi ro. Vì thế KHÔNG chạy chuỗi
cứng mà chạy VÒNG LẶP MÁY TRẠNG THÁI: mỗi vòng NHẬN DIỆN màn hình đang hiện (đa tín hiệu: ô nhập + TEXT + testid
— INV-8) rồi phản ứng:
  - ô username/identifier          → điền username
  - 'nhập SĐT hoặc username'       → điền lại username (bước chống bot của X)
  - 'Confirm your account'         → điền confirm_username (@handle)
  - ô mật khẩu                     → điền password
  - ô số + text '2FA authenticator'→ tự sinh TOTP từ otp_secret
  - ô số + text 'gửi qua email'    → mở tab Outlook lấy mã 6 số (Hotmail) rồi điền (LoginAcid)
  - arkose/funcaptcha              → BLOCKED (không tự giải — báo ra, đổi profile/can thiệp)
  - guard đã đăng nhập             → LOGGED_IN (xác minh lại bằng _verify — INV-2)

Vượt được 2FA mà KHÔNG gặp màn 'mã email' → KHÔNG đụng tới Hotmail (đúng yêu cầu). Ô số của X dùng CHUNG testid
(`ocfEnterTextTextInput`) cho cả '2FA app' lẫn 'mã qua email' → chỉ TEXT tách được; nếu text mập mờ, mặc định
thử TOTP (rẻ, có sẵn secret), rớt thì mới thử nhánh email. Bắt mọi tín hiệu RÕ RÀNG; không khớp chắc chắn →
báo ra, KHÔNG đoán/không DEAD (INV-1). KHÔNG log cookie/credential/mã (INV-12). Đăng nhập OK → thu cookie mới.
"""

from __future__ import annotations

import enum
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
from .hotmail_otp import HotmailOtpReader

logger = logging.getLogger("fastcheck.worker.login")

_X_LOGIN_URL = "https://x.com"

# Ngân sách thời gian (giây) — GIỮ CHẶT (như InfoLogin) để tổng phiên nằm trong ngưỡng ack login orchestrator.
# Nhánh 'mã email' là ngoại lệ chậm nhất (mở tab + đọc mail) — xem hotmail_otp.py.
_STEP_TIMEOUT = 8.0  # chờ một màn hình render
_ADVANCE_TIMEOUT = 4.0  # chờ URL đổi sau khi submit bước (X SPA đổi hash '#/...')
_VERIFY_RENDER_TIMEOUT = 6.0
_MAX_STEPS = 8  # trần số bước (chống lặp vô hạn khi X kẹt/đổi DOM)

# Text tách màn 'mã 6 số gửi qua EMAIL' (LoginAcid) với màn '2FA authenticator' (dùng chung ô số OCF — INV-8).
_EMAIL_CODE_TEXTS = (
    "check your email",
    "we sent",
    "sent you a",
    "sent a code",
    "confirmation code",
    "code to your email",
    "enter it below to verify",
)
# 'unusual login activity' của X = bước hỏi lại SĐT/username (chống bot), KHÔNG phải chặn cứng → xử như ALT.
_ALT_IDENTIFIER_TEXTS = (
    "phone number or username",
    "phone or username",
    "unusual login activity",
    "enter your phone",
)
_CONFIRM_TEXTS = ("confirm your account", "enter your username")
# Landing x.com (chưa hiện ô nhập tài khoản) — nút "Sign in" mở luồng đăng nhập. testid `loginButton` ổn định.
_LOGIN_ENTRY_SELECTOR = '[data-testid="loginButton"]'
_LOGIN_ENTRY_TEXTS = ("Sign in", "Log in", "Đăng nhập")
# Chặn CỨNG (arkose/captcha/authorize) — KHÔNG gồm ô OCF (dùng chung cho OTP) để mọi màn OTP không thành BLOCKED.
_ARKOSE_SELECTORS = ('iframe[src*="arkoselabs"]', 'iframe[src*="funcaptcha"]', "#arkose")
_ARKOSE_TEXTS = (
    "verify you're human",
    "solve this puzzle",
    "authorize access to your account",
)
_BAD_CRED_TEXTS = (
    "wrong password",
    "the password you entered was incorrect",
    "incorrect password",
    "please try again",
)


class _Screen(enum.Enum):
    HOME = "home"
    BLOCK = "block"
    BAD_CRED = "bad_cred"
    ENTRY = "entry"  # landing x.com — bấm "Sign in" mở luồng đăng nhập (chưa hiện ô nhập tài khoản)
    IDENTIFIER = "identifier"
    ALT_IDENTIFIER = "alt_identifier"
    CONFIRM = "confirm"
    PASSWORD = "password"
    TWO_FACTOR = "two_factor"
    EMAIL_CODE = "email_code"
    UNKNOWN = "unknown"


class XUserPassLogin:
    """Đăng nhập X native (user/pass/2FA + fallback mã email Hotmail) bằng máy trạng thái nhận-diện-màn-hình."""

    def __init__(self, spec: LoginFormSpec, otp_reader: HotmailOtpReader | None = None) -> None:
        if not spec.supports_info:
            raise LoginError("LoginFormSpec này không cấu hình login-by-info/userpass")
        self._spec = spec
        # Inject được để test không cần mô phỏng Outlook; mặc định reader thật (mở tab Outlook).
        self._otp_reader = otp_reader or HotmailOtpReader()

    def login(self, page: LoginPage, credential: Credential) -> LoginResult:
        if not credential.username:
            raise LoginError("login USERPASS cần username (định danh đăng nhập X)")
        if not credential.password:
            raise LoginError("login USERPASS cần password")
        logger.info("userpass-login (X native user/pass/2FA + fallback mã email Hotmail): goto %s", _X_LOGIN_URL)
        page.goto(_X_LOGIN_URL)

        email_attempted = False
        username_done = False  # ÉP thứ tự: chưa nhập xong username thì KHÔNG được điền password (xem gate dưới)
        last_sig: tuple[str, str] | None = None
        stuck = 0
        for step in range(_MAX_STEPS):
            # Chờ MỘT màn hình quen thuộc render trước khi đọc DOM (chống race → false negative — skill mục 6).
            page.wait_present(self._anchor_selector, _STEP_TIMEOUT)
            screen = self._classify(page)
            logger.info("userpass-login bước %d: màn '%s'", step, screen.value)

            if screen is _Screen.HOME:
                return self._verify(page)  # xác minh lại guard (INV-2) + thu fresh_cookie
            if screen is _Screen.BLOCK:
                return LoginResult(LoginOutcome.BLOCKED, LoginMethod.USERPASS, detail="captcha_or_challenge")
            if screen is _Screen.BAD_CRED:
                return LoginResult(LoginOutcome.BAD_CREDENTIAL, LoginMethod.USERPASS, detail="login_error_shown")

            # Chống lặp: cùng một màn (chữ ký = màn + url) lặp lại mà KHÔNG tiến → escalate/báo ra theo màn.
            sig = (screen.value, page.current_url)
            stuck = stuck + 1 if sig == last_sig else 0
            last_sig = sig

            # Ô số mà TOTP không qua (stuck) → thử coi như mã email (LoginAcid không lộ text) nếu có Hotmail.
            action = screen
            has_mailbox = bool(credential.hotmail_token or credential.hotmail_email)
            if action is _Screen.TWO_FACTOR and stuck >= 1 and not email_attempted and has_mailbox:
                action = _Screen.EMAIL_CODE

            # ÉP THỨ TỰ username → password (chốt chặn quan trọng nhất): X preload ô `input[name=password]` ẩn
            # ngay ở màn nhập TÀI KHOẢN → _classify có thể nhầm bước đầu thành 'password' và điền MẬT KHẨU vào ô
            # tài khoản. Khi CHƯA nhập xong username mà lại định điền password: còn ô identifier → nhập username
            # trước; chỉ có nút "Sign in" (landing) → mở luồng. Không phụ thuộc heuristic hiển thị nên bền hơn.
            if not username_done and action is _Screen.PASSWORD:
                if page.has_element(self._spec.username_selector):
                    action = _Screen.IDENTIFIER
                elif page.has_element(_LOGIN_ENTRY_SELECTOR):
                    action = _Screen.ENTRY
            if action is not screen:
                logger.info(
                    "userpass-login bước %d: ép '%s' → '%s' (chưa nhập username thì không điền password)",
                    step,
                    screen.value,
                    action.value,
                )

            if action is _Screen.ENTRY:
                # Landing x.com: bấm "Sign in" mở luồng đăng nhập (hiện ô username ở vòng sau). KHÔNG _advance
                # (đây là click mở form, không phải submit bước) — để _classify vòng sau bắt ô identifier.
                if not (
                    page.click(_LOGIN_ENTRY_SELECTOR)
                    or any(page.click_text(t) for t in _LOGIN_ENTRY_TEXTS)
                ):
                    return self._form_error("login_entry_not_found")
                page.wait_present(self._spec.username_selector, _STEP_TIMEOUT)
            elif action is _Screen.IDENTIFIER:
                if not page.fill(self._spec.username_selector, credential.username):
                    return self._form_error("identifier_field_not_found")
                self._advance(page, self._spec.username_selector)
                username_done = True  # đã nhập username → từ giờ MỚI cho phép nhánh password
                # Đúng luồng: nhập tài khoản → CHỜ X forward sang trang MẬT KHẨU rồi mới điền mk (vòng sau). X giữ
                # ô username cũ trong DOM nên chờ ô MẬT KHẨU render (tín hiệu đáng tin hơn 'ô username còn/mất');
                # X chèn bước khác (alt-identifier/2FA/mã email) → không hiện mk → _classify vòng sau tự xử.
                page.wait_present(self._spec.password_selector, _STEP_TIMEOUT)
            elif action is _Screen.ALT_IDENTIFIER:
                # X hỏi lại SĐT/username (chống bot) → điền lại username (ưu tiên confirm_username nếu có).
                value = credential.confirm_username or credential.username
                if not (
                    page.fill(self._spec.confirm_username_selector, value)
                    or page.fill(self._spec.username_selector, value)
                ):
                    return self._form_error("alt_identifier_field_not_found")
                self._advance(page, self._spec.confirm_username_selector)
            elif action is _Screen.CONFIRM:
                if not credential.confirm_username:
                    # X đòi @handle mà operator chưa cấp → báo RÕ việc cần làm (không đoán — INV-1).
                    return LoginResult(
                        LoginOutcome.BLOCKED, LoginMethod.USERPASS, detail="confirm_username_required"
                    )
                page.fill(self._spec.confirm_username_selector, credential.confirm_username)
                self._advance(page, self._spec.confirm_username_selector)
            elif action is _Screen.PASSWORD:
                if stuck >= 1:
                    # Đã điền mật khẩu mà vẫn kẹt màn mật khẩu → sai mật khẩu / bị chặn (không đoán — INV-1).
                    return LoginResult(
                        LoginOutcome.BAD_CREDENTIAL, LoginMethod.USERPASS, detail="password_not_accepted"
                    )
                if not page.fill(self._spec.password_selector, credential.password):
                    return self._form_error("password_field_not_found")
                self._advance(page, self._spec.password_selector)
            elif action is _Screen.TWO_FACTOR:
                result = self._handle_two_factor(page, credential, stuck)
                if result is not None:
                    return result
            elif action is _Screen.EMAIL_CODE:
                email_attempted = True
                result = self._handle_email_code(page, credential)
                if result is not None:
                    return result
            else:  # UNKNOWN — có thể chưa render xong; chờ thêm một vòng rồi mới báo lỗi form.
                if stuck >= 2:
                    return self._form_error("unknown_screen")
                continue

            if stuck >= 3:
                # Lặp mãi một màn không tiến (không rơi vào nhánh báo-rõ nào ở trên) → session chưa lập.
                return LoginResult(LoginOutcome.COOKIE_DEAD, LoginMethod.USERPASS, detail="login_stuck")

        # Hết số bước → xác minh lần cuối (có thể đã vào home ở bước cuối mà vòng lặp chưa kịp bắt).
        return self._verify(page)

    def _handle_two_factor(
        self, page: LoginPage, credential: Credential, stuck: int
    ) -> LoginResult | None:
        if not credential.otp_secret:
            # Cần 2FA mà không có secret → báo ra để người can thiệp (không đoán — INV-1).
            return LoginResult(LoginOutcome.OTP_REQUIRED, LoginMethod.USERPASS, detail="otp_needed_no_secret")
        if stuck >= 1:
            # Đã điền TOTP mà vẫn ở màn mã (secret sai / lệch đồng hồ) và không có nhánh email khả dụng → báo ra.
            return LoginResult(LoginOutcome.OTP_REQUIRED, LoginMethod.USERPASS, detail="otp_rejected")
        code = generate_totp(credential.otp_secret)
        if not page.fill(self._spec.otp_selectors[0], code):
            return self._form_error("otp_field_not_found")
        self._advance(page, self._spec.otp_selectors[0])
        return None

    def _handle_email_code(self, page: LoginPage, credential: Credential) -> LoginResult | None:
        # LoginAcid: mở tab Outlook lấy mã 6 số (token → fallback mật khẩu). Không lấy được → OTP_REQUIRED (INV-1).
        code = self._otp_reader.read_login_code(page, credential)
        if not code:
            return LoginResult(
                LoginOutcome.OTP_REQUIRED, LoginMethod.USERPASS, detail="email_code_unavailable"
            )
        if not page.fill(self._spec.otp_selectors[0], code):
            return self._form_error("email_code_field_not_found")
        self._advance(page, self._spec.otp_selectors[0])
        return None

    def _classify(self, page: LoginPage) -> _Screen:
        spec = self._spec
        login_fields = (
            spec.username_selector,
            spec.password_selector,
            *spec.otp_selectors,
            spec.confirm_username_selector,
        )
        # 1. GUARD đã đăng nhập (INV-2): thấy guard VÀ không còn ô login nào (X home không có ô đăng nhập).
        if page.has_element(*spec.verify_selectors) and not page.has_element(*login_fields):
            return _Screen.HOME
        # 2. Chặn CỨNG (arkose/captcha) TRƯỚC khi kết luận gì khác (skill: block trước verdict).
        if page.has_element(*_ARKOSE_SELECTORS) or page.has_text(*_ARKOSE_TEXTS):
            return _Screen.BLOCK
        # 3. Ô mật khẩu → PASSWORD (đặt trước identifier vì X giữ ô 'text' cũ trong DOM).
        if page.has_element(spec.password_selector):
            return _Screen.PASSWORD
        # 4. Ô SỐ (2FA / mã email) → phân biệt bằng TEXT (dùng chung testid OCF nên selector không tách được).
        if page.has_element(*spec.otp_selectors):
            if page.has_text(*_EMAIL_CODE_TEXTS):
                return _Screen.EMAIL_CODE
            return _Screen.TWO_FACTOR  # mặc định 2FA (rẻ, có secret); rớt → escalate email ở vòng sau
        # 5. 'Confirm your account' (@handle) — text đặc trưng + ô challenge_response (KHÔNG phải ô số).
        if page.has_text(*_CONFIRM_TEXTS) and page.has_element(spec.confirm_username_selector):
            return _Screen.CONFIRM
        # 6. 'Nhập SĐT hoặc username' (chống bot) — text đặc trưng + ô text.
        if page.has_text(*_ALT_IDENTIFIER_TEXTS) and page.has_element(
            spec.confirm_username_selector, spec.username_selector
        ):
            return _Screen.ALT_IDENTIFIER
        # 7. Ô identifier gốc.
        if page.has_element(spec.username_selector):
            return _Screen.IDENTIFIER
        # 8. Landing x.com CHƯA hiện ô nhập (chỉ có nút "Sign in") → mở luồng đăng nhập. Đặt SAU identifier để khi
        #    ô nhập đã hiện thì ưu tiên điền (nút loginButton có thể còn trong DOM nền sau khi mở modal).
        if page.has_element(_LOGIN_ENTRY_SELECTOR):
            return _Screen.ENTRY
        # 9. Thông báo sai mật khẩu rõ ràng (không kèm ô mật khẩu ở vòng này).
        if page.has_text(*_BAD_CRED_TEXTS):
            return _Screen.BAD_CRED
        return _Screen.UNKNOWN

    def _advance(self, page: LoginPage, enter_field: str) -> None:
        """Chuyển bước có XÁC MINH URL đổi (X SPA hash-routing). Thứ tự (Enter trước — như InfoLogin): Enter
        trong ô → nếu URL không đổi thì click nút submit (testid → text chính xác, né nút social)."""
        before = page.current_url
        page.press_enter(enter_field)
        if page.wait_url_change(before, _ADVANCE_TIMEOUT):
            return
        if self._spec.submit_selector and page.click(self._spec.submit_selector):
            if page.wait_url_change(before, _ADVANCE_TIMEOUT):
                return
        for text in (*self._spec.submit_texts, *self._spec.next_texts):
            if page.click_text(text) and page.wait_url_change(before, _ADVANCE_TIMEOUT):
                return

    def _verify(self, page: LoginPage) -> LoginResult:
        """Guard đăng nhập AUTHORITATIVE (INV-2): điều hướng home → chờ render → phân loại. KHÔNG tin cookie tên
        (session có thể chưa lập server-side). Chặn → BLOCKED; còn ở trang login → COOKIE_DEAD; không thấy guard
        → COOKIE_DEAD (không đoán LOGGED_IN — INV-1). Thấy guard → LOGGED_IN + thu fresh_cookie (§4.4)."""
        page.goto(self._spec.home_url)
        page.wait_present(
            ",".join(filter(None, (*self._spec.verify_selectors, *self._spec.block_selectors))),
            _VERIFY_RENDER_TIMEOUT,
        )
        if page.has_element(*self._spec.block_selectors):
            return LoginResult(LoginOutcome.BLOCKED, LoginMethod.USERPASS, detail="captcha_or_challenge")
        if self._redirected_to_login(page):
            return LoginResult(LoginOutcome.COOKIE_DEAD, LoginMethod.USERPASS, detail="not_logged_in_after_userpass")
        if not page.has_element(*self._spec.verify_selectors):
            return LoginResult(LoginOutcome.COOKIE_DEAD, LoginMethod.USERPASS, detail="verify_guard_failed")
        return LoginResult(
            LoginOutcome.LOGGED_IN, LoginMethod.USERPASS, fresh_cookie=page.cookies_string()
        )

    def _redirected_to_login(self, page: LoginPage) -> bool:
        url = page.current_url.lower()
        return any(marker in url for marker in self._spec.login_url_markers)

    def _form_error(self, step: str) -> LoginResult:
        logger.warning(
            "userpass-login DỪNG ở '%s' (không khớp selector) — X đổi DOM/chặn bot; xem DIAG để sửa selector",
            step,
        )
        return LoginResult(LoginOutcome.FORM_ERROR, LoginMethod.USERPASS, detail=step)

    @property
    def _anchor_selector(self) -> str:
        # CSS list mọi ô/guard đã biết → wait_present trả NGAY khi bất kỳ màn nào render (không sleep mù).
        spec = self._spec
        parts = [
            spec.username_selector,
            spec.password_selector,
            *spec.otp_selectors,
            spec.confirm_username_selector,
            *spec.verify_selectors,
            _LOGIN_ENTRY_SELECTOR,  # landing x.com (nút "Sign in") → không phải chờ hết giờ ở bước đầu
        ]
        return ",".join(p for p in parts if p)
