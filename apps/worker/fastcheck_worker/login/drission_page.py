"""`DrissionLoginPage` — hiện thực `LoginPage` trên browser THẬT (DrissionPage attach CDP GemLogin).

Chỉ dùng ở real mode. Điền bằng cách PASTE cả chuỗi một lần (nhanh, không gõ từng ký tự) rồi để `_advance`
nhấn Enter đi tiếp. KHÔNG log giá trị cookie/credential (INV-12). Selector query phòng thủ như DrissionPageView.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any, cast

from ..browser.cookies import parse_cookies

if TYPE_CHECKING:
    from DrissionPage import ChromiumPage

logger = logging.getLogger("fastcheck.worker.login")

# Chờ sau khi paste để trang (X là React) kịp cập nhật state (bật nút Next / nhận value) trước khi Enter —
# tránh Enter khi value chưa "ăn" (X bỏ qua submit nếu field chưa hợp lệ).
_SETTLE_AFTER_FILL = 0.7
# Chờ tìm phần tử form (giây) khi gõ/click — ngắn để không kéo dài job.
_STEP_LOOKUP_TIMEOUT = 8.0
# Trần thời gian điều hướng (giây). X là SPA có kết nối bền → 'load' rất lâu; retry=0 + timeout để KHÔNG
# reload-loop và KHÔNG treo quá command_ack_timeout của orchestrator (60s). Hết giờ vẫn tương tác được.
_GOTO_TIMEOUT = 20.0


class DrissionLoginPage:
    """Bọc ChromiumPage để chạy kịch bản login. Vòng đời browser do GemLogin quản (không quit ở đây)."""

    def __init__(self, page: ChromiumPage) -> None:
        self._page = page
        self._main_page = page  # tab gốc (platform) để quay lại sau OAuth popup của Google

    @property
    def current_url(self) -> str:
        return str(self._page.url)

    def goto(self, url: str) -> None:
        # retry=0: KHÔNG tự reload khi X SPA "load" lâu (chống vòng reload — như page_source). Bounded timeout
        # để tổng phiên login < command_ack_timeout orchestrator. Hết giờ get trả về, vẫn tương tác được sau đó.
        self._page.get(url, retry=0, timeout=_GOTO_TIMEOUT)

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
        # PASTE cả chuỗi một lần (nhanh — KHÔNG gõ từng ký tự). el.input(text) dispatch input event qua CDP nên
        # React của X vẫn nhận (bật nút Next). KHÔNG log text (INV-12).
        el.input(text)
        # Chờ ngắn để trang kịp cập nhật state trước khi _advance nhấn Enter (X bật nút / nhận value async).
        time.sleep(_SETTLE_AFTER_FILL)
        return True

    def click(self, selector: str) -> bool:
        el = self._safe_ele(selector, timeout=_STEP_LOOKUP_TIMEOUT)
        if el is None:
            return False
        # Real mouse click trước (React nhận sự kiện); lỗi → JS .click() (nút X là React, đôi khi bỏ qua click
        # CDP thường). Giống click_text để nút "Next"/submit ăn chắc khi Enter không submit được.
        try:
            el.click()
            return True
        except Exception:  # noqa: BLE001
            try:
                el.click(by_js=True)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("click %r lỗi (%s)", selector, type(exc).__name__)
                return False

    def press_enter(self, selector: str) -> bool:
        el = self._safe_ele(selector, timeout=_STEP_LOOKUP_TIMEOUT)
        if el is None:
            return False
        try:
            from DrissionPage.common import Keys  # noqa: PLC0415 — chỉ real mode

            el.input(Keys.ENTER)  # submit bước hiện tại (X: Enter ở ô email = "Continue")
            return True
        except Exception as exc:  # noqa: BLE001 — Enter fallback best-effort, không làm hỏng login
            logger.debug("press_enter %r lỗi (%s)", selector, type(exc).__name__)
            return False

    def click_text(self, text: str) -> bool:
        # Nhắm phần tử CLICK ĐƯỢC (button/a/[role=button/link]) theo text, KHÔNG phải <span> text node lồng trong:
        # click span con thường KHÔNG kích hoạt onClick React ở nút cha khi tự động hoá (vd 'Use password' của X
        # ăn ở browser thường nhưng không ăn qua CDP). KHÔNG log text (INV-12).
        # Ưu tiên khớp CHÍNH XÁC (normalize-space = text) để "Continue" KHÔNG trúng "Continue with Google/phone/
        # Apple" (X có mấy nút social cùng chứa "Continue"); không có mới lùi về contains.
        if not text:
            return False
        safe = text.replace('"', "")
        exact_xpath = (
            f'xpath://button[normalize-space(.)="{safe}"] | //*[@role="button"][normalize-space(.)="{safe}"] '
            f'| //a[normalize-space(.)="{safe}"]'
        )
        contains_xpath = (
            f'xpath://button[contains(., "{safe}")] | //a[contains(., "{safe}")] '
            f'| //*[@role="button"][contains(., "{safe}")] | //*[@role="link"][contains(., "{safe}")]'
        )
        el = None
        try:
            el = self._page.ele(exact_xpath, timeout=_STEP_LOOKUP_TIMEOUT)
            if not el:
                el = self._page.ele(contains_xpath, timeout=1)
            if not el:  # fallback: bất kỳ phần tử nào chứa text (rồi click sẽ tự bubble lên nút cha)
                el = self._page.ele(f"text:{text}", timeout=1)
        except Exception as exc:  # noqa: BLE001 — tìm theo text best-effort
            logger.debug("click_text tìm %r lỗi (%s)", text, type(exc).__name__)
            return False
        if not el:
            return False
        # Thử real mouse click (React nhận sự kiện); không được → JS .click() (bubble lên document, React vẫn bắt).
        try:
            el.click()
            return True
        except Exception:  # noqa: BLE001
            try:
                el.click(by_js=True)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("click_text click %r lỗi (%s)", text, type(exc).__name__)
                return False

    def wait_present(self, selector: str, timeout: float) -> bool:
        if not selector:
            return False
        try:
            return bool(self._page.ele(f"css:{selector}", timeout=timeout))
        except Exception as exc:  # noqa: BLE001
            logger.debug("wait_present %r lỗi (%s)", selector, type(exc).__name__)
            return False

    def wait_url_change(self, old_url: str, timeout: float) -> bool:
        # X là SPA hash-routing (#/s/knowledge_check → bước kế) → URL đổi khi chuyển bước. Đây là tín hiệu ĐÁNG
        # TIN để biết "đã sang bước mới" (X giữ input cũ trong DOM nên 'ô còn/mất' KHÔNG đáng tin). Poll ngắn.
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if str(self._page.url) != old_url:
                    return True
            except Exception as exc:  # noqa: BLE001 — đọc url best-effort
                logger.debug("wait_url_change đọc url lỗi (%s)", type(exc).__name__)
            time.sleep(0.25)
        return False

    def use_latest_tab(self) -> bool:
        # OAuth Google mở tab/popup mới → chuyển thao tác sang tab mới nhất. latest_tab trả tab mới nhất
        # (ChromiumTab) — cùng API ele/input nên gán vào self._page là dùng được. Không có popup → giữ nguyên.
        try:
            latest = self._page.latest_tab
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.debug("use_latest_tab lỗi (%s)", type(exc).__name__)
            return False
        if latest is not None and latest is not self._page:
            self._page = latest
            return True
        return False

    def use_main_tab(self) -> None:
        self._page = self._main_page

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

    def form_diagnostics(self) -> dict[str, object]:
        """Liệt kê input/button THẬT trên trang login (name/type/autocomplete/data-testid + text nút) để cập
        nhật selector khi X/TikTok đổi DOM. KHÔNG chứa cookie/credential/GIÁ TRỊ ô nhập (INV-12) — chỉ cấu trúc."""
        inputs: list[str] = []
        buttons: list[str] = []
        try:
            # cast list[Any]: stub DrissionPage khai báo ChromiumElementsList.__iter__/__getitem__ sai.
            for el in cast("list[Any]", self._page.eles("css:input", timeout=0))[:40]:
                attrs = [
                    f"{k}={el.attr(k)}"
                    for k in ("name", "type", "autocomplete", "data-testid", "inputmode")
                    if el.attr(k)
                ]
                if attrs:
                    inputs.append(" ".join(attrs))
            for el in cast("list[Any]", self._page.eles('css:button, [role="button"]', timeout=0))[:40]:
                tid = el.attr("data-testid")
                txt = (el.text or "").strip()[:24]
                if tid or txt:
                    buttons.append(f"testid={tid} text={txt!r}")
        except Exception as exc:  # noqa: BLE001 — chẩn đoán best-effort, không chặn luồng
            logger.debug("form_diagnostics lỗi (%s)", type(exc).__name__)
        return {"url": self.current_url, "inputs": inputs, "buttons": buttons}

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
