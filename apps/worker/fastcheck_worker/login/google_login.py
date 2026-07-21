"""Login qua GOOGLE ("Continue with Google") cho TikTok & YouTube — dùng TÀI KHOẢN GOOGLE (email/mật khẩu).

Vì sao: login gốc của các platform này hay vướng captcha/challenge (DOM đổi liên tục); form Google ổn định hơn.
Luồng (đúng thao tác tay người dùng):
  TikTok: mở trang login TikTok → click "Continue with Google" → (Google mở tab/popup) → email → Enter →
          mật khẩu → Enter → (TÙY CHỌN) Google hỏi mã 2FA nếu tài khoản Google bật authenticator app
  YT    : vào THẲNG Google sign-in (YouTube = Google) → email → Enter → mật khẩu → Enter → (tùy chọn) 2FA
Sau đó verify guard đăng nhập trên platform (cookie-first như CookieLogin — INV-8). KHÔNG log credential (INV-12).

Google chặn browser tự động RẤT mạnh ("This browser or app may not be secure"): nếu sau khi nhập email mà
KHÔNG hiện ô mật khẩu → coi là BLOCKED (báo RÕ, không đoán — INV-1), đường tin cậy vẫn là login-by-cookie.

2FA Google là TÙY CHỌN (chỉ xảy ra nếu tài khoản Google đó bật authenticator app) — có otp_secret thì tự
sinh mã điền tiếp; không có thì báo OTP_REQUIRED; không thấy màn hình 2FA thì bỏ qua bước này (INV-1: nhánh
rõ ràng có/không, không đoán).
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from .base import Credential, LoginError, LoginMethod, LoginOutcome, LoginPage, LoginResult, generate_totp
from .forms import LoginFormSpec

logger = logging.getLogger("fastcheck.worker.login")

# Chờ tối đa mỗi bước (giây) — tổng < timeout job (INV-9) & < command_ack_timeout orchestrator.
_STEP_TIMEOUT = 15.0
# 2FA Google là bước TÙY CHỌN — ngắn hơn _STEP_TIMEOUT để không giữ MỌI lần login chờ thêm nếu tài khoản
# không bật 2FA (đa số trường hợp).
_GOOGLE_OTP_TIMEOUT = 5.0
# Selector form Google (CHUNG cho mọi platform — đây là trang của Google, không phải của TikTok/YT). Fallback id cũ.
_GOOGLE_EMAIL = 'input[type="email"], #identifierId'
_GOOGLE_PASSWORD = 'input[type="password"], input[name="Passwd"]'
# Màn hình 2FA (TOTP) của Google — CHƯA kiểm chứng trên DOM thật (không có quyền truy cập trực tiếp lúc viết);
# xác nhận lại qua form_diagnostics() nếu OTP không được nhận diện đúng, rồi cập nhật selector (INV-7).
_GOOGLE_OTP = 'input#totpPin, input[name="totpPin"], input[type="tel"]'


def _redirected_to_login(url: str, markers: tuple[str, ...]) -> bool:
    """URL có SEGMENT trùng marker (vd /login) = chưa đăng nhập (so segment, không substring — như base)."""
    if not markers:
        return False
    segments = {seg.lower() for seg in urlparse(url).path.split("/") if seg}
    return any(m.lower() in segments for m in markers)


def _cookie_names(page: LoginPage) -> set[str]:
    getter = getattr(page, "cookie_names", None)
    if getter is None:
        return set()
    try:
        return set(getter())
    except Exception:  # noqa: BLE001 — best-effort, lỗi → coi như không có
        return set()


class GoogleLogin:
    """Đăng nhập platform qua Google OAuth. Credential.username/password là TÀI KHOẢN GOOGLE."""

    def __init__(self, spec: LoginFormSpec) -> None:
        self._spec = spec

    def login(self, page: LoginPage, credential: Credential) -> LoginResult:
        if not credential.username or not credential.password:
            raise LoginError("đăng nhập Google cần email + mật khẩu Google")
        spec = self._spec
        start_url = spec.google_login_url or spec.login_url
        logger.info("google-login v2 (TikTok/YouTube qua Google, 2FA tùy chọn): mở %s", start_url)
        page.goto(start_url)

        # TikTok: click "Continue with Google" để mở OAuth (YouTube: vào thẳng Google, google_button_texts rỗng).
        if spec.google_button_texts:
            if not any(page.click_text(t) for t in spec.google_button_texts):
                logger.warning("google-login: không thấy nút 'Continue with Google' — platform đổi giao diện?")
                return LoginResult(
                    LoginOutcome.FORM_ERROR, LoginMethod.INFO, detail="google_button_not_found"
                )
            # OAuth Google thường mở tab/popup MỚI → chuyển thao tác sang tab đó.
            page.use_latest_tab()

        # Email Google → Enter.
        if not page.fill(_GOOGLE_EMAIL, credential.username):
            logger.warning("google-login: không thấy ô email Google")
            return LoginResult(LoginOutcome.FORM_ERROR, LoginMethod.INFO, detail="google_email_not_found")
        page.press_enter(_GOOGLE_EMAIL)

        # Chờ ô mật khẩu Google hiện ra. Không hiện = Google CHẶN browser tự động / bắt xác minh (báo rõ).
        if not page.wait_present(_GOOGLE_PASSWORD, _STEP_TIMEOUT):
            logger.warning(
                "google-login: không hiện ô mật khẩu — Google chặn browser tự động ('may not be secure') "
                "hoặc bắt xác minh; đường tin cậy là login-by-cookie"
            )
            return LoginResult(
                LoginOutcome.BLOCKED, LoginMethod.INFO, detail="google_blocked_or_verify"
            )
        if not page.fill(_GOOGLE_PASSWORD, credential.password):
            return LoginResult(
                LoginOutcome.FORM_ERROR, LoginMethod.INFO, detail="google_password_not_found"
            )
        page.press_enter(_GOOGLE_PASSWORD)

        # 2FA Google TÙY CHỌN (chỉ khi tài khoản Google đó bật authenticator app). `wait_present` cho DOM
        # kịp render (không dùng giá trị trả về để quyết định — bước này KHÔNG bắt buộc phải xuất hiện),
        # `has_element` (timeout=0) mới là tín hiệu quyết định nhánh (INV-1: rõ có/không, không đoán).
        page.wait_present(_GOOGLE_OTP, _GOOGLE_OTP_TIMEOUT)
        if page.has_element(_GOOGLE_OTP):
            otp_result = self._handle_google_otp(page, credential)
            if otp_result is not None:
                return otp_result

        # Xong OAuth ở popup → quay về tab platform để verify.
        page.use_main_tab()
        return self._verify(page)

    def _handle_google_otp(self, page: LoginPage, credential: Credential) -> LoginResult | None:
        """Google bắt 2FA (tài khoản Google bật authenticator app). Có otp_secret → tự sinh TOTP điền tiếp
        rồi để `_verify()` xác nhận kết quả cuối; không có secret → OTP_REQUIRED (cần người, không đoán —
        INV-1). Trả None nghĩa là đã điền code, luồng tiếp tục bình thường."""
        if not credential.otp_secret:
            return LoginResult(
                LoginOutcome.OTP_REQUIRED, LoginMethod.INFO, detail="google_otp_needed_no_secret"
            )
        code = generate_totp(credential.otp_secret)
        page.fill(_GOOGLE_OTP, code)
        page.press_enter(_GOOGLE_OTP)
        return None

    def _verify(self, page: LoginPage) -> LoginResult:
        """Verify guard đăng nhập trên platform (cookie-first, giống CookieLogin — không lệ thuộc selector giòn)."""
        spec = self._spec
        page.goto(spec.home_url)
        if spec.block_selectors and page.has_element(*spec.block_selectors):
            return LoginResult(LoginOutcome.BLOCKED, LoginMethod.INFO, detail="captcha_or_challenge")
        if _redirected_to_login(page.current_url, spec.login_url_markers):
            # Vẫn ở trang login sau khi qua Google = chưa lập được phiên (lỗi profile, không kết luận target).
            return LoginResult(
                LoginOutcome.COOKIE_DEAD, LoginMethod.INFO, detail="not_logged_in_after_google"
            )
        if spec.auth_cookies:
            names = _cookie_names(page)
            if names and all(c in names for c in spec.auth_cookies):
                return LoginResult(
                    LoginOutcome.LOGGED_IN, LoginMethod.INFO, fresh_cookie=page.cookies_string()
                )
        if not page.has_element(*spec.verify_selectors):
            return LoginResult(LoginOutcome.COOKIE_DEAD, LoginMethod.INFO, detail="login_guard_failed")
        return LoginResult(LoginOutcome.LOGGED_IN, LoginMethod.INFO, fresh_cookie=page.cookies_string())
