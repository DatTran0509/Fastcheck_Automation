"""Login-by-cookie (cả 4 platform). Cookie đã nạp TRƯỚC điều hướng (INV-2, ở page source/adapter).

Kịch bản: điều hướng trang chủ platform → xác minh guard đăng nhập (thấy avatar/menu và KHÔNG bị đẩy về
trang login). Thấy → LOGGED_IN + thu cookie mới để refresh. Có captcha/challenge → BLOCKED. Không thấy
guard → COOKIE_DEAD (cookie hết hạn) — lỗi profile, KHÔNG kết luận gì về target (INV-2/INV-3).
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from .base import Credential, LoginOutcome, LoginPage, LoginResult, LoginMethod
from .forms import LoginFormSpec

logger = logging.getLogger("fastcheck.worker.login")


def _redirected_to_login(url: str, markers: tuple[str, ...]) -> bool:
    """URL có SEGMENT trùng marker (vd /login) = chưa đăng nhập. So segment (không substring) — như base."""
    if not markers:
        return False
    segments = {seg.lower() for seg in urlparse(url).path.split("/") if seg}
    return any(m.lower() in segments for m in markers)


def _cookie_names(page: LoginPage) -> set[str]:
    """Đọc tên cookie best-effort (real DrissionPage). Trang không hỗ trợ → rỗng → dùng fallback DOM."""
    getter = getattr(page, "cookie_names", None)
    if getter is None:
        return set()
    try:
        return set(getter())
    except Exception:  # noqa: BLE001 — best-effort, lỗi → coi như không có
        return set()


class CookieLogin:
    """Xác minh phiên bằng cookie đã inject. Không gõ gì — chỉ điều hướng + guard (cookie-first, INV-8)."""

    def __init__(self, spec: LoginFormSpec) -> None:
        self._spec = spec

    def login(self, page: LoginPage, credential: Credential) -> LoginResult:
        page.goto(self._spec.home_url)
        # Block/challenge bắt TRƯỚC guard: captcha cũng làm avatar biến mất, nhưng là BLOCKED chứ không
        # phải COOKIE_DEAD — phân biệt để chẩn đoán đúng (INV-3, giống base detector).
        if self._spec.block_selectors and page.has_element(*self._spec.block_selectors):
            return LoginResult(LoginOutcome.BLOCKED, LoginMethod.COOKIE, detail="captcha_or_challenge")
        # Bị đẩy về trang login = cookie không đủ để nền tảng nhận phiên → COOKIE_DEAD (chắc chắn).
        if _redirected_to_login(page.current_url, self._spec.login_url_markers):
            return LoginResult(LoginOutcome.COOKIE_DEAD, LoginMethod.COOKIE, detail="redirected_to_login")
        # Cookie-first (INV-8): đủ cookie session cốt lõi + KHÔNG bị đẩy về login → LOGGED_IN. Locale-independent,
        # không phụ thuộc selector DOM giòn/đa ngôn ngữ (nguồn gây COOKIE_DEAD giả — vd YouTube/FB tiếng Việt).
        if self._spec.auth_cookies:
            names = _cookie_names(page)
            if names and all(c in names for c in self._spec.auth_cookies):
                return LoginResult(
                    LoginOutcome.LOGGED_IN, LoginMethod.COOKIE, fresh_cookie=page.cookies_string()
                )
        # Fallback DOM (khi không đọc được cookie / platform không khai báo auth_cookies).
        if not page.has_element(*self._spec.verify_selectors):
            return LoginResult(LoginOutcome.COOKIE_DEAD, LoginMethod.COOKIE, detail="login_guard_failed")
        # Đăng nhập OK → thu cookie mới (có thể đã xoay) để orchestrator mã hoá & refresh (spec §4.4).
        return LoginResult(LoginOutcome.LOGGED_IN, LoginMethod.COOKIE, fresh_cookie=page.cookies_string())
