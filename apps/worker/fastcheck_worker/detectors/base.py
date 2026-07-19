"""Base detector: guard đăng nhập + vote engine dùng chung (spec §6.5, skill platform-detector).

Đây là phần quyết định KPI chính xác 98%. Luật vàng, không thoả hiệp:
  * INV-1: ba nhánh {LIVE, DEAD, INCONCLUSIVE}. KHÔNG có `else DEAD`. Không rõ → INCONCLUSIVE.
  * INV-2: guard đăng nhập chạy TRƯỚC khi kết luận về target. Chưa login = lỗi profile = INCONCLUSIVE.
  * INV-3: trả `url_status` (target) TÁCH BIỆT `profile_health` (profile).
  * INV-8: vote đa tín hiệu (HTTP status + DOM + URL cuối), selector bền + fallback; soft-404 bắt bằng nội dung.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

from ..contracts import ProfileHealth, UrlStatus

logger = logging.getLogger("fastcheck.worker.detector")


class PageView(Protocol):
    """Nguồn tín hiệu detector đọc. Hiện thực: `HtmlPageView` (test/fake) và DrissionPage (Phase sau)."""

    @property
    def http_status(self) -> int | None: ...

    @property
    def final_url(self) -> str: ...

    def has_element(self, *selectors: str) -> bool: ...

    def text_contains(self, *needles: str) -> bool: ...

    def cookie_names(self) -> set[str]:
        """Tên các cookie hiện có (KHÔNG giá trị — INV-12). Browser thật trả cookie thật; fake trả rỗng."""
        ...


@dataclass(frozen=True)
class SignalSpec:
    """Bảng tín hiệu một platform (spec §10.5). Điểm khởi đầu — PHẢI health-check định kỳ."""

    # Guard đăng nhập (INV-2): avatar/menu đặc trưng của phiên đã đăng nhập. Bền + fallback.
    login_selectors: tuple[str, ...]
    # Redirect tới trang login = chưa đăng nhập (cookie chết) → lỗi profile.
    # So khớp theo TỪNG SEGMENT của path (không phải substring) để không dính nhầm tên file/khác.
    login_url_markers: tuple[str, ...]
    # Tín hiệu LIVE của target (player/nội dung). Bền + fallback.
    live_selectors: tuple[str, ...]
    # Tín hiệu DEAD qua DOM (khi có phần tử báo lỗi rõ ràng).
    dead_selectors: tuple[str, ...]
    # Tín hiệu DEAD qua NỘI DUNG — bắt soft-404 (HTTP 200 nhưng "không tồn tại"), INV-8.
    dead_texts: tuple[str, ...]
    # Tín hiệu block/challenge (captcha/turnstile) → profile BLOCKED, KHÔNG kết luận target.
    block_selectors: tuple[str, ...]
    block_texts: tuple[str, ...]
    # HTTP status coi là chết chắc chắn.
    dead_http_statuses: tuple[int, ...] = (404, 410)
    # Cookie đăng nhập cốt lõi (INV-2/INV-8): tín hiệu đã-đăng-nhập MẠNH nhất, locale-independent. Nền tảng
    # SPA (FB) đổi DOM + đa ngôn ngữ khiến selector DOM không đáng tin; cookie session thì chắc chắn. Cần ĐỦ
    # (all) các cookie này để coi là đã đăng nhập (tránh dương tính giả). Rỗng = chỉ dựa DOM (fake/golden).
    auth_cookies: tuple[str, ...] = ()


@dataclass(frozen=True)
class Signals:
    """Tín hiệu thô đã thu, đầu vào cho vote engine. Đã tách rõ từng chiều để vote minh bạch."""

    http_status: int | None
    final_url: str
    logged_in: bool
    dom_live: bool
    dom_dead: bool
    dom_block: bool


@dataclass(frozen=True)
class DetectResult:
    """Kết quả detector. url_status (target) TÁCH BIỆT profile_health (profile) — INV-3."""

    url_status: UrlStatus
    profile_health: ProfileHealth
    block_reason: str | None = None
    response_time_ms: int | None = None


def _redirected_to_login(url: str, markers: tuple[str, ...]) -> bool:
    """True nếu path của URL có một SEGMENT trùng marker (vd `/login`), không phải substring.

    Dùng segment để `/login_wall.html` (tên file fixture) KHÔNG bị coi là redirect login —
    guard khi đó phải dựa vào việc thiếu avatar (tín hiệu thật), không phải trùng chuỗi tình cờ.
    """
    if not markers:
        return False
    segments = {seg.lower() for seg in urlparse(url).path.split("/") if seg}
    return any(m.lower() in segments for m in markers)


def _page_cookie_names(page: PageView) -> set[str]:
    """Đọc tên cookie best-effort (browser thật). Fake/test double thiếu method → rỗng (dùng DOM fallback)."""
    getter = getattr(page, "cookie_names", None)
    if getter is None:
        return set()
    try:
        return set(getter())
    except Exception:  # noqa: BLE001 — đọc cookie best-effort; lỗi → coi như không có, KHÔNG chặn
        return set()


def verify_logged_in(page: PageView, spec: SignalSpec) -> bool:
    """INV-2: đã đăng nhập nếu (cookie session đủ) HOẶC (thấy avatar/menu) VÀ không bị đẩy về trang login.

    Đây là chốt chặn hỏng âm thầm quan trọng nhất: cookie chết → trang guest → nếu bỏ guard,
    detector đọc trang guest và báo DEAD sai (anti-patterns §1).

    Ưu tiên COOKIE (INV-8): nền tảng SPA đa ngôn ngữ (FB tiếng Việt...) đổi/ẩn selector DOM khiến guard DOM
    dương tính giả 'chưa đăng nhập'; cookie session (c_user/xs, sessionid, auth_token...) là tín hiệu chắc
    chắn, không phụ thuộc ngôn ngữ/layout. Đủ cookie cốt lõi → đã đăng nhập. Không có cookie → fallback DOM.
    """
    if _redirected_to_login(page.final_url, spec.login_url_markers):
        return False
    if spec.auth_cookies:
        names = _page_cookie_names(page)
        # Cần ĐỦ (all) cookie cốt lõi để tránh dương tính giả (cookie sót lại sau khi hết phiên).
        if names and all(c in names for c in spec.auth_cookies):
            return True
    return page.has_element(*spec.login_selectors)


def collect_signals(page: PageView, spec: SignalSpec) -> Signals:
    """Thu đủ tín hiệu từ page theo bảng tín hiệu. Không kết luận gì ở đây."""
    return Signals(
        http_status=page.http_status,
        final_url=page.final_url,
        logged_in=verify_logged_in(page, spec),
        dom_live=page.has_element(*spec.live_selectors),
        # soft-404: DOM báo lỗi HOẶC nội dung "không tồn tại" (INV-8 — không chỉ HTTP status).
        dom_dead=page.has_element(*spec.dead_selectors) or page.text_contains(*spec.dead_texts),
        dom_block=page.has_element(*spec.block_selectors) or page.text_contains(*spec.block_texts),
    )


def vote_engine(signals: Signals, spec: SignalSpec) -> UrlStatus:
    """Vote đa tín hiệu → LIVE / DEAD / INCONCLUSIVE (INV-1, INV-8). CHỈ gọi khi profile khoẻ.

    Đếm phiếu độc lập từ nhiều tín hiệu thay vì dựa một selector:
      DEAD  : HTTP status chết  |  DOM/nội dung báo "không tồn tại" (soft-404)
      LIVE  : DOM target hiển thị (player/nội dung)
    Chỉ kết luận khi phiếu MỘT CHIỀU rõ ràng. Mâu thuẫn hoặc không có phiếu nào → INCONCLUSIVE.
    TUYỆT ĐỐI không có nhánh `else DEAD` — "không thấy tín hiệu" ≠ chết (INV-1).
    """
    dead_votes = 0
    live_votes = 0

    if signals.http_status is not None and signals.http_status in spec.dead_http_statuses:
        dead_votes += 1
    if signals.dom_dead:  # soft-404 bắt bằng nội dung, không chỉ status
        dead_votes += 1
    if signals.dom_live:
        live_votes += 1

    if dead_votes > 0 and live_votes == 0:
        return UrlStatus.DEAD
    if live_votes > 0 and dead_votes == 0:
        return UrlStatus.LIVE
    # Mâu thuẫn (vừa live vừa dead) hoặc KHÔNG tín hiệu nào (selector vỡ) → INCONCLUSIVE.
    return UrlStatus.INCONCLUSIVE


class BaseDetector:
    """Detector chung: guard trước, block trước, rồi mới vote target. Mỗi platform chỉ khác `spec`."""

    def __init__(self, spec: SignalSpec) -> None:
        self.spec = spec

    def detect(self, page: PageView, response_time_ms: int | None = None) -> DetectResult:
        try:
            signals = collect_signals(page, self.spec)
        except Exception as exc:  # noqa: BLE001 — phân loại & báo ra, không nuốt
            # INV-1: lỗi khi ĐỌC tín hiệu KHÔNG BAO GIỜ thành DEAD. Báo INCONCLUSIVE, profile chưa kết tội.
            logger.warning("lỗi thu tín hiệu (%s) → INCONCLUSIVE", type(exc).__name__)
            return DetectResult(
                url_status=UrlStatus.INCONCLUSIVE,
                profile_health=ProfileHealth.OK,
                block_reason=f"detector_error:{type(exc).__name__}",
                response_time_ms=response_time_ms,
            )

        # ── Phase A: cổng sức khoẻ profile — CHẠY TRƯỚC mọi kết luận về target (INV-2) ──
        # block/challenge trước guard: captcha cũng làm avatar biến mất, nhưng nó là BLOCKED
        # (profile bị siết), không phải CHALLENGED (cookie chết). Phân biệt để chẩn đoán đúng (INV-3).
        if signals.dom_block:
            return DetectResult(
                url_status=UrlStatus.INCONCLUSIVE,  # KHÔNG DEAD — chưa hề đọc được target
                profile_health=ProfileHealth.BLOCKED,
                block_reason="captcha_or_challenge",
                response_time_ms=response_time_ms,
            )
        if not signals.logged_in:
            # Cookie chết / login wall = lỗi profile → INCONCLUSIVE + CHALLENGED (INV-2), KHÔNG DEAD.
            return DetectResult(
                url_status=UrlStatus.INCONCLUSIVE,
                profile_health=ProfileHealth.CHALLENGED,
                block_reason="login_guard_failed",
                response_time_ms=response_time_ms,
            )

        # ── Phase B: vote target (chỉ khi profile khoẻ) — INV-1, INV-8 ──
        url_status = vote_engine(signals, self.spec)
        block_reason = None if url_status != UrlStatus.INCONCLUSIVE else "no_decisive_signal"
        return DetectResult(
            url_status=url_status,
            profile_health=ProfileHealth.OK,
            block_reason=block_reason,
            response_time_ms=response_time_ms,
        )
