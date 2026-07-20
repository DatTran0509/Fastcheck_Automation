"""Trang CHƯA render (JS/asset không tải — vd X ChunkLoadError do IP bị chặn) phải được BÁO RÕ, không lẫn
với 'no_decisive_signal'. Đã đăng nhập (guard qua DOM) + trang gần trắng + không tín hiệu live/dead/block →
INCONCLUSIVE + THROTTLED (lỗi hạ tầng, không phạt tài khoản) + block_reason 'page_not_rendered'.
"""

from __future__ import annotations

from fastcheck_worker.contracts import Platform, ProfileHealth, UrlStatus
from fastcheck_worker.detectors import get_detector
from fastcheck_worker.detectors.html_view import HtmlPageView


def test_x_shell_only_is_page_not_rendered() -> None:
    # Chỉ có phần tử guard (đã đăng nhập) — không tweet, không lỗi, body gần như trắng (shell X + logo SVG).
    html = '<html><body><div data-testid="SideNav_AccountSwitcher_Button"></div></body></html>'
    page = HtmlPageView(html, 200, "https://x.com/user/status/123")
    result = get_detector(Platform.TWITTER).detect(page)
    assert result.url_status == UrlStatus.INCONCLUSIVE  # KHÔNG DEAD (INV-1)
    assert result.profile_health == ProfileHealth.THROTTLED  # hạ tầng, không kết tội tài khoản
    assert result.block_reason is not None and "page_not_rendered" in result.block_reason


def test_rendered_but_ambiguous_stays_no_decisive_signal() -> None:
    # Trang render ĐỦ text nhưng không có tín hiệu quyết định → giữ no_decisive_signal (không nhầm 'chưa render').
    body = "This is a long enough page body with plenty of visible text but no decisive tweet or error marker."
    html = f'<html><body><div data-testid="SideNav_AccountSwitcher_Button"></div><p>{body}</p></body></html>'
    page = HtmlPageView(html, 200, "https://x.com/user/status/123")
    result = get_detector(Platform.TWITTER).detect(page)
    assert result.url_status == UrlStatus.INCONCLUSIVE
    assert result.profile_health == ProfileHealth.OK
    assert result.block_reason == "no_decisive_signal"
