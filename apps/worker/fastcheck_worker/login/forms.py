"""Bảng tín hiệu/selector cho kịch bản login mỗi platform (điểm khởi đầu — health-check định kỳ).

Tái sử dụng `SignalSpec` của detector: `login_selectors` (guard đã đăng nhập), `block_selectors`
(captcha/challenge), `login_url_markers` (trang login) — KHÔNG khai báo lại (một nguồn sự thật).
Form selector (ô user/pass/submit/OTP) chỉ dùng cho login-by-info trực tiếp trên site gốc (X) — TikTok &
YouTube đăng nhập qua tài khoản Google (GoogleLogin) nên không cần khai báo form gốc riêng.
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
    # Ô nhập @username ở bước "Confirm your account" (X hỏi @handle để chống bot sau bước nhập tài khoản). Điền
    # confirm_username của credential vào đây; rỗng = platform không có bước này.
    confirm_username_selector: str = ""
    # Text nút submit ("Log in"/"Đăng nhập"/"Continue"/"Tiếp tục").
    submit_texts: tuple[str, ...] = ()
    otp_selectors: tuple[str, ...] = ()
    error_selectors: tuple[str, ...] = ()
    supports_info: bool = False
    # ── Đăng nhập qua GOOGLE (TikTok & YouTube — GoogleLogin) ──
    # URL bắt đầu cho Google-login (YT: trang Google sign-in; TikTok: trang login có nút "Continue with Google").
    google_login_url: str = ""
    # Text nút "Continue with Google" trên trang platform (TikTok). Rỗng = vào thẳng Google (YouTube).
    google_button_texts: tuple[str, ...] = ()


TIKTOK_LOGIN = LoginFormSpec(
    home_url="https://www.tiktok.com/",
    verify_selectors=TIKTOK_SPEC.login_selectors,
    block_selectors=TIKTOK_SPEC.block_selectors,
    login_url_markers=TIKTOK_SPEC.login_url_markers,
    auth_cookies=TIKTOK_SPEC.auth_cookies,
    # Đăng nhập qua GOOGLE (TikTok — GoogleLogin): mở trang chọn phương thức đăng nhập TikTok (có nút social)
    # → "Continue with Google" → Google OAuth (email/mật khẩu tài khoản Google, 2FA tùy chọn nếu có). TikTok
    # KHÔNG dùng form gốc (user/pass trực tiếp trên tiktok.com) nên không khai báo field form ở đây.
    google_login_url="https://www.tiktok.com/login",
    google_button_texts=("Continue with Google", "Tiếp tục với Google"),
)

TWITTER_LOGIN = LoginFormSpec(
    home_url="https://x.com/home",
    verify_selectors=TWITTER_SPEC.login_selectors,
    block_selectors=TWITTER_SPEC.block_selectors,
    login_url_markers=TWITTER_SPEC.login_url_markers,
    auth_cookies=TWITTER_SPEC.auth_cookies,
    # Passwordless (người dùng chỉ): nhập email → Next → "Confirm your account" (@username) → Next → mã OTP.
    login_url="https://x.com",
    # CSS list (dấu phẩy = fallback, INV-8) vì X phục vụ NHIỀU biến thể trang login. `~=` khớp
    # autocomplete="username webauthn" (khớp CHÍNH XÁC "username" KHÔNG match — bug cũ khiến ô user không được gõ).
    username_selector='input[autocomplete~="username"], input[name="text"], input[name="username_or_email"]',
    password_selector='input[name="password"], input[type="password"]',
    submit_selector='[data-testid="LoginForm_Login_Button"]',
    next_selector='[data-testid="ocfEnterTextNextButton"], [data-testid="OCF_CallToAction_Button"]',
    # Text nút submit bước nhập tk & "Confirm your account". "Next" ĐỨNG TRƯỚC "Continue": trên trang landing
    # X, nút chính là "Next" — khớp trước để KHÔNG đụng nút social "Continue with Google/phone/Apple"; ở màn
    # "Confirm your account" nút là "Continue" (không có nút social nào ở đó nên an toàn). Chủ yếu dựa selector.
    next_texts=("Next", "Continue", "Tiếp theo", "Tiếp tục"),
    # Ô @username ở bước "Confirm your account" (knowledge_check) thực tế là `input[name="challenge_response"]`
    # (xác nhận qua DIAG form thật). Đặt TRƯỚC làm selector chính; thêm biến thể OCF/name=text làm fallback cho
    # các phiên bản trang khác. Loại `inputmode="numeric"` để KHÔNG khớp ô OTP. KHÔNG dùng `name="username_or_email"`
    # (ô email cũ X giữ lại trong DOM) để tránh điền nhầm @username vào ô email.
    confirm_username_selector=(
        'input[name="challenge_response"], '
        '[data-testid="ocfEnterTextTextInput"]:not([inputmode="numeric"]), '
        'input[name="text"]:not([inputmode="numeric"])'
    ),
    # Text nút submit cho bước mật khẩu (fallback) VÀ bước OTP: "Log in" (màn mật khẩu) + "Next"/"Continue"
    # (màn OTP knowledge_check của X). Click theo text CHÍNH XÁC nên liệt kê nhiều nhãn là an toàn (chỉ nhãn
    # khớp đúng mới được click), né nút social "Continue with ...".
    submit_texts=("Log in", "Đăng nhập", "Next", "Continue", "Tiếp theo", "Tiếp tục"),
    # CHỈ selector CÓ inputmode="numeric": testid OCF gốc (`ocfEnterTextTextInput`) dùng CHUNG cho cả bước
    # "confirm your identity" (challenge — đã có trong block_selectors của detector) lẫn bước nhập mã 2FA,
    # không tự phân biệt được. inputmode="numeric" là tín hiệu ĐÚNG riêng cho "đây là OTP" (INV-7/INV-1).
    otp_selectors=('input[name="text"][inputmode="numeric"]',),
    error_selectors=('[data-testid="LoginForm_Login_Button"][aria-disabled="true"]',),
    supports_info=True,
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
