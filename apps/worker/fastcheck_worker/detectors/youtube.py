"""Detector YouTube — bảng tín hiệu khởi đầu (spec §10.5, skill platform-detector).

Loại target Excel yêu cầu: **video / channel**. YouTube dùng custom element (`ytd-*`) + id ổn định
(`#movie_player`) → LIVE dựa player/tiêu đề (video) và tên kênh (channel). DEAD (video gỡ/riêng tư, kênh
bị chấm dứt) bắt bằng NỘI DUNG (soft-404, INV-8). Guard đăng nhập trước; consent/verify bất thường →
BLOCKED (INV-2/INV-3). Bảng là ĐIỂM KHỞI ĐẦU — golden set + alert bắt khi cơ chế đổi.
"""

from __future__ import annotations

from .base import BaseDetector, SignalSpec

YOUTUBE_SPEC = SignalSpec(
    # Đã đăng nhập: avatar tài khoản ở masthead. CHỈ selector đơn (fake↔real khớp nhau).
    login_selectors=(
        "#avatar-btn",
        '[aria-label="Account menu"]',
        "ytd-topbar-menu-button-renderer",
    ),
    login_url_markers=("signin", "servicelogin"),
    # Cookie đăng nhập trên domain .youtube.com (đã kiểm chứng export thật): LOGIN_INFO (chỉ có khi đã đăng
    # nhập YouTube) + SAPISID (session Google). KHÔNG dùng SID/HSID (chỉ có ở .google.com, KHÔNG ở .youtube.com).
    auth_cookies=("LOGIN_INFO", "SAPISID"),
    # LIVE: player video (video) hoặc header kênh (channel). KHÔNG dùng `ytd-watch-flexy` — đó là khung
    # trang /watch, hiện diện CẢ khi video gỡ (over-broad → false LIVE). Dựa player + header kênh cụ thể.
    # LIVE: TIÊU ĐỀ video/kênh — chỉ có ở target THẬT. KHÔNG dùng #movie_player/.html5-video-player: khung
    # player hiện CẢ ở trang video-không-tồn-tại (báo lỗi bên trong) → LIVE giả (đã kiểm chứng thật).
    live_selectors=(
        '[itemprop="name"]',  # tiêu đề video/kênh (schema.org) — video thật có, trang lỗi không
        ".slim-video-information-title",  # tiêu đề video (m.youtube.com)
        "ytd-watch-metadata",  # tiêu đề video (desktop)
        "#channel-header",
        "ytd-channel-name",
    ),
    # DEAD qua DOM: overlay lỗi trong player (video gỡ/không tồn tại) — đã kiểm chứng thật trên m.youtube.com.
    dead_selectors=(
        ".player-error-overlay",
        "ytd-background-promo-renderer",
        ".ytp-error",
        "#error-screen",
    ),
    # DEAD qua NỘI DUNG (soft-404): video gỡ/riêng tư/kênh chấm dứt.
    dead_texts=(
        "video unavailable",
        "this video is unavailable",  # m.youtube.com — đã kiểm chứng thật
        "this video isn't available anymore",
        "this video is private",
        "this video has been removed",
        "this channel does not exist",
        "this channel doesn't exist",
        "account associated with this video has been terminated",
        "removed for violating youtube's",
    ),
    # BLOCKED/CHALLENGE: recaptcha / trang consent bất thường / unusual traffic.
    block_selectors=(
        'iframe[src*="recaptcha"]',
        "#recaptcha",
        "ytd-consent-bump-v2-lightbox",
        'form[action*="consent"]',
    ),
    block_texts=(
        "confirm you're not a robot",
        "our systems have detected unusual traffic",
        "verify it's you",
        "before you continue to youtube",
    ),
)


class YouTubeDetector(BaseDetector):
    def __init__(self) -> None:
        super().__init__(YOUTUBE_SPEC)
