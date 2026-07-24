"""Đọc mã xác minh 6 số của X từ hộp thư Outlook/Hotmail (USERPASS — bước LoginAcid của X).

Khi X đòi mã 6 số gửi qua email ở BẤT KỲ bước login nào, worker mở TAB MỚI trong CÙNG browser (1 context —
INV-6), vào Outlook, tìm email X mới nhất và bóc mã (6 chữ số). Đăng nhập Outlook:
  1. ƯU TIÊN inject Microsoft token (M.C...$$) như cookie để vào THẲNG hộp thư — né form login + 2FA của
     Microsoft (chính Microsoft cũng có anti-bot; đi bằng token ổn định hơn gõ mật khẩu).
  2. FALLBACK đăng nhập email + mật khẩu Hotmail nếu token không vào được.
Không lấy được mã → trả None (worker báo OTP_REQUIRED — TUYỆT ĐỐI không đoán một mã sai, INV-1). Xong PHẢI
đóng tab để quay lại phiên login X ở tab gốc. KHÔNG log mã/cookie/credential (INV-12).

Selector/URL Outlook & tên cookie cho token là ĐIỂM KHỞI ĐẦU — Microsoft đổi DOM/cơ chế liên tục; cần
health-check định kỳ như detector (spec §8.5). Mọi bước KIỂM kết quả, không nuốt lỗi.
"""

from __future__ import annotations

import logging
import re
import time

from .base import Credential, LoginPage

logger = logging.getLogger("fastcheck.worker.login")

_OUTLOOK_MAIL_URL = "https://outlook.live.com/mail/0/"
# Domain để gắn cookie token (Microsoft account). Token dùng chung cho login.live.com.
_MS_COOKIE_DOMAIN = "https://login.live.com/"

# Tên cookie khả dĩ cho Microsoft/RPS token (M.C...$$). CẦN XÁC MINH với traffic/DOM thật — Microsoft không
# công bố; đặt vài tên phổ biến, đường TIN CẬY vẫn là fallback mật khẩu. Không vào được → password path.
_MS_TOKEN_COOKIE_NAMES = ("__Host-MSAAUTH", "MSPAuth", "WLSSC")

# Selector đăng nhập Microsoft (login.live.com) — fallback khi không có/không dùng được token.
_MS_EMAIL = 'input[type="email"], input[name="loginfmt"]'
_MS_PASSWORD = 'input[type="password"], input[name="passwd"]'
_MS_SUBMIT = "#idSIButton9"  # cùng id cho nút "Next" (sau email) và "Sign in" (sau mật khẩu)
_MS_SUBMIT_TEXTS = ("Next", "Sign in", "Đăng nhập", "Yes")
_MS_STAY_SIGNED_IN_NO = "#idBtn_Back"  # "Stay signed in?" → No (không giữ phiên trên máy trạm)

# Guard "đã vào hộp thư": khung danh sách mail của Outlook. CSS list (dấu phẩy = fallback, INV-8).
_INBOX_GUARD = (
    'div[role="main"], [aria-label="Message list"], '
    'div[data-app-section="MailList"], div[aria-label*="Message list"]'
)
# Vùng đọc để bóc mã (subject + snippet email mới nhất). Đọc TEXT rồi regex — không phụ thuộc 1 selector giòn.
_MESSAGE_LIST = (
    '[aria-label="Message list"], div[data-app-section="MailList"], '
    'div[role="region"][aria-label*="Message"], div[role="main"]'
)

# Ngân sách thời gian (giây) — GIỮ CHẶT vì đây là NHÁNH CHẬM (mở tab + login + chờ mail). Tổng phiên login X
# kể cả nhánh này cần nằm trong ngưỡng ack login của orchestrator (nhánh email là ngoại lệ chậm nhất).
_INBOX_TIMEOUT = 12.0
_STEP_TIMEOUT = 8.0
_CODE_POLL_TIMEOUT = 20.0  # X gửi mail có thể trễ vài giây → poll lại inbox
_CODE_POLL_INTERVAL = 2.0

# Bóc mã: ưu tiên 6 số ĐỨNG GẦN từ khoá ('code'/'confirmation') để tránh bắt nhầm số khác (giờ, số lượng...).
_CODE_KEYWORD_PATTERNS = (
    re.compile(r"code[^0-9]{0,24}(\d{6})", re.IGNORECASE),
    re.compile(r"(\d{6})[^0-9]{0,24}(?:is your|to (?:log|sign) in|confirmation)", re.IGNORECASE),
)
_CODE_CONTEXT_WORDS = ("code", "confirm", "verification", "x.com", "twitter", "single-use")
_BARE_6_DIGITS = re.compile(r"(?<!\d)(\d{6})(?!\d)")


