"""Bảng tín hiệu/selector cho kịch bản login mỗi platform (điểm khởi đầu — health-check định kỳ).

Tái sử dụng `SignalSpec` của detector: `login_selectors` (guard đã đăng nhập), `block_selectors`
(captcha/challenge), `login_url_markers` (trang login) — KHÔNG khai báo lại (một nguồn sự thật).
Form selector (ô user/pass/submit/OTP) chỉ dùng cho login-by-info (TikTok & X).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..detectors.facebook import FACEBOOK_SPEC
from ..detectors.tiktok import TIKTOK_SPEC
from ..detectors.twitter import TWITTER_SPEC
from ..detectors.youtube import YOUTUBE_SPEC


@dataclass(frozen=True)
class LoginFormSpec:
    """Cấu hình login một platform. `home_url` để verify cookie; phần form chỉ cho info-login."""

    home_url: str
    verify_selectors: tuple[str, ...]  # thấy = đã đăng nhập (guard DOM — fallback)
    block_selectors: tuple[str, ...]  # captcha/challenge
    login_url_markers: tuple[str, ...]  # segment path của trang login (chưa đăng nhập)
    # Cookie đăng nhập cốt lõi (tái dùng từ detector SPEC): guard cookie-first, locale-independent (INV-8).
    auth_cookies: tuple[str, ...] = ()
    # ── info-login (TikTok & X) ──
    login_url: str = ""
    username_selector: str = ""
    password_selector: str = ""
    submit_selector: str = ""
    # Bước trung gian (X: nhập username → "Continue" → nhập password).
    next_selector: str = ""
    # Text nút "Continue"/"Tiếp tục" (X không có testid ổn định) — click theo text nếu selector không khớp.
    next_texts: tuple[str, ...] = ()
    # Link "Use password" (X hiện bước "Confirm your account" khi nghi bot) — bấm để sang ô mật khẩu.
    use_password_text: str = ""
    # Text nút submit ("Log in"/"Đăng nhập"/"Continue"/"Tiếp tục").
    submit_texts: tuple[str, ...] = ()
    otp_selectors: tuple[str, ...] = ()
    error_selectors: tuple[str, ...] = ()
    supports_info: bool = False
    # ── Đăng nhập qua GOOGLE (X & YouTube — GoogleLogin) ──
    # URL bắt đầu cho Google-login (YT: trang Google sign-in; X: trang login có nút "Continue with Google").
    google_login_url: str = ""
    # Text nút "Continue with Google" trên trang platform (X). Rỗng = vào thẳng Google (YouTube).
    google_button_texts: tuple[str, ...] = ()


TIKTOK_LOGIN = LoginFormSpec(
    home_url="https://www.tiktok.com/",
    verify_selectors=TIKTOK_SPEC.login_selectors,
    block_selectors=TIKTOK_SPEC.block_selectors,
    login_url_markers=TIKTOK_SPEC.login_url_markers,
    auth_cookies=TIKTOK_SPEC.auth_cookies,
    login_url="https://www.tiktok.com/login/phone-or-email/email",
    username_selector='input[name="username"]',
    password_selector='input[type="password"]',
    submit_selector='button[data-e2e="login-button"]',
    otp_selectors=('input[name="code"]', '[data-e2e="verify-code"]'),
    error_selectors=('[data-e2e="login-error"]', ".error-text"),
    supports_info=True,
)

TWITTER_LOGIN = LoginFormSpec(
    home_url="https://x.com/home",
    verify_selectors=TWITTER_SPEC.login_selectors,
    block_selectors=TWITTER_SPEC.block_selectors,
    login_url_markers=TWITTER_SPEC.login_url_markers,
    auth_cookies=TWITTER_SPEC.auth_cookies,
    # URL onboarding trực tiếp (người dùng chỉ): nhập tk → Continue → Use password → nhập mk → Continue.
    login_url="https://x.com/i/jf/onboarding/web?mode=login",
    # CSS list (dấu phẩy = fallback, INV-8) vì X phục vụ NHIỀU biến thể trang login. `~=` khớp
    # autocomplete="username webauthn" (khớp CHÍNH XÁC "username" KHÔNG match — bug cũ khiến ô user không được gõ).
    username_selector='input[autocomplete~="username"], input[name="text"], input[name="username_or_email"]',
    password_selector='input[name="password"], input[type="password"]',
    submit_selector='[data-testid="LoginForm_Login_Button"]',
    next_selector='[data-testid="ocfEnterTextNextButton"], [data-testid="OCF_CallToAction_Button"]',
    next_texts=("Continue", "Tiếp tục"),
    # X chèn "Confirm your account" (username/phone) khi nghi bot — link "Use password" bỏ qua sang mật khẩu.
    use_password_text="Use password",
    submit_texts=("Log in", "Đăng nhập", "Continue", "Tiếp tục"),
    otp_selectors=('[data-testid="ocfEnterTextTextInput"]', 'input[name="text"][inputmode="numeric"]'),
    error_selectors=('[data-testid="LoginForm_Login_Button"][aria-disabled="true"]',),
    supports_info=True,
    # Đăng nhập qua Google: mở trang login X (có nút social) → "Continue with Google" → Google OAuth.
    google_login_url="https://x.com/i/flow/login",
    google_button_texts=("Continue with Google", "Tiếp tục với Google", "Sign in with Google"),
)

# FB & YT: chỉ login-by-cookie (đúng phạm vi Excel) — không có phần form info.
FACEBOOK_LOGIN = LoginFormSpec(
    home_url="https://www.facebook.com/",
    verify_selectors=FACEBOOK_SPEC.login_selectors,
    block_selectors=FACEBOOK_SPEC.block_selectors,
    login_url_markers=FACEBOOK_SPEC.login_url_markers,
    auth_cookies=FACEBOOK_SPEC.auth_cookies,
)

YOUTUBE_LOGIN = LoginFormSpec(
    home_url="https://www.youtube.com/",
    verify_selectors=YOUTUBE_SPEC.login_selectors,
    block_selectors=YOUTUBE_SPEC.block_selectors,
    login_url_markers=YOUTUBE_SPEC.login_url_markers,
    auth_cookies=YOUTUBE_SPEC.auth_cookies,
    # YouTube = tài khoản Google → vào THẲNG Google sign-in (không có nút "Continue with Google"), rồi
    # redirect về youtube. Đăng nhập bằng email/mật khẩu GOOGLE.
    google_login_url="https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fwww.youtube.com%2F",
)
