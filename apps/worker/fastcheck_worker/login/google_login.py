"""Login qua GOOGLE ("Continue with Google") cho TikTok & YouTube — dùng TÀI KHOẢN GOOGLE (email/mật khẩu).

Vì sao: login gốc của các platform này hay vướng captcha/challenge (DOM đổi liên tục); form Google ổn định hơn.
Luồng (đúng thao tác tay người dùng):
  TikTok: mở trang login TikTok → click "Continue with Google" → (Google mở tab/popup) → email → Enter →
          mật khẩu → Enter → (TÙY CHỌN) Google hỏi mã 2FA nếu tài khoản Google bật authenticator app
  YT    : vào THẲNG Google sign-in (YouTube = Google) → email → Enter → mật khẩu → Enter → (tùy chọn) 2FA
Sau đó verify guard đăng nhập trên platform (cookie-first như CookieLogin — INV-8). KHÔNG log credential (INV-12).

Google chặn browser tự động RẤT mạnh ("This browser or app may not be secure"): nếu sau khi nhập email mà
KHÔNG hiện ô mật khẩu → coi là BLOCKED (báo RÕ, không đoán — INV-1), đường tin cậy vẫn là login-by-cookie.

2FA Google là TÙY CHỌN (chỉ xảy ra nếu tài khoản Google đó bật authenticator app) — có otp_secret thì tự
sinh mã điền tiếp; không có thì báo OTP_REQUIRED; không thấy màn hình 2FA thì bỏ qua bước này (INV-1: nhánh
rõ ràng có/không, không đoán).
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from .base import Credential, LoginError, LoginMethod, LoginOutcome, LoginPage, LoginResult, generate_totp
from .forms import LoginFormSpec

logger = logging.getLogger("fastcheck.worker.login")

# Chờ tối đa mỗi bước (giây). NGÂN SÁCH: tổng worst-case của TẤT CẢ các bước PHẢI < command_ack_timeout của
# orchestrator (60s), nếu không station bị coi là "không phản hồi 60000ms" dù login vẫn đang chạy. Mỗi wait_*
# TRẢ VỀ NGAY khi có tín hiệu (thường 1–3s), timeout chỉ là trần khi bị chặn/mạng chậm → đặt vừa đủ, đừng rộng.
_STEP_TIMEOUT = 8.0
# 2FA Google là bước TÙY CHỌN — ngắn hơn _STEP_TIMEOUT. LƯU Ý: bước này LUÔN chờ hết timeout với tài khoản
# KHÔNG bật 2FA (đa số) vì ô OTP không bao giờ hiện → giữ NHỎ để không phí thời gian mỗi lần login.
_GOOGLE_OTP_TIMEOUT = 3.0
# Chờ trang platform (SPA) render guard/block sau khi quay lại verify — load_mode 'none' trả về sớm nên
# has_element(timeout=0) có thể đọc DOM khi chưa kịp render → COOKIE_DEAD OAN cho ca đăng nhập THẬT.
_VERIFY_RENDER_TIMEOUT = 6.0
# Sau khi Google xác thực xong, OAuth còn phải REDIRECT/POSTBACK về platform (popup Google đóng, trang
# accounts.google.com/gsi/transform postMessage token cho x.com) để platform LẬP PHIÊN. Chờ guard tự hiện trên
# tab platform trước khi force-goto — nếu goto sớm sẽ CẮT callback (phiên chưa kịp lập) → COOKIE_DEAD OAN dù
# Google đã auth OK (bug: qua 2FA, hiện popup "Save password", nhưng guard fail). Hết giờ → verify vẫn chạy tiếp.
_OAUTH_SETTLE_TIMEOUT = 12.0
# Host trang đăng nhập Google — dùng để XÁC MINH "Continue with Google" đã mở đúng trang Google trước khi gõ.
_GOOGLE_HOST = "accounts.google.com"
# Selector form Google (CHUNG cho mọi platform — đây là trang của Google, không phải của TikTok/YT). Fallback id cũ.
_GOOGLE_EMAIL = 'input[type="email"], #identifierId'
_GOOGLE_PASSWORD = 'input[type="password"], input[name="Passwd"]'
# Nút "Next" của Google (submit bước). Google KHÔNG submit đáng tin khi gõ Enter qua CDP (form Material/React,
# giống X) → PHẢI click nút. Trang identifier có nút id #identifierNext, trang mật khẩu có #passwordNext; kèm
# fallback theo text đa ngôn ngữ. Không có nút → cuối cùng mới Enter.
_GOOGLE_EMAIL_NEXT = "#identifierNext"
_GOOGLE_PASSWORD_NEXT = "#passwordNext"
_GOOGLE_OTP_NEXT = "#totpNext"
_GOOGLE_NEXT_TEXTS = ("Next", "Tiếp theo")
# Màn CHỌN phương thức 2FA (tài khoản bật NHIỀU cách): phải chọn "Google Authenticator app" thì mới hiện ô nhập
# mã TOTP. "Google Authenticator" là TÊN THƯƠNG HIỆU (không dịch) → khớp `contains` bền hơn chuỗi dài bản địa
# hoá; chỉ dòng phương thức authenticator chứa cụm này (các dòng khác: security key/phone/SMS/recovery).
_GOOGLE_TOTP_OPTION_TEXTS = ("Google Authenticator",)
# Màn hình 2FA (TOTP) của Google — CHƯA kiểm chứng trên DOM thật (không có quyền truy cập trực tiếp lúc viết);
# xác nhận lại qua form_diagnostics() nếu OTP không được nhận diện đúng, rồi cập nhật selector (INV-7).
_GOOGLE_OTP = 'input#totpPin, input[name="totpPin"], input[type="tel"]'
# Thông báo lỗi khi SAI mật khẩu Google (để REFINE nhãn BAD_CREDENTIAL). Vùng aria-live="assertive" của Google
# RỖNG khi không có lỗi và được BƠM phần tử con (có [jsname]) khi có lỗi → chọn phần tử CON làm tín hiệu
# locale-independent; kèm fallback class (dễ đổi). CHƯA kiểm chứng DOM thật — không khớp thì vẫn AN TOÀN: rơi
# về BLOCKED, KHÔNG bao giờ thành LOGGED_IN. Xác nhận qua form_diagnostics() rồi cập nhật (INV-7, như _GOOGLE_OTP).
_GOOGLE_PASSWORD_ERROR = (
    'div[aria-live="assertive"] [jsname]',
    "span.Ekjuhf",
)


def _redirected_to_login(url: str, markers: tuple[str, ...]) -> bool:
    """URL có SEGMENT trùng marker (vd /login) = chưa đăng nhập (so segment, không substring — như base)."""
    if not markers:
        return False
    segments = {seg.lower() for seg in urlparse(url).path.split("/") if seg}
    return any(m.lower() in segments for m in markers)


class GoogleLogin:
    """Đăng nhập platform qua Google OAuth. Credential.username/password là TÀI KHOẢN GOOGLE."""

    def __init__(self, spec: LoginFormSpec) -> None:
        self._spec = spec

    def login(self, page: LoginPage, credential: Credential) -> LoginResult:
        if not credential.username or not credential.password:
            raise LoginError("đăng nhập Google cần email + mật khẩu Google")
        spec = self._spec
        start_url = spec.google_login_url or spec.login_url
        logger.info("google-login v2 (TikTok/YouTube qua Google, 2FA tùy chọn): mở %s", start_url)
        page.goto(start_url)

        # X/TikTok: click "Continue with Google" để mở OAuth (YouTube: vào thẳng Google, google_button_texts rỗng).
        if spec.google_button_texts:
            if not any(page.click_text(t) for t in spec.google_button_texts):
                logger.warning("google-login: không thấy nút 'Continue with Google' — platform đổi giao diện?")
                return LoginResult(
                    LoginOutcome.FORM_ERROR, LoginMethod.INFO, detail="google_button_not_found"
                )
            # OAuth Google mở popup MỚI (TikTok) HOẶC redirect CÙNG tab (X) → chuyển sang tab mới nếu có.
            page.use_latest_tab()
            # XÁC MINH đã sang trang Google TRƯỚC khi gõ email. Nếu KHÔNG (click chưa "ăn" / popup mở trễ / nút
            # nằm trong iframe), `_GOOGLE_EMAIL` = input[type=email] sẽ khớp NHẦM ô "Email or username" GỐC của
            # platform (X) → gõ email vào đó rồi Enter kích hoạt "Continue" gốc (đúng bug đã thấy). Chưa xác nhận
            # được Google → BLOCKED, KHÔNG gõ gì (INV-1/INV-2). Thử use_latest_tab lần 2 phòng popup mở trễ.
            if not page.wait_url_contains(_GOOGLE_HOST, _STEP_TIMEOUT):
                page.use_latest_tab()
                if not page.wait_url_contains(_GOOGLE_HOST, _STEP_TIMEOUT):
                    logger.warning(
                        "google-login: bấm 'Continue with Google' KHÔNG mở được trang Google (nút đổi/iframe/"
                        "popup bị chặn?) — KHÔNG gõ email vào ô platform; đường tin cậy là login-by-cookie"
                    )
                    return LoginResult(
                        LoginOutcome.BLOCKED, LoginMethod.INFO, detail="google_oauth_not_opened"
                    )

        # Email Google → bấm "Next".
        if not page.fill(_GOOGLE_EMAIL, credential.username):
            logger.warning("google-login: không thấy ô email Google")
            return LoginResult(LoginOutcome.FORM_ERROR, LoginMethod.INFO, detail="google_email_not_found")
        email_url = page.current_url
        self._advance(page, _GOOGLE_EMAIL, _GOOGLE_EMAIL_NEXT)

        # XÁC MINH đã rời bước email TRƯỚC khi đụng mật khẩu. Trang identifier của Google có ô mật khẩu ẨN (cho
        # trình quản lý mật khẩu) → `wait_present(password)` một mình KHÔNG chứng minh đã sang bước mật khẩu;
        # nếu chưa sang mà vẫn fill thì mật khẩu bị gõ vào ô EMAIL (email+password dính liền — bug thật đã gặp,
        # log ra `google_password_step_stuck`). URL đổi = đã sang bước mật khẩu; KHÔNG đổi = Google chặn/từ chối
        # email (vd sai định dạng, "couldn't find your account", chặn bot) → BLOCKED, KHÔNG gõ mật khẩu (INV-1).
        if not page.wait_url_change(email_url, _STEP_TIMEOUT):
            logger.warning(
                "google-login: bấm Next ở bước email KHÔNG chuyển bước — Google chặn/từ chối email; "
                "đường tin cậy là login-by-cookie"
            )
            return LoginResult(
                LoginOutcome.BLOCKED, LoginMethod.INFO, detail="google_email_step_stuck"
            )

        # Chờ ô mật khẩu THẬT (bước pwd) hiện ra. Không hiện = Google chặn browser tự động ('may not be secure')
        # / bắt xác minh (báo rõ). Vì đã xác minh đổi bước ở trên nên đây là ô mật khẩu thật, không phải ô ẩn.
        if not page.wait_present(_GOOGLE_PASSWORD, _STEP_TIMEOUT):
            logger.warning(
                "google-login: không hiện ô mật khẩu sau khi sang bước pwd — Google bắt xác minh; "
                "đường tin cậy là login-by-cookie"
            )
            return LoginResult(
                LoginOutcome.BLOCKED, LoginMethod.INFO, detail="google_blocked_or_verify"
            )
        if not page.fill(_GOOGLE_PASSWORD, credential.password):
            return LoginResult(
                LoginOutcome.FORM_ERROR, LoginMethod.INFO, detail="google_password_not_found"
            )
        self._advance(page, _GOOGLE_PASSWORD, _GOOGLE_PASSWORD_NEXT)

        # `wait_present` cho DOM kịp render sau khi submit mật khẩu (2FA là bước TÙY CHỌN — không dùng giá trị
        # trả về để quyết định). `has_element` (timeout=0) mới là tín hiệu quyết định nhánh.
        page.wait_present(_GOOGLE_OTP, _GOOGLE_OTP_TIMEOUT)

        # (b) Ô mật khẩu VẪN còn = mật khẩu KHÔNG được chấp nhận (sai mật khẩu HOẶC Google bắt xác minh) →
        # TUYỆT ĐỐI không đi tiếp _verify: cookie session CŨ còn sót trong profile GemLogin sẽ khiến _verify báo
        # LOGGED_IN GIẢ rồi chụp cookie CHẾT làm 'fresh_cookie' (đúng lỗi "login thành công nhưng pool guard
        # fail"). Có tín hiệu lỗi → BAD_CREDENTIAL; không có → BLOCKED. Cả hai KHÔNG phải DEAD, KHÔNG LOGGED_IN.
        if page.has_element(_GOOGLE_PASSWORD):
            if page.has_element(*_GOOGLE_PASSWORD_ERROR):
                logger.warning("google-login: Google báo SAI mật khẩu → BAD_CREDENTIAL (không đoán logged-in)")
                return LoginResult(
                    LoginOutcome.BAD_CREDENTIAL, LoginMethod.INFO, detail="google_wrong_password"
                )
            logger.warning(
                "google-login: kẹt ở bước mật khẩu (không rời màn được) → Google chặn/bắt xác minh; "
                "đường tin cậy là login-by-cookie"
            )
            return LoginResult(
                LoginOutcome.BLOCKED, LoginMethod.INFO, detail="google_password_step_stuck"
            )

        # (a) 2FA TÙY CHỌN: có thể gặp màn CHỌN phương thức trước → chọn "Google Authenticator app" → nhập TOTP
        # tự sinh từ otp_secret. Không bật 2FA → trả None, đi verify. Sai/thiếu → BAD/OTP_REQUIRED (không đoán).
        otp_result = self._handle_two_factor(page, credential)
        if otp_result is not None:
            return otp_result

        # (c) Xong OAuth (X redirect / TikTok popup) → quay về tab platform để verify guard đăng nhập.
        page.use_main_tab()
        return self._verify(page)

    def _advance(self, page: LoginPage, field_selector: str, next_selector: str) -> None:
        """Submit một bước của Google. Google submit bằng nút "Next" — click qua CDP KHÔNG phải lúc nào cũng
        kích hoạt submit khi gõ Enter (form Material/React, giống nút của X). Ưu tiên click nút theo id, fallback
        theo text đa ngôn ngữ, cuối cùng mới Enter trong ô (nhắm đúng form nên an toàn). Việc ĐÃ chuyển bước hay
        chưa do caller xác minh bằng `wait_url_change` — helper này chỉ CỐ submit, không tự kết luận."""
        if next_selector and page.click(next_selector):
            return
        if any(page.click_text(t) for t in _GOOGLE_NEXT_TEXTS):
            return
        page.press_enter(field_selector)

    def _handle_two_factor(self, page: LoginPage, credential: Credential) -> LoginResult | None:
        """2FA Google (TÙY CHỌN — chỉ khi tài khoản bật). Tài khoản có NHIỀU phương thức → Google hiện màn CHỌN
        trước; phải chọn "Google Authenticator app" thì mới hiện ô nhập mã TOTP. Có otp_secret → tự sinh TOTP
        (đồng nhất app Authenticator, cùng thuật toán RFC 6238) điền tiếp rồi để `_verify()` chốt; không có
        secret → OTP_REQUIRED (cần người — INV-1). Trả None nghĩa: KHÔNG có 2FA (đi verify) HOẶC đã điền mã xong.
        """
        # Ô nhập mã chưa hiện + còn trên trang Google → có thể đang ở màn CHỌN phương thức: chọn Authenticator
        # app. Chỉ thử khi còn accounts.google.com (đã rời Google = đăng nhập xong, không phí thời gian tìm).
        if not page.has_element(_GOOGLE_OTP) and "accounts.google.com" in page.current_url:
            if any(page.click_text(t) for t in _GOOGLE_TOTP_OPTION_TEXTS):
                page.wait_present(_GOOGLE_OTP, _STEP_TIMEOUT)
        # Không có ô mã 2FA → tài khoản KHÔNG bật authenticator (hoặc phương thức khác) → để verify quyết định.
        if not page.has_element(_GOOGLE_OTP):
            return None
        if not credential.otp_secret:
            logger.warning("google-login: Google bắt 2FA (authenticator) nhưng KHÔNG có otp_secret → OTP_REQUIRED")
            return LoginResult(
                LoginOutcome.OTP_REQUIRED, LoginMethod.INFO, detail="google_otp_needed_no_secret"
            )
        code = generate_totp(credential.otp_secret)
        if not page.fill(_GOOGLE_OTP, code):
            return LoginResult(
                LoginOutcome.FORM_ERROR, LoginMethod.INFO, detail="google_otp_field_not_found"
            )
        self._advance(page, _GOOGLE_OTP, _GOOGLE_OTP_NEXT)
        return None

    def _verify(self, page: LoginPage) -> LoginResult:
        """Verify guard đăng nhập trên platform SAU khi qua Google OAuth.

        KHÁC CookieLogin (INV-8 cookie-first) một cách CÓ CHỦ Ý: ở đây KHÔNG tin "tên cookie có mặt" làm bằng
        chứng đăng nhập. Login qua Google là tương tác THẬT; nếu OAuth không hoàn tất (sai mật khẩu / Google
        bắt xác minh giữa chừng) thì cookie session CŨ còn sót trong profile GemLogin vẫn mang tên `sessionid`/
        `LOGIN_INFO` → guard "tên cookie" sẽ báo LOGGED_IN GIẢ rồi chụp luôn cookie CHẾT làm `fresh_cookie` →
        nạp vào pool test guard fail (đúng triệu chứng đã gặp). Vì vậy chỉ tin GUARD DOM do SERVER render
        (avatar/profile-icon qua data-e2e/id — chỉ hiện khi phiên THẬT hợp lệ, cookie phía client sót lại
        KHÔNG làm server render nó). Guard vắng → COOKIE_DEAD (INV-1/INV-2: thà báo lỗi còn hơn trả sai âm thầm).
        """
        spec = self._spec
        # CHỜ OAuth postback/redirect lập phiên platform TRƯỚC khi force-goto (xem _OAUTH_SETTLE_TIMEOUT): guard
        # TỰ hiện trên tab platform khi X nhận token và điều hướng về home. goto sớm sẽ cắt callback → COOKIE_DEAD
        # oan. Chỉ CHỜ (không dùng giá trị trả về — has_element cuối mới quyết định). Rời Google mới có ý nghĩa chờ.
        guard_selectors = ",".join(s for s in spec.verify_selectors if s)
        page.wait_present(guard_selectors, _OAUTH_SETTLE_TIMEOUT)
        page.goto(spec.home_url)
        # Chờ trang render tín hiệu QUYẾT ĐỊNH (guard đăng nhập HOẶC block) trước khi đọc — chỉ để CHỜ, không
        # dùng giá trị trả về (has_element bên dưới mới quyết định), giống idiom nhánh 2FA phía trên.
        wait_selectors = ",".join(s for s in (*spec.verify_selectors, *spec.block_selectors) if s)
        page.wait_present(wait_selectors, _VERIFY_RENDER_TIMEOUT)
        if spec.block_selectors and page.has_element(*spec.block_selectors):
            return LoginResult(LoginOutcome.BLOCKED, LoginMethod.INFO, detail="captcha_or_challenge")
        if _redirected_to_login(page.current_url, spec.login_url_markers):
            # Vẫn ở trang login sau khi qua Google = chưa lập được phiên (lỗi profile, không kết luận target).
            return LoginResult(
                LoginOutcome.COOKIE_DEAD, LoginMethod.INFO, detail="not_logged_in_after_google"
            )
        # GUARD DOM là tín hiệu QUYẾT ĐỊNH (server-rendered) — xem docstring vì sao KHÔNG dùng lối tắt tên cookie.
        # Detail RIÊNG cho Google (khác `login_guard_failed` của CookieLogin — cookie chết là chuyện thường,
        # không cần DIAG) để bật DIAG ĐÚNG ca này: qua được OAuth mà guard vẫn vắng = X chưa lập phiên (callback
        # chưa xong / bị chặn) HOẶC guard selector đã lỗi thời → cần xem DOM home thật để phân biệt (INV-7).
        if not page.has_element(*spec.verify_selectors):
            return LoginResult(
                LoginOutcome.COOKIE_DEAD, LoginMethod.INFO, detail="google_verify_guard_failed"
            )
        return LoginResult(LoginOutcome.LOGGED_IN, LoginMethod.INFO, fresh_cookie=page.cookies_string())
