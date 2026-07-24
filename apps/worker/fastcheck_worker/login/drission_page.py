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
# click_text: khớp-CHÍNH-XÁC / contains chỉ nhắm nút THẬT (button/a/role=button) → nếu đúng loại thẻ thì thấy
# NGAY, dùng timeout NGẮN để KHÔNG treo 8s khi nút không thuộc các thẻ đó (vd nút social TikTok là <div>). Việc
# CHỜ render dồn vào bước `text:` (bắt mọi biến thể DOM) với _STEP_LOOKUP_TIMEOUT — hết cảnh "đứng im ~10s".
_CLICK_EXACT_TIMEOUT = 1.5
# Trần thời gian điều hướng (giây). X là SPA có kết nối bền → 'load' rất lâu; retry=0 + timeout để KHÔNG
# reload-loop và KHÔNG treo quá command_ack_timeout của orchestrator (60s). Hết giờ vẫn tương tác được.
_GOTO_TIMEOUT = 20.0
# Modal login của X ([role=dialog]/[aria-modal]) CHỒNG lên trang nền (home khi CHƯA đăng nhập cũng có ô/nút
# trùng) → ưu tiên tìm TRONG modal (xem `_dialog`); không có modal (login full-page / trang Google) → tìm toàn
# trang như cũ (không đổi hành vi GoogleLogin/CookieLogin).