class HotmailOtpReader:
    """Mở tab Outlook, đăng nhập (token → fallback mật khẩu), bóc mã 6 số của X. Trả None nếu không lấy được."""

    def read_login_code(self, page: LoginPage, credential: Credential) -> str | None:
        """Mở tab Outlook lấy mã xác minh email của X. None = không lấy được (worker → OTP_REQUIRED, không đoán)."""
        has_token = bool(credential.hotmail_token)
        has_pw = bool(credential.hotmail_email and credential.hotmail_password)
        if not has_token and not has_pw:
            logger.info("USERPASS: X đòi mã email nhưng KHÔNG có hotmail token/email+password → không lấy được mã")
            return None

        page.open_new_tab(_OUTLOOK_MAIL_URL)
        try:
            entered = False
            if has_token:
                entered = self._enter_with_token(page, credential.hotmail_token or "")
                if not entered:
                    logger.info("USERPASS: token Microsoft không vào được hộp thư → thử fallback mật khẩu")
            if not entered and has_pw:
                entered = self._login_with_password(
                    page, credential.hotmail_email or "", credential.hotmail_password or ""
                )
            if not entered:
                logger.info("USERPASS: không vào được hộp thư Outlook (token & mật khẩu đều fail) → không lấy được mã")
                return None
            code = self._read_code(page)
            if code is None:
                logger.info("USERPASS: vào được Outlook nhưng KHÔNG thấy email mã X (poll hết giờ) — không đoán mã")
            return code
        finally:
            # Đóng tab Outlook để quay về tab login X (browser vẫn do execute.finally đóng — INV-9).
            page.close_current_tab()

    def _enter_with_token(self, page: LoginPage, token: str) -> bool:
        """Inject Microsoft token như cookie rồi tải lại Outlook. True nếu vào được hộp thư. Tên cookie cần xác
        minh thật (xem _MS_TOKEN_COOKIE_NAMES) — thất bại thì fallback mật khẩu (không đoán đã vào — INV-1)."""
        cookie = "; ".join(f"{name}={token}" for name in _MS_TOKEN_COOKIE_NAMES)
        page.set_cookies(cookie, _MS_COOKIE_DOMAIN)  # KHÔNG log giá trị (INV-12)
        page.goto(_OUTLOOK_MAIL_URL)
        return self._inbox_ready(page)

    def _login_with_password(self, page: LoginPage, email: str, password: str) -> bool:
        """Đăng nhập Outlook bằng email + mật khẩu Hotmail. True nếu vào được hộp thư. Microsoft bắt xác minh
        thêm (2FA của chính Microsoft) → không tự động được → False (báo ra, không đoán — INV-1)."""
        page.goto(_OUTLOOK_MAIL_URL)  # chưa đăng nhập → Microsoft redirect sang login.live.com
        if not page.wait_present(_MS_EMAIL, _STEP_TIMEOUT):
            logger.info("USERPASS: không thấy ô email đăng nhập Microsoft (DOM đổi hoặc đã có phiên khác)")
            return False
        page.fill(_MS_EMAIL, email)
        self._click_next(page)
        if not page.wait_present(_MS_PASSWORD, _STEP_TIMEOUT):
            logger.info("USERPASS: Outlook không hiện ô mật khẩu sau email (có thể Microsoft bắt xác minh) — bỏ")
            return False
        page.fill(_MS_PASSWORD, password)
        self._click_next(page)
        # "Stay signed in?" → No (không giữ phiên trên máy trạm). Không có màn này → click no-op, bỏ qua.
        page.click(_MS_STAY_SIGNED_IN_NO)
        return self._inbox_ready(page)

    def _click_next(self, page: LoginPage) -> None:
        if page.click(_MS_SUBMIT):
            return
        for text in _MS_SUBMIT_TEXTS:
            if page.click_text(text):
                return

    def _inbox_ready(self, page: LoginPage) -> bool:
        return page.wait_present(_INBOX_GUARD, _INBOX_TIMEOUT)

    def _read_code(self, page: LoginPage) -> str | None:
        """Poll đọc danh sách mail, bóc mã 6 số của X. Outlook web tự cập nhật (mail mới hiện lên DOM) nên đọc
        lại text mỗi vòng là đủ. Hết giờ mà không thấy → None (không đoán — INV-1)."""
        deadline = time.monotonic() + _CODE_POLL_TIMEOUT
        while True:
            code = self._extract_code(page.read_text(_MESSAGE_LIST))
            if code:
                return code
            if time.monotonic() >= deadline:
                return None
            time.sleep(_CODE_POLL_INTERVAL)

    @staticmethod
    def _extract_code(text: str) -> str | None:
        """Bóc mã 6 số từ text hộp thư. Ưu tiên 6 số GẦN từ khoá; chỉ nhận 6 số đơn lẻ khi text có ngữ cảnh mã
        xác minh (tránh bắt nhầm số bất kỳ — INV-1). Không có → None."""
        if not text:
            return None
        for pattern in _CODE_KEYWORD_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1)
        low = text.lower()
        if any(word in low for word in _CODE_CONTEXT_WORDS):
            match = _BARE_6_DIGITS.search(text)
            if match:
                return match.group(1)
        return None
