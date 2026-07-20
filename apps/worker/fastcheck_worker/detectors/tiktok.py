"""Detector TikTok — bảng tín hiệu khởi đầu (spec §10.5, skill platform-detector).

CHỈ khai báo dữ liệu tín hiệu; toàn bộ logic (guard, block, vote, 3 nhánh) nằm ở `base.BaseDetector`.
Bảng này là ĐIỂM KHỞI ĐẦU, không phải chân lý — TikTok đổi cơ chế bất cứ lúc nào → golden set +
alert khi INCONCLUSIVE/BLOCKED tăng đột biến tồn tại để bắt việc đó (docs/anti-patterns.md).
"""

from __future__ import annotations

from .base import BaseDetector, SignalSpec

TIKTOK_SPEC = SignalSpec(
    # Đã đăng nhập: avatar/menu người dùng ở nav. Ưu tiên data-e2e/aria (bền), kèm fallback.
    login_selectors=(
        "[data-e2e=profile-icon]",
        "[data-e2e=nav-profile]",
        "[data-e2e=nav-user]",
        "[aria-label=Profile]",
    ),
    login_url_markers=("login", "signup", "log-in"),
    # Cookie session TikTok: sessionid (bắt buộc khi đã đăng nhập). Locale-independent (INV-2/INV-8).
    auth_cookies=("sessionid",),
    # LIVE: trang VIDEO (chi tiết) HOẶC trang PROFILE (đã kiểm chứng DOM thật: stats + follow + grid).
    # KHÔNG dùng bare "video": thẻ <video> shell hiện CẢ ở trang "video không khả dụng" → LIVE giả (đã kiểm
    # chứng: id video sai vẫn khớp "video"). Dùng data-e2e chi tiết video (chỉ có ở video THẬT).
    live_selectors=(
        # Video — khung chi tiết + player.
        "[data-e2e=browse-video]",
        "[data-e2e=video-detail]",
        "[data-e2e=video-player]",
        "[data-e2e=feed-video]",
        # Tương tác video THẬT (like/comment/share/desc/music): CHỈ có ở video sống, KHÔNG có ở trang "video
        # không khả dụng" → tín hiệu LIVE bền hơn, không dương tính giả như bare <video>. (TikTok đổi data-e2e
        # liên tục — nhiều biến thể để bắt được; xem log DIAG khi no_decisive_signal để cập nhật.)
        "[data-e2e=like-count]",
        "[data-e2e=comment-count]",
        "[data-e2e=share-count]",
        "[data-e2e=browse-like-count]",
        "[data-e2e=browse-comment-count]",
        "[data-e2e=video-desc]",
        "[data-e2e=browse-video-desc]",
        "[data-e2e=video-music]",
        "[data-e2e=browse-music]",
        # Profile (@user): follower count + nút follow + tiêu đề + item video trong grid — profile sống.
        "[data-e2e=followers-count]",
        "[data-e2e=follow-button]",
        "[data-e2e=user-title]",
        "[data-e2e=user-post-item]",
    ),
    # DEAD qua DOM: khối báo video/tài khoản không tồn tại.
    dead_selectors=(
        "[data-e2e=video-detail-notfound]",
        "[data-e2e=user-page-notfound]",
    ),
    # DEAD qua NỘI DUNG (soft-404 — HTTP 200 vẫn có thể là chết).
    dead_texts=(
        "video currently unavailable",
        "video is currently unavailable",
        "video unavailable",
        "couldn't find this account",
        "couldn't find this video",
        "this video is not available",
        "this account was banned",
        # TikTok tiếng Việt (đã kiểm chứng thật). Dùng SUBSTRING ngắn để bền với biến thể chữ:
        # video sai → "Video hiện không khả dụng"; account sai → "Không THỂ tìm thấy tài khoản này".
        "video hiện không khả dụng",
        "tìm thấy tài khoản này",  # khớp cả "không thể tìm thấy..." lẫn "không tìm thấy..."
        "không tìm thấy video",
        "tài khoản này không tồn tại",
    ),
    # BLOCKED/CHALLENGE: captcha / cloudflare turnstile.
    block_selectors=(
        "#captcha-verify-container",
        ".captcha_verify_container",
        '[id*="captcha"]',
        # Giá trị có dấu chấm PHẢI trích dẫn: real CSS querySelector ném lỗi nếu để trần (fake↔real khớp).
        'iframe[src*="challenges.cloudflare"]',
    ),
    block_texts=(
        "verify to continue",
        "security check",
        "please verify",
        # Trang CHẶN/giới hạn tạm thời (anti-bot/rate-limit) — "thử lại sau" = TẠM THỜI → BLOCKED (không phải
        # DEAD). Đã kiểm chứng thật khi bị TikTok throttle: "Page not available. Sorry about that! Please try
        # again later." KHÔNG nhầm với "Video hiện không khả dụng" (DEAD — không có 'try again later').
        "page not available",
        "please try again later",
        "try again later",
        "sorry about that",
        "vui lòng thử lại sau",
        "thử lại sau",
    ),
)


class TikTokDetector(BaseDetector):
    def __init__(self) -> None:
        super().__init__(TIKTOK_SPEC)
