"""Module kịch bản đăng nhập phía Client (station-management-design §7, spec §4.4).

Kịch bản login LƯU PHÍA CLIENT (đúng yêu cầu Excel): Server chỉ *gọi* "chạy script login platform X",
Client tự chạy tại máy trên browser GemLogin (DrissionPage). Interface chung `login(page, credential) ->
LoginResult`, mỗi platform một hiện thực (KHÔNG copy-paste 4 script rời — dùng chung flow, khác `spec`).

- **Login-by-cookie** cho CẢ 4 platform: cookie đã nạp TRƯỚC điều hướng (INV-2) → xác minh guard đăng nhập.
- **Login-by-info** cho **TikTok & X** khi cookie chết: gõ mô phỏng người, phát hiện captcha/OTP (spec §4.4).
  FB & YT KHÔNG hỗ trợ login-by-info (đúng phạm vi Excel) → yêu cầu info cho FB/YT ném LoginError.
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
from .info_login import InfoLogin
from .forms import FACEBOOK_LOGIN, TIKTOK_LOGIN, TWITTER_LOGIN, YOUTUBE_LOGIN

# login-by-cookie: cả 4 platform (verify selectors lấy từ bảng tín hiệu detector — không lặp lại).
_COOKIE: dict[Platform, CookieLogin] = {
    Platform.TIKTOK: CookieLogin(TIKTOK_LOGIN),
    Platform.FACEBOOK: CookieLogin(FACEBOOK_LOGIN),
    Platform.TWITTER: CookieLogin(TWITTER_LOGIN),
    Platform.YOUTUBE: CookieLogin(YOUTUBE_LOGIN),
}
# login-by-info: CHỈ TikTok & X (spec §4.4 / Excel).
_INFO: dict[Platform, InfoLogin] = {
    Platform.TIKTOK: InfoLogin(TIKTOK_LOGIN),
    Platform.TWITTER: InfoLogin(TWITTER_LOGIN),
}


def get_login_strategy(platform: Platform, method: LoginMethod) -> LoginStrategy:
    """Trả strategy đăng nhập cho (platform, method). Fail loud nếu không hỗ trợ (INV-1 — không đoán)."""
    if method == LoginMethod.COOKIE:
        return _COOKIE[platform]
    strategy = _INFO.get(platform)
    if strategy is None:
        raise LoginError(f"login-by-info không hỗ trợ cho {platform.value} (chỉ TikTok & X — spec §4.4)")
    return strategy


__all__ = [
    "CookieLogin",
    "Credential",
    "InfoLogin",
    "LoginError",
    "LoginMethod",
    "LoginPage",
    "LoginResult",
    "LoginStrategy",
    "get_login_strategy",
]
