"""Detector Facebook — bảng tín hiệu khởi đầu (spec §10.5, skill platform-detector).

Bao các loại target Excel yêu cầu: **post / profile / group / page**. CHỈ khai báo dữ liệu tín hiệu;
guard + block + vote 3 nhánh nằm ở `base.BaseDetector` (không copy-paste — INV-8). Bảng là ĐIỂM KHỞI
ĐẦU: FB đổi cơ chế liên tục + DOM rối rắm/obfuscated → dựa aria-label/role bền + soft-404 theo NỘI DUNG,
golden set + alert bắt khi selector vỡ (docs/anti-patterns.md).

Lưu ý ngữ nghĩa (INV-1): "content isn't available" của FB có thể là privacy-restricted (không phải chết).
Chỉ những chuỗi FB dùng cho "không tồn tại" rõ ràng mới tính phiếu DEAD; mơ hồ → để INCONCLUSIVE.
"""

from __future__ import annotations

from .base import BaseDetector, SignalSpec

FACEBOOK_SPEC = SignalSpec(
    # Đã đăng nhập: nút tài khoản/profile ở banner. CHỈ selector đơn (fake↔real khớp nhau — không combinator).
    login_selectors=(
        '[aria-label="Your profile"]',
        '[aria-label="Account"]',
        '[aria-label="Account controls and settings"]',
    ),
    login_url_markers=("login", "checkpoint"),
    # Cookie session FB (đã kiểm chứng trên GemLogin thật): c_user (user id) + xs (session secret). Locale-
    # independent — guard chắc chắn cho FB tiếng Việt/SPA nơi selector DOM không đáng tin (INV-2/INV-8).
    auth_cookies=("c_user", "xs"),
    # LIVE: bài viết/nội dung profile/page/group render. role=article là bài post; data-pagelet là khối nội dung.
    live_selectors=(
        '[role="article"]',
        '[data-pagelet^="ProfileTimeline"]',
        '[data-pagelet^="GroupFeed"]',
        '[data-pagelet="page"]',
        '[data-pagelet*="Feed"]',
        # Reel/Video (đã kiểm chứng DOM thật): container "Reels"/"ReelsUFIBar" + video đã render.
        '[data-pagelet^="Reels"]',
        'div[data-video-id]',
    ),
    # DEAD qua DOM: khối báo lỗi/không khả dụng của FB (khi có).
    dead_selectors=(
        '[data-pagelet="ErrorCard"]',
        '#content .UIFullPage_Container',
    ),
    # DEAD qua NỘI DUNG (soft-404 — HTTP 200 vẫn có thể là chết). Chọn chuỗi FB dùng cho "không tồn tại".
    dead_texts=(
        "this page isn't available",
        "this content isn't available right now",
        "the link you followed may be broken",
        "the page you requested cannot be displayed",
        "this page isn't available right now",
        "page not found",
        "this account has been deleted",
        "content not found",
        # FB tiếng Việt (đã kiểm chứng thật trên reel không tồn tại): trang không hiển thị / nội dung không có.
        "trang này hiện không hiển thị",
        "nội dung này hiện không có",
        "liên kết bạn nhấp vào có thể bị hỏng",
    ),
    # BLOCKED/CHALLENGE: checkpoint xác minh danh tính / captcha.
    block_selectors=(
        'form[action*="checkpoint"]',
        '[name="captcha_response"]',
        '#captcha',
        'input[name="captcha_persist_data"]',
    ),
    block_texts=(
        "confirm your identity",
        "we need to confirm",
        "checkpoint required",
        "you're temporarily blocked",
        "suspicious activity",
        "enter the code we sent",
    ),
)


class FacebookDetector(BaseDetector):
    def __init__(self) -> None:
        super().__init__(FACEBOOK_SPEC)
