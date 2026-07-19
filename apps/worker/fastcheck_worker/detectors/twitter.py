"""Detector X/Twitter — bảng tín hiệu khởi đầu (spec §10.5, skill platform-detector).

Loại target Excel yêu cầu: **post (tweet) / profile**. X dùng `data-testid` khá ổn định → guard/LIVE
dựa testid + aria (bền, INV-8). Soft-404 (post đã xoá / account suspended) bắt bằng NỘI DUNG. Guard chạy
trước: login wall / cookie chết → INCONCLUSIVE+CHALLENGED, KHÔNG DEAD (INV-2). Bảng là ĐIỂM KHỞI ĐẦU.
"""

from __future__ import annotations

from .base import BaseDetector, SignalSpec

TWITTER_SPEC = SignalSpec(
    # Đã đăng nhập: nút chuyển tài khoản / tab profile ở side-nav.
    login_selectors=(
        '[data-testid="SideNav_AccountSwitcher_Button"]',
        '[data-testid="AppTabBar_Profile_Link"]',
        '[aria-label="Account menu"]',
        '[data-testid="SideNav_NewTweet_Button"]',
    ),
    # /i/flow/login, /login → chưa đăng nhập. So theo segment path.
    login_url_markers=("login", "flow", "logout"),
    # Cookie session X/Twitter: auth_token (phiên) + ct0 (CSRF) — cả hai có khi đã đăng nhập. Locale-independent.
    auth_cookies=("auth_token", "ct0"),
    # LIVE: tweet render, hoặc header profile hiển thị. CHỈ selector đơn (không tổ hợp con cháu) để
    # fake (HtmlPageView) và real (DrissionPage) khớp nhau — `primaryColumn` là khung, KHÔNG dùng làm LIVE.
    live_selectors=(
        '[data-testid="tweet"]',
        'article[data-testid="tweet"]',
        '[data-testid="UserName"]',
        '[data-testid="UserProfileHeader_Items"]',
        '[data-testid="tweetText"]',
    ),
    # DEAD qua DOM: khối báo lỗi rỗng của X.
    dead_selectors=(
        '[data-testid="error-detail"]',
        '[data-testid="empty_state_header_text"]',
    ),
    # DEAD qua NỘI DUNG (soft-404): post xoá / account không tồn tại / bị đình chỉ.
    dead_texts=(
        "this post is unavailable",
        "this post was deleted",
        "post unavailable",
        "hmm...this page doesn't exist",
        "this account doesn't exist",
        "account suspended",
        "these tweets are protected",
        "this account owner limits who can view",
    ),
    # BLOCKED/CHALLENGE: arkose/funcaptcha, xác minh con người.
    block_selectors=(
        'iframe[src*="arkoselabs"]',
        'iframe[src*="funcaptcha"]',
        '#arkose',
        '[data-testid="ocfEnterTextTextInput"]',
    ),
    block_texts=(
        "verify you're human",
        "unusual login activity",
        "confirm your identity",
        "solve this puzzle",
        "we've detected unusual activity",
        "authorize access to your account",
    ),
)


class TwitterDetector(BaseDetector):
    def __init__(self) -> None:
        super().__init__(TWITTER_SPEC)
