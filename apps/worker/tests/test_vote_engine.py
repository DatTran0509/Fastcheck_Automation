"""Test A — unit vote engine + guard/block ordering (Test A của Phase 1).

Kiểm mỗi tổ hợp tín hiệu ra đúng nhánh; đặc biệt: THIẾU tín hiệu → INCONCLUSIVE (không bao giờ DEAD).
Đây là lõi quyết định KPI 98% nên test thẳng vào `vote_engine` + `BaseDetector.detect` với tín hiệu
tổng hợp (không cần HTML), tách bạch khỏi I/O.
"""

from __future__ import annotations

from fastcheck_worker.contracts import ProfileHealth, UrlStatus
from fastcheck_worker.detectors import TIKTOK_SPEC, TikTokDetector
from fastcheck_worker.detectors.base import BaseDetector, PageView, Signals, vote_engine


def _sig(
    *,
    http: int | None = 200,
    live: bool = False,
    dead: bool = False,
) -> Signals:
    return Signals(
        http_status=http,
        final_url="https://www.tiktok.com/@u/video/1",
        logged_in=True,
        dom_live=live,
        dom_dead=dead,
        dom_block=False,
    )


# ── vote_engine: từng tổ hợp tín hiệu → đúng nhánh ────────────────────────────
def test_live_dom_only_is_live() -> None:
    assert vote_engine(_sig(http=200, live=True), TIKTOK_SPEC) == UrlStatus.LIVE


def test_http_404_is_dead() -> None:
    assert vote_engine(_sig(http=404), TIKTOK_SPEC) == UrlStatus.DEAD


def test_soft_404_content_is_dead() -> None:
    # HTTP 200 nhưng nội dung báo chết → DEAD (bắt bằng nội dung, INV-8).
    assert vote_engine(_sig(http=200, dead=True), TIKTOK_SPEC) == UrlStatus.DEAD


def test_no_signal_is_inconclusive() -> None:
    # THIẾU tín hiệu (selector vỡ): không live, không dead, http 200 → INCONCLUSIVE (INV-1).
    assert vote_engine(_sig(http=200, live=False, dead=False), TIKTOK_SPEC) == UrlStatus.INCONCLUSIVE


def test_conflicting_signals_is_inconclusive() -> None:
    # Vừa có tín hiệu live vừa có tín hiệu dead (mâu thuẫn) → INCONCLUSIVE, KHÔNG đoán DEAD.
    assert vote_engine(_sig(http=200, live=True, dead=True), TIKTOK_SPEC) == UrlStatus.INCONCLUSIVE


def test_missing_http_status_still_not_dead() -> None:
    # http_status=None + không tín hiệu → INCONCLUSIVE (không coi vắng status là chết).
    assert vote_engine(_sig(http=None, live=False, dead=False), TIKTOK_SPEC) == UrlStatus.INCONCLUSIVE


def test_dead_dom_with_live_dom_conflict_inconclusive() -> None:
    assert vote_engine(_sig(http=404, live=True), TIKTOK_SPEC) == UrlStatus.INCONCLUSIVE


# ── BaseDetector.detect: guard & block chạy TRƯỚC vote target ─────────────────
class _FakePage(PageView):
    def __init__(
        self,
        *,
        http: int | None,
        final_url: str,
        elements: set[str],
        text: str = "",
    ) -> None:
        self._http = http
        self._final_url = final_url
        self._elements = elements
        self._text = text.lower()

    @property
    def http_status(self) -> int | None:
        return self._http

    @property
    def final_url(self) -> str:
        return self._final_url

    def has_element(self, *selectors: str) -> bool:
        return any(s in self._elements for s in selectors)

    def text_contains(self, *needles: str) -> bool:
        return any(n.lower() in self._text for n in needles)


def test_block_beats_guard_and_target() -> None:
    # Có captcha → BLOCKED + INCONCLUSIVE, kể cả khi có tín hiệu live (không đọc target khi bị siết).
    page = _FakePage(
        http=200,
        final_url="https://www.tiktok.com/@u/video/1",
        elements={"#captcha-verify-container", "video"},
    )
    result = TikTokDetector().detect(page)
    assert result.url_status == UrlStatus.INCONCLUSIVE
    assert result.profile_health == ProfileHealth.BLOCKED


def test_guard_fail_is_challenged_not_dead() -> None:
    # Không avatar + có nội dung "chết"-giả → guard fail TRƯỚC → CHALLENGED, KHÔNG DEAD (INV-2).
    page = _FakePage(
        http=200,
        final_url="https://www.tiktok.com/@u/video/1",
        elements=set(),
        text="video currently unavailable",
    )
    result = TikTokDetector().detect(page)
    assert result.url_status == UrlStatus.INCONCLUSIVE
    assert result.profile_health == ProfileHealth.CHALLENGED


def test_detect_never_raises_returns_inconclusive() -> None:
    # Page ném lỗi khi đọc → detector nuốt-có-báo, trả INCONCLUSIVE (INV-1), không để lỗi bung ra.
    class _Boom(PageView):
        @property
        def http_status(self) -> int | None:
            return 200

        @property
        def final_url(self) -> str:
            return "https://www.tiktok.com/@u/video/1"

        def has_element(self, *selectors: str) -> bool:
            raise RuntimeError("dom exploded")

        def text_contains(self, *needles: str) -> bool:
            return False

    result = BaseDetector(TIKTOK_SPEC).detect(_Boom())
    assert result.url_status == UrlStatus.INCONCLUSIVE
