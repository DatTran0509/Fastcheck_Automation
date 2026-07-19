"""Test B — golden set TikTok: load fixture qua static server THẬT, assert từng ca.

Đây là lưới an toàn KPI 98% (spec §6.5). Mỗi fixture là một tình huống nền tảng có thật; assert
cứng để khi TikTok đổi cơ chế (selector vỡ), test đỏ TRƯỚC khi KPI vỡ. Hai ca cốt tử:
  - login_wall → INCONCLUSIVE + CHALLENGED (TUYỆT ĐỐI không DEAD)
  - missing_selector → INCONCLUSIVE (TUYỆT ĐỐI không DEAD)
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from fastcheck_worker.browser.page_source import FakePageSource
from fastcheck_worker.contracts import Platform, ProfileHealth, UrlStatus
from fastcheck_worker.detectors import get_detector
from fastcheck_worker.runner import run_check

from .fixture_server import FixtureServer


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    server = FixtureServer()
    url = server.start()
    try:
        yield url
    finally:
        server.stop()


def _detect(base: str, fixture: str) -> tuple[UrlStatus, ProfileHealth]:
    # Tải fixture qua HTTP thật (status thật) → HtmlPageView → detector TikTok.
    source = FakePageSource()
    page = source.open_page(f"{base}/{fixture}", cookie="fake-cookie")
    result = get_detector(Platform.TIKTOK).detect(page)
    return result.url_status, result.profile_health


def test_live(base_url: str) -> None:
    url_status, health = _detect(base_url, "live.html")
    assert url_status == UrlStatus.LIVE
    assert health == ProfileHealth.OK


def test_profile_live(base_url: str) -> None:
    # Trang PROFILE (@user) sống — DOM khác trang video (đã kiểm chứng thật): stats + follow + grid.
    url_status, health = _detect(base_url, "profile_live.html")
    assert url_status == UrlStatus.LIVE
    assert health == ProfileHealth.OK


def test_dead_404(base_url: str) -> None:
    url_status, _ = _detect(base_url, "dead_404.html")
    assert url_status == UrlStatus.DEAD


def test_video_notfound_is_dead_not_live(base_url: str) -> None:
    # Video id SAI (đã kiểm chứng thật): thẻ <video> shell KHÔNG được LIVE giả; text VN "Video hiện không
    # khả dụng" → DEAD. Đây là ca chống LIVE-giả bạn báo (id video 20 chữ số vẫn ra LIVE).
    url_status, health = _detect(base_url, "video_notfound.html")
    assert url_status == UrlStatus.DEAD, "video sai phải DEAD, KHÔNG LIVE"
    assert health == ProfileHealth.OK


def test_account_notfound_is_dead(base_url: str) -> None:
    # Tài khoản @user SAI (đã kiểm chứng thật): khung [data-e2e=user-page] KHÔNG LIVE giả; text VN
    # "Không thể tìm thấy tài khoản này" → DEAD (không còn INCONCLUSIVE→DLQ).
    url_status, health = _detect(base_url, "account_notfound.html")
    assert url_status == UrlStatus.DEAD, "tài khoản sai phải DEAD"
    assert health == ProfileHealth.OK


def test_soft404_200_is_dead_not_live_or_inconclusive(base_url: str) -> None:
    # HTTP 200 nhưng nội dung "không tồn tại" → DEAD (không được LIVE/INCONCLUSIVE).
    url_status, _ = _detect(base_url, "soft404_200.html")
    assert url_status == UrlStatus.DEAD


def test_login_wall_is_inconclusive_challenged_never_dead(base_url: str) -> None:
    # CA CỐT TỬ: cookie chết → INCONCLUSIVE + CHALLENGED. TUYỆT ĐỐI KHÔNG DEAD (INV-2).
    url_status, health = _detect(base_url, "login_wall.html")
    assert url_status == UrlStatus.INCONCLUSIVE
    assert url_status != UrlStatus.DEAD
    assert health == ProfileHealth.CHALLENGED


def test_captcha_is_inconclusive_blocked(base_url: str) -> None:
    url_status, health = _detect(base_url, "captcha.html")
    assert url_status == UrlStatus.INCONCLUSIVE
    assert health == ProfileHealth.BLOCKED


def test_rate_limited_page_is_blocked_not_dead(base_url: str) -> None:
    # "Page not available / try again later" = TikTok CHẶN/giới hạn TẠM THỜI (đã kiểm chứng thật) → BLOCKED
    # (profile bị siết → cooldown + xoay proxy), KHÔNG DEAD (target chưa đọc được), KHÔNG OK (đừng retry-hammer).
    url_status, health = _detect(base_url, "rate_limited.html")
    assert url_status == UrlStatus.INCONCLUSIVE
    assert health == ProfileHealth.BLOCKED


def test_missing_selector_is_inconclusive_never_dead(base_url: str) -> None:
    # CA CỐT TỬ chống selector vỡ: "không thấy tín hiệu LIVE" ≠ DEAD → INCONCLUSIVE (INV-1/INV-8).
    url_status, _ = _detect(base_url, "missing_selector.html")
    assert url_status == UrlStatus.INCONCLUSIVE
    assert url_status != UrlStatus.DEAD


# Chạy qua process pool runner (đúng đường thực thi INV-10) để chắc pipeline khớp golden.
def test_run_check_via_pool_path_matches_golden(base_url: str) -> None:
    outcome = run_check(
        {
            "platform": Platform.TIKTOK.value,
            "target_url": f"{base_url}/live.html",
            "cookie": "fake-cookie",
            "fixture_base_url": None,
            "gemlogin_profile_id": None,
        }
    )
    assert outcome["url_status"] == UrlStatus.LIVE.value
    assert outcome["profile_health"] == ProfileHealth.OK.value
