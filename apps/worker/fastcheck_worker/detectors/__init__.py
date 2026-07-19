"""Detector phân loại LIVE/DEAD/INCONCLUSIVE theo nền tảng (spec §6.5, skill platform-detector).

`base` giữ guard đăng nhập + vote engine dùng chung (không copy-paste giữa platform, INV-8).
Mỗi platform chỉ khai báo bảng tín hiệu (`SignalSpec`).
"""

from __future__ import annotations

from ..contracts import Platform
from .base import (
    BaseDetector,
    DetectResult,
    PageView,
    Signals,
    SignalSpec,
    collect_signals,
    verify_logged_in,
    vote_engine,
)
from .facebook import FACEBOOK_SPEC, FacebookDetector
from .tiktok import TIKTOK_SPEC, TikTokDetector
from .twitter import TWITTER_SPEC, TwitterDetector
from .youtube import YOUTUBE_SPEC, YouTubeDetector

# Registry detector theo platform. Thêm platform mới = thêm một entry (mỗi platform 1 SignalSpec).
_DETECTORS: dict[Platform, BaseDetector] = {
    Platform.TIKTOK: TikTokDetector(),
    Platform.FACEBOOK: FacebookDetector(),
    Platform.TWITTER: TwitterDetector(),
    Platform.YOUTUBE: YouTubeDetector(),
}


def get_detector(platform: Platform) -> BaseDetector:
    """Trả detector cho platform. Ném KeyError nếu chưa hỗ trợ (fail loud, không đoán — INV-1)."""
    return _DETECTORS[platform]


__all__ = [
    "FACEBOOK_SPEC",
    "TIKTOK_SPEC",
    "TWITTER_SPEC",
    "YOUTUBE_SPEC",
    "BaseDetector",
    "DetectResult",
    "FacebookDetector",
    "PageView",
    "Signals",
    "SignalSpec",
    "TikTokDetector",
    "TwitterDetector",
    "YouTubeDetector",
    "collect_signals",
    "get_detector",
    "verify_logged_in",
    "vote_engine",
]
