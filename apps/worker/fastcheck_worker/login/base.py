"""Interface + kiểu dữ liệu chung cho kịch bản đăng nhập (§7).

`LoginPage` là lớp trừu tượng các thao tác login cần trên browser (gõ, click, chờ, đọc cookie) — tách khỏi
DrissionPage để unit-test được bằng `FakeLoginPage` (không cần Chromium). Real mode: `DrissionLoginPage`
bọc ChromiumPage. Không log cookie/credential (INV-12).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class LoginError(RuntimeError):
    """Không thể đăng nhập vì lý do cấu hình/không hỗ trợ (báo ra, không nuốt — INV-1)."""


class LoginMethod(str, enum.Enum):
    COOKIE = "COOKIE"
    INFO = "INFO"


class LoginOutcome(str, enum.Enum):
    """Kết quả đăng nhập — tách rõ để orchestrator phân loại (giống profile_health)."""

    LOGGED_IN = "LOGGED_IN"  # đã đăng nhập → tiếp tục detect
    COOKIE_DEAD = "COOKIE_DEAD"  # cookie hết hạn / không thấy guard → lỗi profile (CHALLENGED)
    BLOCKED = "BLOCKED"  # captcha/challenge chặn → profile BLOCKED
    OTP_REQUIRED = "OTP_REQUIRED"  # cần OTP mà không có otp_secret → cần can thiệp
    BAD_CREDENTIAL = "BAD_CREDENTIAL"  # sai user/pass
    # KHÔNG tìm thấy ô/nút của form login (selector đổi hoặc trang chặn bot) → lỗi TỰ ĐỘNG HOÁ, KHÔNG phải
    # cookie chết. Báo RÕ bước hỏng (detail) + DIAG cấu trúc form để cập nhật selector — không nuốt (INV-1).
    FORM_ERROR = "FORM_ERROR"


@dataclass(frozen=True)
class Credential:
    """Thông tin đăng nhập. KHÔNG lưu mật khẩu thô ngoài phiên (INV-12) — chỉ dùng trong bộ nhớ lúc login."""

    method: LoginMethod
    cookie: str = ""
    username: str | None = None
    password: str | None = None
    # TOTP secret (base32) để tự sinh mã 2FA khi info-login gặp OTP. Không có → OTP_REQUIRED.
    otp_secret: str | None = None


@dataclass(frozen=True)
class LoginResult:
    """Kết quả một lần chạy kịch bản login. `fresh_cookie` để orchestrator mã hoá & refresh (spec §4.4)."""

    outcome: LoginOutcome
    method: LoginMethod
    fresh_cookie: str | None = None
    detail: str | None = None

    @property
    def logged_in(self) -> bool:
        return self.outcome == LoginOutcome.LOGGED_IN


@runtime_checkable
class LoginPage(Protocol):
    """Thao tác browser cần cho login. Real: DrissionPage; test: FakeLoginPage."""

    @property
    def current_url(self) -> str: ...

    def goto(self, url: str) -> None: ...

    def has_element(self, *selectors: str) -> bool: ...

    def cookie_names(self) -> set[str]:
        """Tên cookie hiện có (KHÔNG giá trị — INV-12) cho guard cookie-first. Real: DrissionPage; test: rỗng."""
        ...

    def fill(self, selector: str, text: str) -> bool:
        """Gõ `text` vào ô `selector` MÔ PHỎNG NGƯỜI (delay từng ký tự). True nếu thấy ô để gõ."""
        ...

    def click(self, selector: str) -> bool:
        """Click phần tử `selector`. True nếu thấy để click."""
        ...

    def press_enter(self, selector: str) -> bool:
        """Gõ Enter trong ô `selector` để SUBMIT bước (fallback khi nút Next/Submit không có selector ổn định
        — vd nút 'Continue' của X). Nhắm đúng form nên an toàn hơn đoán nút. True nếu thấy ô để gõ."""
        ...

    def click_text(self, text: str) -> bool:
        """Click phần tử theo TEXT hiển thị (vd link 'Use password' của X để bỏ qua bước xác minh danh tính).
        Dùng khi không có selector/testid ổn định. True nếu tìm & click được."""
        ...

    def use_latest_tab(self) -> bool:
        """Chuyển thao tác sang tab/popup MỚI NHẤT (OAuth Google 'Continue with Google' thường mở popup mới).
        True nếu đã chuyển sang tab khác. Test/fake: no-op → False."""
        ...

    def use_main_tab(self) -> None:
        """Quay về tab gốc (platform) sau khi xong OAuth ở popup. Test/fake: no-op."""
        ...

    def wait_present(self, selector: str, timeout: float) -> bool:
        """Chờ selector xuất hiện tối đa `timeout` giây. Hết giờ → False (KHÔNG coi là lỗi cứng)."""
        ...

    def cookies_string(self) -> str:
        """Xuất cookie hiện tại (JSON) để refresh session sau phiên thành công. KHÔNG log giá trị."""
        ...


class LoginStrategy(Protocol):
    """Một chiến lược đăng nhập (cookie hoặc info) cho một platform."""

    def login(self, page: LoginPage, credential: Credential) -> LoginResult: ...
