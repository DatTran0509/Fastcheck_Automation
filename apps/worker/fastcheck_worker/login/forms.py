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
    # Bước trung gian (X: nhập username → "Next" → nhập password).
    next_selector: str = ""
    otp_selectors: tuple[str, ...] = ()
    error_selectors: tuple[str, ...] = ()
    supports_info: bool = False


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
    login_url="https://x.com/i/flow/login",
    username_selector='input[autocomplete="username"]',
    password_selector='input[name="password"]',
    submit_selector='[data-testid="LoginForm_Login_Button"]',
    next_selector='[role="button"][data-testid="ocfEnterTextNextButton"]',
    otp_selectors=('[data-testid="ocfEnterTextTextInput"]', 'input[name="text"][inputmode="numeric"]'),
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
)
