"""Golden set FB / X / YT — mở rộng lưới an toàn KPI 98% sang 3 platform còn lại (Phase 2, spec §10.5).

Mỗi ca là một tình huống nền tảng có thật, assert cứng để selector vỡ → test đỏ TRƯỚC khi KPI vỡ.
Hai ca cốt tử lặp lại cho MỌI platform (INV-1/INV-2):
  - login_wall      → INCONCLUSIVE + CHALLENGED (TUYỆT ĐỐI không DEAD)
  - missing_selector→ INCONCLUSIVE (TUYỆT ĐỐI không DEAD — "không thấy LIVE" ≠ chết)
  - captcha         → INCONCLUSIVE + BLOCKED (profile bị siết, chưa đọc được target)
Bao đủ loại target Excel yêu cầu: FB post/profile(group/page), X post/profile, YT video/channel.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from fastcheck_worker.browser.page_source import FakePageSource
from fastcheck_worker.contracts import Platform, ProfileHealth, UrlStatus
from fastcheck_worker.detectors import get_detector

from .fixture_server import FixtureServer


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    server = FixtureServer()
    url = server.start()
    try:
        yield url
    finally:
        server.stop()


def _detect(
    base: str, platform: Platform, fixture: str
) -> tuple[UrlStatus, ProfileHealth]:
    # Tải fixture platform-specific qua HTTP thật (status thật) → HtmlPageView → detector platform.
    source = FakePageSource()
    folder = platform.value.lower()
    page = source.open_page(f"{base}/{folder}/{fixture}.html", cookie="fake-cookie")
    result = get_detector(platform).detect(page)
    return result.url_status, result.profile_health


# (platform, fixture, expected_url_status, expected_profile_health|None)
_LIVE_CASES = [
    (Platform.FACEBOOK, "live"),
    (Platform.FACEBOOK, "group_live"),
    (Platform.FACEBOOK, "page_live"),
    (Platform.FACEBOOK, "reel_live"),
    (Platform.TWITTER, "live"),
    (Platform.TWITTER, "profile_live"),
    (Platform.YOUTUBE, "live"),
    (Platform.YOUTUBE, "channel_live"),
]

_DEAD_CASES = [
    (Platform.FACEBOOK, "dead_404"),
    (Platform.FACEBOOK, "soft404_200"),
    (Platform.FACEBOOK, "reel_dead"),  # reel sai/không tồn tại (FB tiếng Việt) — đã kiểm chứng thật
    (Platform.TWITTER, "dead_404"),
    (Platform.TWITTER, "soft404_200"),
    (Platform.YOUTUBE, "dead_404"),
    (Platform.YOUTUBE, "soft404_200"),
    (Platform.YOUTUBE, "video_dead"),  # video không tồn tại: player-error-overlay + "unavailable" (không LIVE giả)
]

_PLATFORMS = [Platform.FACEBOOK, Platform.TWITTER, Platform.YOUTUBE]


@pytest.mark.parametrize(("platform", "fixture"), _LIVE_CASES)
def test_live_targets_are_live(base_url: str, platform: Platform, fixture: str) -> None:
    url_status, health = _detect(base_url, platform, fixture)
    assert url_status == UrlStatus.LIVE, f"{platform.value}/{fixture} phải LIVE"
    assert health == ProfileHealth.OK


@pytest.mark.parametrize(("platform", "fixture"), _DEAD_CASES)
def test_dead_targets_are_dead(base_url: str, platform: Platform, fixture: str) -> None:
    # Gồm cả soft-404 (HTTP 200 nhưng nội dung "không tồn tại") — bắt bằng NỘI DUNG (INV-8).
    url_status, _ = _detect(base_url, platform, fixture)
    assert url_status == UrlStatus.DEAD, f"{platform.value}/{fixture} phải DEAD"


@pytest.mark.parametrize("platform", _PLATFORMS)
def test_login_wall_is_inconclusive_challenged_never_dead(
    base_url: str, platform: Platform
) -> None:
    # CA CỐT TỬ: cookie chết → INCONCLUSIVE + CHALLENGED. TUYỆT ĐỐI KHÔNG DEAD (INV-2).
    url_status, health = _detect(base_url, platform, "login_wall")
    assert url_status == UrlStatus.INCONCLUSIVE
    assert health == ProfileHealth.CHALLENGED


@pytest.mark.parametrize("platform", _PLATFORMS)
def test_missing_selector_is_inconclusive_never_dead(
    base_url: str, platform: Platform
) -> None:
    # CA CỐT TỬ chống selector vỡ: đã đăng nhập, không dấu hiệu chết, LIVE-selector không khớp →
    # INCONCLUSIVE (INV-1). "Không thấy tín hiệu LIVE" KHÔNG được thành DEAD.
    url_status, health = _detect(base_url, platform, "missing_selector")
    assert url_status == UrlStatus.INCONCLUSIVE
    assert health == ProfileHealth.OK


@pytest.mark.parametrize("platform", _PLATFORMS)
def test_captcha_is_inconclusive_blocked_never_dead(
    base_url: str, platform: Platform
) -> None:
    # Block/challenge bắt TRƯỚC guard/vote: INCONCLUSIVE + BLOCKED (chưa đọc được target — INV-3).
    url_status, health = _detect(base_url, platform, "captcha")
    assert url_status == UrlStatus.INCONCLUSIVE
    assert health == ProfileHealth.BLOCKED