class DrissionLoginPage:
    """Bọc ChromiumPage để chạy kịch bản login. Vòng đời browser do GemLogin quản (không quit ở đây)."""

    def __init__(self, page: ChromiumPage) -> None:
        self._page = page
        self._main_page = page  # tab gốc (platform) để quay lại sau OAuth popup của Google
        # load_mode 'none': .get() GỬI lệnh điều hướng rồi TRẢ NGAY, KHÔNG chờ event 'load'. Trang login
        # TikTok & Google OAuth là SPA nặng → 'normal' (mặc định) chờ 'load' làm TREO ~10s trước khi bấm
        # "Continue with Google" dù nút đã có sẵn trong DOM. 'none' (không 'eager') để trang tải TIẾP các
        # chunk động thay vì bị hủy — cùng lý do browser/page_source.py. fill/click/wait_present đã có timeout
        # riêng nên vẫn chờ đúng phần tử cần trước khi thao tác.
        self._set_fast_load(page)

    @staticmethod
    def _set_fast_load(page: Any) -> None:
        # API set.load_mode khác nhau giữa ChromiumPage và ChromiumTab/phiên bản → best-effort, không chặn login.
        try:
            page.set.load_mode.none()
        except Exception as exc:  # noqa: BLE001
            logger.debug("set load_mode none lỗi (%s)", type(exc).__name__)

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
        # CHỈ tính phần tử ĐANG HIỂN THỊ. X render sẵn/giữ input ẩn của bước khác (vd ô `input[name=password]` ẩn
        # ngay ở màn nhập TÀI KHOẢN) → nếu tính cả phần tử ẩn thì _classify nhận nhầm màn (bước 0 thành
        # 'password' rồi điền mật khẩu vào ô tài khoản). Chỉ tin VISIBLE để phân biệt đúng màn (skill: đa tín hiệu).
        return any(sel and self._visible_ele(sel, 0.0) is not None for sel in selectors)

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
            # Ưu tiên nút TRONG modal login (nền + modal cùng có nút 'Continue'/'Continue with Google' → click nút
            # nền khuất = không tiến). xpath tương đối ('.//') để tìm trong dialog; không thấy → tìm toàn trang.
            dialog = self._dialog()
            if dialog is not None:
                rel_exact = exact_xpath.replace("xpath://", "xpath:.//").replace(" | //", " | .//")
                rel_contains = contains_xpath.replace("xpath://", "xpath:.//").replace(" | //", " | .//")
                el = dialog.ele(rel_exact, timeout=_CLICK_EXACT_TIMEOUT) or dialog.ele(
                    rel_contains, timeout=_CLICK_EXACT_TIMEOUT
                )
            # exact/contains nhắm nút THẬT (button/a/role=button) → timeout NGẮN: đúng loại thẻ thì thấy ngay,
            # không thì rơi xuống nhanh (KHÔNG treo 8s ở đây khi nút là <div> như nút social TikTok).
            if not el:
                el = self._page.ele(exact_xpath, timeout=_CLICK_EXACT_TIMEOUT)
            if not el:
                el = self._page.ele(contains_xpath, timeout=_CLICK_EXACT_TIMEOUT)
            if not el:  # bắt mọi biến thể DOM (click sẽ bubble lên nút cha) — CHỜ render dồn vào đây
                el = self._page.ele(f"text:{text}", timeout=_STEP_LOOKUP_TIMEOUT)
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
        # Chờ MỘT selector con (tách dấu phẩy — né lỗi khớp css-list của DrissionPage) XUẤT HIỆN trong DOM. Đây
        # là chờ "màn đã render" (settle) nên chỉ cần CÓ MẶT, không cần hiển thị. Poll để không phụ thuộc css-list.
        if not selector:
            return False
        parts = self._parts(selector)
        deadline = time.time() + max(timeout, 0.0)
        while True:
            for part in parts:
                try:
                    if self._page.ele(f"css:{part}", timeout=0):
                        return True
                except Exception as exc:  # noqa: BLE001
                    logger.debug("wait_present %r lỗi (%s)", part, type(exc).__name__)
            if time.time() >= deadline:
                return False
            time.sleep(0.2)

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

    def wait_url_contains(self, substring: str, timeout: float) -> bool:
        # Poll URL cho tới khi CHỨA substring (vd 'accounts.google.com') — xác nhận đã sang đúng trang OAuth
        # trước khi gõ. Poll ngắn như wait_url_change.
        if not substring:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if substring in str(self._page.url):
                    return True
            except Exception as exc:  # noqa: BLE001 — đọc url best-effort
                logger.debug("wait_url_contains đọc url lỗi (%s)", type(exc).__name__)
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
            self._set_fast_load(latest)  # popup Google cũng là SPA → tránh treo chờ 'load'
            return True
        return False

    def use_main_tab(self) -> None:
        self._page = self._main_page

    def open_new_tab(self, url: str) -> None:
        # Mở TAB MỚI trong CÙNG browser (1 context — INV-6) rồi chuyển thao tác sang tab đó. USERPASS dùng để mở
        # Outlook lấy mã email mà KHÔNG rời tab login X. new_tab(url) của DrissionPage 4.x trả ChromiumTab (cùng
        # API ele/input). Lỗi → giữ nguyên tab hiện tại (reader sẽ thấy inbox không sẵn sàng → fallback/None).
        try:
            tab = self._main_page.new_tab(url)
        except Exception as exc:  # noqa: BLE001 — mở tab lỗi = không lấy được mã (báo ra ở reader, không đoán)
            logger.warning("open_new_tab lỗi (%s) — không mở được tab Outlook", type(exc).__name__)
            return
        self._page = tab
        self._set_fast_load(tab)

    def close_current_tab(self) -> None:
        # Đóng tab phụ (Outlook) rồi quay về tab gốc (X). CHỈ đóng khi KHÁC tab gốc — đóng tab gốc = đóng browser
        # (browser do GemLogin quản, đóng ở execute.finally — INV-9). Luôn trỏ lại main dù đóng có lỗi.
        cur = self._page
        if cur is not self._main_page:
            try:
                cur.close()
            except Exception as exc:  # noqa: BLE001 — đóng tab best-effort, không chặn trả kết quả
                logger.debug("close_current_tab lỗi (%s)", type(exc).__name__)
        self._page = self._main_page

    def has_text(self, *needles: str) -> bool:
        # Đọc text hiển thị của <body> (không phân biệt hoa/thường) để phân biệt màn hình khi selector trùng —
        # vd X dùng chung ô số cho '2FA app' lẫn 'mã qua email'. KHÔNG log nội dung (INV-12). Lỗi/không có → False.
        wanted = [n.lower() for n in needles if n]
        if not wanted:
            return False
        try:
            body = self._page.ele("tag:body", timeout=0)
            text = (body.text if body else "") or ""
        except Exception as exc:  # noqa: BLE001 — đọc text best-effort, không làm hỏng login
            logger.debug("has_text đọc body lỗi (%s)", type(exc).__name__)
            return False
        haystack = text.lower()
        return any(n in haystack for n in wanted)

    def read_text(self, selector: str) -> str:
        # Text của phần tử đầu khớp `selector` (bóc mã 6 số từ email X trong Outlook). Không thấy → "" (không
        # đoán mã sai — INV-1). KHÔNG log nội dung (INV-12).
        el = self._safe_ele(selector, timeout=_STEP_LOOKUP_TIMEOUT)
        if el is None:
            return ""
        try:
            return (el.text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.debug("read_text %r lỗi (%s)", selector, type(exc).__name__)
            return ""

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

    @staticmethod
    def _parts(selector: str) -> list[str]:
        # Tách CSS-list (dấu phẩy) thành TỪNG selector con → tìm từng cái. Né lỗi khớp css-list của DrissionPage
        # (đầu mối khiến `input[name=password]` khớp nhầm ô tài khoản) VÀ cho phép lọc theo phần tử hiển thị.
        # Selector trong dự án không có dấu phẩy lồng trong ngoặc nên tách thô theo ',' là an toàn.
        return [p.strip() for p in selector.split(",") if p.strip()]

    @staticmethod
    def _is_displayed(el: Any) -> bool:
        # `is_displayed` có thể là property hoặc method tuỳ phiên bản DrissionPage → xử cả hai. Không xác định
        # được → coi như HIỂN THỊ (không chặn thao tác khi API đổi).
        try:
            disp = el.states.is_displayed
            return bool(disp() if callable(disp) else disp)
        except Exception:  # noqa: BLE001
            return True

    def _dialog(self) -> Any:
        # Modal login đang mở (nếu có). Tìm từng phần (né css-list). Không có → None → tìm toàn trang.
        for part in ('[role="dialog"]', '[aria-modal="true"]'):
            try:
                el = self._page.ele(f"css:{part}", timeout=0)
                if el:
                    return el
            except Exception as exc:  # noqa: BLE001
                logger.debug("tìm dialog %r lỗi (%s)", part, type(exc).__name__)
        return None

    def _visible_ele(self, selector: str, timeout: float) -> Any:
        # Phần tử ĐANG HIỂN THỊ đầu tiên khớp `selector`, ưu tiên trong modal login (dialog) rồi toàn trang. Poll
        # tới `timeout` để chờ render. QUAN TRỌNG: chỉ nhận VISIBLE — X giữ input ẩn của bước khác trong DOM
        # (ô password ẩn ở màn nhập tài khoản) → tin phần tử ẩn = nhận nhầm màn + điền nhầm ô (bug bước 0='password').
        parts = self._parts(selector)
        deadline = time.time() + max(timeout, 0.0)
        while True:
            for root in (r for r in (self._dialog(), self._page) if r is not None):
                for part in parts:
                    try:
                        els = cast("list[Any]", root.eles(f"css:{part}", timeout=0))
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("tìm %r lỗi (%s)", part, type(exc).__name__)
                        continue
                    for el in els:
                        if self._is_displayed(el):
                            return el
            if time.time() >= deadline:
                return None
            time.sleep(0.2)

    def _safe_ele(self, selector: str, timeout: float) -> Any:
        # DrissionPage không có type stub → trả Any (mypy cho phép .input()/.click()/.clear()).
        if not selector:
            return None
        el = self._visible_ele(selector, timeout)
        if el is not None:
            return el
        # Không có phần tử HIỂN THỊ khớp → thử phần tử bất kỳ (kể cả ẩn) để không chặn cứng khi is_displayed sai.
        for part in self._parts(selector):
            try:
                found = self._page.ele(f"css:{part}", timeout=0)
                if found:
                    return found
            except Exception as exc:  # noqa: BLE001
                logger.debug("tìm phần tử %r lỗi (%s)", part, type(exc).__name__)
        return None
