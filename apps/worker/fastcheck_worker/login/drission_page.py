"""`DrissionLoginPage` — hiện thực `LoginPage` trên browser THẬT (DrissionPage attach CDP GemLogin).

Chỉ dùng ở real mode. Gõ MÔ PHỎNG NGƯỜI: từng ký tự + delay ngẫu nhiên nhỏ (chống phát hiện bot ở mức cơ
bản — spec §4.4). KHÔNG log giá trị cookie/credential (INV-12). Selector query phòng thủ như DrissionPageView.
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import TYPE_CHECKING, Any

from ..browser.cookies import parse_cookies

if TYPE_CHECKING:
    from DrissionPage import ChromiumPage

logger = logging.getLogger("fastcheck.worker.login")

# Delay gõ mỗi ký tự (giây) — khoảng người thật, đủ để không "dán" tức thì.
_TYPE_DELAY_MIN = 0.04
_TYPE_DELAY_MAX = 0.18
# Chờ tìm phần tử form (giây) khi gõ/click — ngắn để không kéo dài job.
_STEP_LOOKUP_TIMEOUT = 8.0


class DrissionLoginPage:
    """Bọc ChromiumPage để chạy kịch bản login. Vòng đời browser do GemLogin quản (không quit ở đây)."""

    def __init__(self, page: ChromiumPage) -> None:
        self._page = page

    @property
    def current_url(self) -> str:
        return str(self._page.url)

    def goto(self, url: str) -> None:
        self._page.get(url)

    def set_cookies(self, cookie: str, target_url: str) -> None:
        """Nạp cookie TRƯỚC điều hướng (INV-2) cho login-by-cookie. KHÔNG log giá trị (INV-12)."""
        logger.debug("login: inject cookie trước điều hướng (len=%d)", len(cookie or ""))
        cookies = parse_cookies(cookie or "", target_url)
        if not cookies:
            return
        try:
            self._page.set.cookies(cookies)
        except Exception as exc:  # noqa: BLE001 — cookie hỏng = lỗi profile, guard sẽ bắt (COOKIE_DEAD)
            logger.warning("login: set cookie lỗi (%s) — guard sẽ bắt (COOKIE_DEAD, không đoán)", type(exc).__name__)

    def has_element(self, *selectors: str) -> bool:
        for sel in selectors:
            if not sel:
                continue
            try:
                if self._page.ele(f"css:{sel}", timeout=0):
                    return True
            except Exception as exc:  # noqa: BLE001 — selector giòn không làm hỏng login
                logger.debug("selector %r lỗi (%s)", sel, type(exc).__name__)
        return False

    def fill(self, selector: str, text: str) -> bool:
        el = self._safe_ele(selector, timeout=_STEP_LOOKUP_TIMEOUT)
        if el is None:
            return False
        try:
            el.clear()
        except Exception:  # noqa: BLE001, S110 — clear best-effort (ô có thể chưa hỗ trợ)
            pass
        # Gõ từng ký tự + delay ngẫu nhiên (mô phỏng người — KHÔNG log text, INV-12).
        for ch in text:
            el.input(ch)
            time.sleep(random.uniform(_TYPE_DELAY_MIN, _TYPE_DELAY_MAX))  # noqa: S311 — không phải mật mã
        return True

    def click(self, selector: str) -> bool:
        el = self._safe_ele(selector, timeout=_STEP_LOOKUP_TIMEOUT)
        if el is None:
            return False
        el.click()
        return True

    def wait_present(self, selector: str, timeout: float) -> bool:
        if not selector:
            return False
        try:
            return bool(self._page.ele(f"css:{selector}", timeout=timeout))
        except Exception as exc:  # noqa: BLE001
            logger.debug("wait_present %r lỗi (%s)", selector, type(exc).__name__)
            return False

    def cookies_string(self) -> str:
        # Xuất cookie hiện tại dạng JSON (list dict) để orchestrator mã hoá & refresh. KHÔNG log giá trị.
        try:
            # DrissionPage 4.x: page.cookies() KHÔNG có as_dict — trả list dict {name,value,domain,...}.
            cookies = [dict(c) for c in self._page.cookies()]
            return json.dumps(cookies)
        except Exception as exc:  # noqa: BLE001
            logger.debug("đọc cookie lỗi (%s)", type(exc).__name__)
            return ""

    def cookie_names(self) -> set[str]:
        # Tên cookie hiện tại (CHỈ tên, không giá trị — INV-12) cho guard cookie-first.
        try:
            return {str(c.get("name")) for c in self._page.cookies() if c.get("name")}
        except Exception as exc:  # noqa: BLE001
            logger.debug("đọc tên cookie lỗi (%s)", type(exc).__name__)
            return set()

    def _safe_ele(self, selector: str, timeout: float) -> Any:
        # DrissionPage không có type stub → trả Any (mypy cho phép .input()/.click()/.clear()).
        if not selector:
            return None
        try:
            el = self._page.ele(f"css:{selector}", timeout=timeout)
            return el or None
        except Exception as exc:  # noqa: BLE001
            logger.debug("tìm phần tử %r lỗi (%s)", selector, type(exc).__name__)
            return None
