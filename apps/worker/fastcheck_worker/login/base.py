"""Interface + kiểu dữ liệu chung cho kịch bản đăng nhập (§7).

`LoginPage` là lớp trừu tượng các thao tác login cần trên browser (gõ, click, chờ, đọc cookie) — tách khỏi
DrissionPage để unit-test được bằng `FakeLoginPage` (không cần Chromium). Real mode: `DrissionLoginPage`
bọc ChromiumPage. Không log cookie/credential (INV-12).
"""

from __future__ import annotations

import base64
import enum
import hmac
import struct
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


def generate_totp(secret_b32: str) -> str:
    """Sinh mã TOTP 6 số (RFC 6238, bước 30s, SHA1) từ secret base32 (2FA) — DÙNG CHUNG cho mọi nơi cần
    tự sinh mã (InfoLogin trên site gốc, GoogleLogin khi tài khoản Google bật 2FA). Một nguồn sự thật."""
    padded = secret_b32.strip().upper().replace(" ", "")
    padded += "=" * ((8 - len(padded) % 8) % 8)
    key = base64.b32decode(padded)
    counter = int(time.time()) // 30
    digest = hmac.new(key, struct.pack(">Q", counter), "sha1").digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


class LoginError(RuntimeError):
    """Không thể đăng nhập vì lý do cấu hình/không hỗ trợ (báo ra, không nuốt — INV-1)."""


class LoginMethod(str, enum.Enum):
    COOKIE = "COOKIE"
    INFO = "INFO"
    # X native: username + password + 2FA(TOTP). Nếu X đòi mã 6 số qua email (LoginAcid) ở BẤT KỲ bước nào,
    # mở tab Outlook lấy mã (token ưu tiên, fallback email/password). Vượt 2FA không gặp LoginAcid thì bỏ qua.
    USERPASS = "USERPASS"


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
    # @username của X cho bước "Confirm your account" (X hỏi @handle để chống bot TRƯỚC bước mật khẩu). Khác
    # `username` (định danh đăng nhập — thường là email). Không có → fallback bấm "Use password" bỏ qua bước này.
    confirm_username: str | None = None
    # ── USERPASS (X native): hộp thư khôi phục để lấy mã xác minh email (LoginAcid) khi X đòi mã 6 số. ──
    # `hotmail_token` (Microsoft/RPS token M.C...$$) ưu tiên: inject vào tab Outlook để vào thẳng hộp thư (né
    # form login + 2FA của Microsoft). Không dùng được → fallback `hotmail_email`/`hotmail_password`. KHÔNG log
    # (INV-12). Không có gì để lấy mã mà X đòi mã email → OTP_REQUIRED (báo ra, không đoán — INV-1).
    hotmail_email: str | None = None
    hotmail_password: str | None = None
    hotmail_token: str | None = None


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

    def has_text(self, *needles: str) -> bool:
        """Trang hiện có CHỨA (không phân biệt hoa/thường) MỘT trong `needles`? Dùng để PHÂN BIỆT màn hình khi
        selector TRÙNG nhau — vd X dùng CHUNG ô nhập số (`ocfEnterTextTextInput`) cho cả '2FA authenticator' LẪN
        'mã 6 số gửi qua email' (LoginAcid); chỉ TEXT mới tách được hai màn (vote đa tín hiệu — INV-8). Không
        khớp → False (KHÔNG coi là lỗi cứng). Test/fake: theo text seed từng state."""
        ...

    def read_text(self, selector: str) -> str:
        """Đọc text hiển thị của phần tử `selector` đầu tiên — để BÓC mã 6 số từ email xác minh của X trong hộp
        thư Outlook. Không thấy → "" (KHÔNG đoán một mã sai — INV-1). Chỉ đọc để bóc mã, KHÔNG log nội dung."""
        ...

    def set_cookies(self, cookie: str, target_url: str) -> None:
        """Nạp cookie TRƯỚC điều hướng (INV-2). login-by-cookie dùng để lập phiên; USERPASS dùng để inject
        Microsoft token vào tab Outlook (vào thẳng hộp thư, né login Microsoft). KHÔNG log giá trị (INV-12)."""
        ...

    def open_new_tab(self, url: str) -> None:
        """Mở TAB MỚI trong CÙNG browser tại `url` và chuyển thao tác sang tab đó — USERPASS mở Outlook lấy mã
        email mà KHÔNG rời phiên login X ở tab gốc (1 browser context, không mở browser mới — INV-6). Sau khi
        xong PHẢI `close_current_tab()` để quay lại tab X. Test/fake: mô phỏng chuyển ngữ cảnh sang tab phụ."""
        ...

    def close_current_tab(self) -> None:
        """Đóng tab phụ hiện tại (Outlook) và quay về tab gốc (X). KHÔNG đóng tab gốc. Test/fake: về lại main."""
        ...

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

    def wait_url_change(self, old_url: str, timeout: float) -> bool:
        """Chờ URL đổi khỏi `old_url` tối đa `timeout` giây (X là SPA hash-routing '#/s/...' — URL đổi khi
        chuyển bước login). Dùng để XÁC MINH đã sang bước mới vì X giữ input cũ trong DOM. Hết giờ → False."""
        ...

    def wait_url_contains(self, substring: str, timeout: float) -> bool:
        """Chờ URL hiện tại CHỨA `substring` tối đa `timeout` giây. Dùng để XÁC MINH đã sang đúng trang (vd
        'accounts.google.com' sau khi bấm 'Continue with Google') TRƯỚC khi gõ — tránh gõ email vào ô 'Email or
        username' GỐC của platform khi OAuth chưa mở. Hết giờ → False."""
        ...

    def cookies_string(self) -> str:
        """Xuất cookie hiện tại (JSON) để refresh session sau phiên thành công. KHÔNG log giá trị."""
        ...


class LoginStrategy(Protocol):
    """Một chiến lược đăng nhập (cookie hoặc info) cho một platform."""

    def login(self, page: LoginPage, credential: Credential) -> LoginResult: ...
