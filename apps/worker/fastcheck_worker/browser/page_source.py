"""Nguồn trang cho detector.

`FakePageSource` là bản thay thế dev/test cho chuỗi GemLogin + DrissionPage (GEMLOGIN_MODE=fake):
tải trang qua HTTP (urllib, stdlib) rồi dựng `HtmlPageView`. Nó GIỮ ĐÚNG thứ tự bắt buộc của
INV-2 (§6.8e): **inject cookie TRƯỚC khi điều hướng**. Adapter thật (DrissionPage attach CDP
GemLogin) hiện thực cùng interface `open_page` ở Phase sau (ADR-0006).

Không log cookie/credential (INV-12): chỉ log độ dài, không log giá trị.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from ..detectors.base import PageView
from ..detectors.html_view import HtmlPageView
from .cookies import parse_cookies

if TYPE_CHECKING:  # tránh import DrissionPage khi chạy fake/CI (không có Chromium)
    from DrissionPage import ChromiumPage

logger = logging.getLogger("fastcheck.worker.page")

# Ánh xạ token trong URL nền tảng → file fixture (CHỈ dùng ở fake mode để chạy end-to-end
# không cần TikTok thật). Token nằm ở path/query của URL check.
_FIXTURE_TOKENS: dict[str, str] = {
    "live": "live.html",
    "dead": "dead_404.html",
    "soft404": "soft404_200.html",
    "loginwall": "login_wall.html",
    "captcha": "captcha.html",
    "missing": "missing_selector.html",
    # `flaky`: server trả captcha (BLOCKED) vài lần đầu rồi LIVE — để test auto-switch phục hồi (Phase 3).
    "flaky": "flaky.html",
    # `slow`: server trả LIVE sau độ trễ dài — để job ở trạng thái RUNNING đủ lâu mà kill/bounce giữa
    # chừng (test thu hồi khi station chết + reconnect — Phase 4).
    "slow": "slow.html",
}


class FakePageSource:
    """Mở "trang" ở fake mode. Tuỳ chọn `fixture_base_url` để map URL nền tảng → fixture server."""

    def __init__(self, fixture_base_url: str | None = None, timeout_seconds: float = 15.0) -> None:
        self._fixture_base_url = fixture_base_url.rstrip("/") if fixture_base_url else None
        self._timeout = timeout_seconds

    def open_page(self, target_url: str, cookie: str) -> PageView:
        # INV-2 / §6.8e: cookie đi TRƯỚC điều hướng. Fake mode chưa có browser để nạp thật,
        # nhưng giữ đúng thứ tự để đường thật (Phase sau) chỉ việc thay bước inject.
        self._inject_cookie_before_navigate(cookie)
        url = self._resolve(target_url)
        return self._fetch(url)

    def _inject_cookie_before_navigate(self, cookie: str) -> None:
        # KHÔNG log giá trị cookie (INV-12) — chỉ độ dài để debug được mà không rò tài sản nhạy cảm.
        logger.debug("inject cookie trước điều hướng (len=%d)", len(cookie or ""))

    def _resolve(self, target_url: str) -> str:
        # Golden test truyền thẳng URL fixture (.html) → dùng nguyên.
        if not self._fixture_base_url or target_url.lower().endswith(".html"):
            return target_url
        # E2E fake: map URL TikTok (chứa token) → file fixture trên fixture server.
        parsed = urlparse(target_url)
        haystack = f"{parsed.path} {parsed.query}".lower()
        for token, filename in _FIXTURE_TOKENS.items():
            if token in haystack:
                return f"{self._fixture_base_url}/{filename}"
        # Không nhận ra token: vẫn tải URL gốc (sẽ thành INCONCLUSIVE nếu không phải HTML hợp lệ).
        return target_url

    def _fetch(self, url: str) -> HtmlPageView:
        req = urllib.request.Request(url, headers={"User-Agent": "fastcheck-fake/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 (fake mode)
                body = resp.read().decode("utf-8", errors="replace")
                return HtmlPageView(body, resp.status, resp.geturl())
        except urllib.error.HTTPError as exc:
            # 4xx/5xx: VẪN có body (soft/hard 404 có nội dung) → đọc để vote, giữ nguyên status.
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            return HtmlPageView(body, exc.code, url)


# ── Real: DrissionPage attach CDP GemLogin (GEMLOGIN_MODE=real) ──────────────────
class DrissionPageView:
    """`PageView` đọc DOM/text từ browser THẬT (DrissionPage attach CDP GemLogin).

    Cùng interface với `HtmlPageView` nên detector KHÔNG cần biết nguồn DOM đến từ đâu (ADR-0006).
    Query selector phòng thủ: selector không hợp lệ CSS (vd giá trị chứa dấu chấm không trích dẫn) →
    coi như KHÔNG khớp, KHÔNG ném (tránh một selector giòn làm hỏng cả detect — INV-8).
    """

    def __init__(self, page: ChromiumPage, http_status: int | None, final_url: str, body_text: str) -> None:
        self._page = page
        self._http_status = http_status
        self._final_url = final_url
        self._text = body_text.lower()

    @property
    def http_status(self) -> int | None:
        return self._http_status

    @property
    def final_url(self) -> str:
        return self._final_url

    def has_element(self, *selectors: str) -> bool:
        """True nếu BẤT KỲ selector CSS nào khớp (fallback bền — INV-8). timeout=0: không chờ (đã load)."""
        for sel in selectors:
            try:
                if self._page.ele(f"css:{sel}", timeout=0):
                    return True
            except Exception as exc:  # noqa: BLE001 — selector giòn không được làm hỏng detect
                logger.debug("selector %r lỗi trên DrissionPage (%s) — coi như không khớp", sel, type(exc).__name__)
        return False

    def text_contains(self, *needles: str) -> bool:
        return any(n.lower() in self._text for n in needles)

    def cookie_names(self) -> set[str]:
        """Tên cookie hiện tại của browser thật (INV-12: CHỈ tên, không giá trị). Guard cookie dựa vào đây."""
        try:
            return {str(c.get("name")) for c in self._page.cookies() if c.get("name")}
        except Exception as exc:  # noqa: BLE001 — đọc cookie best-effort; lỗi → rỗng, guard fallback DOM
            logger.debug("đọc tên cookie lỗi (%s)", type(exc).__name__)
            return set()


class DrissionPageSource:
    """Mở trang THẬT: attach vào CDP address GemLogin phơi ra, nạp cookie TRƯỚC điều hướng (INV-2).

    Vòng đời browser do GemLogin quản (đóng qua adapter.close_browser) — source KHÔNG `.quit()` browser.
    """

    def __init__(
        self,
        cdp_address: str,
        load_timeout_seconds: float = 30.0,
        render_settle_seconds: float = 3.0,
    ) -> None:
        self._cdp_address = cdp_address
        self._timeout = load_timeout_seconds
        # Chờ SPA render client-side sau `load` trước khi CHỤP body_text. FB/TikTok/YouTube render nội dung
        # (kể cả chữ "video không khả dụng") bằng JS SAU load event → chụp ngay = trắng → INCONCLUSIVE oan +
        # retry→DLQ. Settle ngắn để tín hiệu (live/dead) kịp hiện. (đã kiểm chứng thật: TikTok id sai.)
        self._settle = render_settle_seconds
        self._page: ChromiumPage | None = None

    def open_page(self, target_url: str, cookie: str) -> PageView:
        from DrissionPage import ChromiumOptions, ChromiumPage  # noqa: PLC0415 — chỉ real mode

        options = ChromiumOptions().set_address(self._cdp_address)
        page = ChromiumPage(options)
        self._page = page
        # eager = chờ DOMContentLoaded (KHÔNG chờ toàn bộ tài nguyên). SPA (TikTok/FB/YT) có kết nối bền
        # (websocket/long-poll) → 'load' rất lâu mới xong → normal + timeout → page.get TỰ RETRY = RELOAD liên
        # tục, trang chưa render đã reload → bug. eager trả sớm khi DOM sẵn, rồi settle chờ JS render.
        page.set.load_mode.eager()
        # INV-2 / §6.8e: cookie đi TRƯỚC điều hướng — set.cookies rồi mới .get(url).
        self._inject_cookie_before_navigate(page, cookie, target_url)
        http_status = self._navigate_capture_status(page, target_url)
        # Chờ JS render xong rồi mới chụp text (SPA) — tránh chụp trang trắng → no_decisive_signal oan.
        if self._settle > 0:
            time.sleep(self._settle)
        body_text = self._body_text(page)
        return DrissionPageView(page, http_status, page.url, body_text)

    def close(self) -> None:
        # KHÔNG quit browser (GemLogin quản vòng đời — adapter.close_browser lo). Chỉ ngắt tham chiếu.
        self._page = None

    def cookies_string(self) -> str:
        """Xuất cookie hiện tại (JSON đầy đủ trường) để refresh session sau phiên OK (spec §4.4). KHÔNG log giá trị."""
        if self._page is None:
            return ""
        try:
            # DrissionPage 4.x: page.cookies() (KHÔNG có tham số as_dict) → list dict {name,value,domain,...}.
            cookies = [dict(c) for c in self._page.cookies()]
            return json.dumps(cookies)
        except Exception as exc:  # noqa: BLE001 — thu cookie best-effort, không chặn kết quả
            logger.debug("đọc cookie để refresh lỗi (%s)", type(exc).__name__)
            return ""

    def _inject_cookie_before_navigate(self, page: ChromiumPage, cookie: str, target_url: str) -> None:
        # KHÔNG log giá trị cookie (INV-12) — chỉ độ dài.
        logger.debug("inject cookie trước điều hướng (len=%d)", len(cookie or ""))
        if not cookie or not cookie.strip():
            return
        cookies = self._parse_cookie(cookie.strip(), target_url)
        if cookies:
            try:
                page.set.cookies(cookies)
            except Exception as exc:  # noqa: BLE001 — cookie hỏng = lỗi profile, guard sẽ bắt → INCONCLUSIVE
                logger.warning("set cookie lỗi (%s) — guard đăng nhập sẽ bắt (INCONCLUSIVE, không DEAD)", type(exc).__name__)

    def _parse_cookie(self, cookie: str, target_url: str) -> list[dict[str, Any]]:
        """Cookie JSON array hoặc chuỗi 'k=v' → list dict (một nguồn: browser/cookies.parse_cookies)."""
        return parse_cookies(cookie, target_url)

    def _navigate_capture_status(self, page: ChromiumPage, target_url: str) -> int | None:
        """Điều hướng + bắt HTTP status gói tin document (best-effort). Không bắt được → None (an toàn).

        None chỉ khiến vote bỏ tín hiệu status (vẫn còn DOM + soft-404 text) — KHÔNG thành DEAD (INV-1).
        """
        status: int | None = None
        try:
            page.listen.start(target_url)
            # retry=0: điều hướng ĐÚNG 1 LẦN, KHÔNG tự tải lại khi chờ lâu (chống RELOAD liên tục — bug đã gặp).
            # Chờ lâu/không xong → ném → catch bên dưới (status=None), vẫn còn DOM + settle → detect bình thường.
            page.get(target_url, timeout=self._timeout, retry=0)
            packet = page.listen.wait(count=1, timeout=8)
            if packet is not None and getattr(packet, "response", None) is not None:
                status = int(packet.response.status)
        except Exception as exc:  # noqa: BLE001 — bắt status là best-effort, không chặn detect
            logger.debug("không bắt được HTTP status (%s) — vote dựa DOM/text", type(exc).__name__)
        finally:
            try:
                page.listen.stop()
            except Exception:  # noqa: BLE001, S110 — dừng listener không được làm hỏng luồng
                pass
        return status

    def _body_text(self, page: ChromiumPage) -> str:
        """Text hiển thị (loại script/style như HtmlPageView) để soft-404 không khớp nhầm JS/JSON nhúng."""
        try:
            body = page.ele("tag:body", timeout=2)
            if body:
                return str(body.text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("đọc body text lỗi (%s)", type(exc).__name__)
        return ""
