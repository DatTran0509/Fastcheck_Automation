"""Module kịch bản đăng nhập phía Client (station-management-design §7, spec §4.4).

Kịch bản login LƯU PHÍA CLIENT (đúng yêu cầu Excel): Server chỉ *gọi* "chạy script login platform X",
Client tự chạy tại máy trên browser GemLogin (DrissionPage). Interface chung `login(page, credential) ->
LoginResult`, mỗi platform một hiện thực (KHÔNG copy-paste 4 script rời — dùng chung flow, khác `spec`).

- **Login-by-cookie** cho CẢ 4 platform: cookie đã nạp TRƯỚC điều hướng (INV-2) → xác minh guard đăng nhập.
- **Login-by-info** (method INFO) khi cookie chết:
    * **X** → gõ user/pass gốc (InfoLogin) trên x.com, phát hiện captcha/OTP (TOTP secret khi X bật 2FA).
    * **TikTok & YouTube** → đăng nhập qua GOOGLE (GoogleLogin) bằng tài khoản Google — form Google ổn định
      hơn login gốc (đỡ vướng captcha/challenge); TOTP secret dùng khi TÀI KHOẢN GOOGLE bật 2FA (tùy chọn).
    * **Facebook** KHÔNG hỗ trợ login-by-info → ném LoginError (đúng phạm vi).
- Sau phiên thành công, cookie mới được thu (`fresh_cookie`) để orchestrator mã hoá & refresh (INV-12).
"""

from __future__ import annotations

from ..contracts import Platform
from .base import (
    Credential,
    LoginError,
    LoginMethod,
    LoginPage,
    LoginResult,
    LoginStrategy,
)
from .cookie_login import CookieLogin
from .google_login import GoogleLogin
from .info_login import InfoLogin
from .forms import FACEBOOK_LOGIN, TIKTOK_LOGIN, TWITTER_LOGIN, YOUTUBE_LOGIN

# login-by-cookie: cả 4 platform (verify selectors lấy từ bảng tín hiệu detector — không lặp lại).
_COOKIE: dict[Platform, CookieLogin] = {
    Platform.TIKTOK: CookieLogin(TIKTOK_LOGIN),
    Platform.FACEBOOK: CookieLogin(FACEBOOK_LOGIN),
    Platform.TWITTER: CookieLogin(TWITTER_LOGIN),
    Platform.YOUTUBE: CookieLogin(YOUTUBE_LOGIN),
}
# login-by-info: X gõ user/pass gốc; TikTok & YouTube đăng nhập qua Google (tài khoản Google).
_INFO: dict[Platform, LoginStrategy] = {
    Platform.TIKTOK: GoogleLogin(TIKTOK_LOGIN),
    Platform.TWITTER: InfoLogin(TWITTER_LOGIN),
    Platform.YOUTUBE: GoogleLogin(YOUTUBE_LOGIN),
}


def get_login_strategy(platform: Platform, method: LoginMethod) -> LoginStrategy:
    """Trả strategy đăng nhập cho (platform, method). Fail loud nếu không hỗ trợ (INV-1 — không đoán)."""
    if method == LoginMethod.COOKIE:
        return _COOKIE[platform]
    strategy = _INFO.get(platform)
    if strategy is None:
        raise LoginError(
            f"login-by-info không hỗ trợ cho {platform.value} "
            "(X: user/pass gốc; TikTok & YouTube: qua Google; Facebook: chỉ cookie)"
        )
    return strategy


__all__ = [
    "CookieLogin",
    "Credential",
    "GoogleLogin",
    "InfoLogin",
    "LoginError",
    "LoginMethod",
    "LoginPage",
    "LoginResult",
    "LoginStrategy",
    "get_login_strategy",
]
