"""Adapter GemLogin: CRUD profile + mở/tắt browser + lấy CDP address (station-management-design §4, §5).

Ranh giới (ADR-0006): vòng đời browser THẬT do **GemLogin** quản (mỗi profile 1 tiến trình + vân tay +
proxy sticky — INV-6/INV-7). Adapter chỉ mở/tắt qua API GemLogin và trả **CDP address** để DrissionPage
attach (`ChromiumOptions().set_address(addr)`), KHÔNG tự quản vòng đời browser. Không log cookie/CDP thô
(INV-12).

- `RealGemLoginAdapter`: gọi API HTTP local GemLogin (CRUD + start/stop, lấy remote debugging address).
- `FakeGemLoginAdapter`: dev/test không cần GemLogin — quản danh sách profile in-memory và, khi "mở
  browser", spawn một tiến trình con THẬT (cây tiến trình) để test process hygiene (kill cây → không sót
  con). Trả CDP address giả.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..process.kill import kill_process_tree

logger = logging.getLogger("fastcheck.worker.gemlogin")

# Nhãn nội bộ nhét vào field `note` của profile GemLogin để mang platform (GemLogin không có field
# platform riêng). Nhờ vậy đồng bộ (§3) biết profile phục vụ nền tảng nào mà không cần bảng phụ.
_PLATFORM_NOTE_PREFIX = "fastcheck-platform="


class GemLoginError(RuntimeError):
    """Lỗi từ API GemLogin (success=false hoặc thiếu dữ liệu). Báo ra, KHÔNG nuốt (INV-1)."""


def _platform_from_note(note: str | None) -> str:
    """Rút platform từ field note (`fastcheck-platform=TIKTOK`). Không có nhãn → "" (sync sẽ bỏ qua)."""
    if not note or _PLATFORM_NOTE_PREFIX not in note:
        return ""
    tail = note.split(_PLATFORM_NOTE_PREFIX, 1)[1].strip().split()
    return tail[0] if tail else ""


@dataclass(frozen=True)
class BrowserHandle:
    """Kết quả mở browser: profile + địa chỉ CDP (để DrissionPage attach) + pid (để giám sát/kill cây)."""

    profile_id: str
    cdp_address: str
    pid: int


@dataclass(frozen=True)
class ProfileSummary:
    """Một profile GemLogin trên máy (để đồng bộ lên server — §3). KHÔNG chứa cookie/credential (INV-12)."""

    gemlogin_profile_id: str
    platform: str
    name: str | None = None
    gem_status: str | None = None


@dataclass(frozen=True)
class ProfileSpec:
    """Thông tin tạo profile GemLogin. Cookie/credential KHÔNG đi qua đây (INV-12) — inject lúc mở browser.

    `config` = ProfileConfig (dict, mirror 4 tab GemLogin) nếu dashboard chỉ định vân tay cụ thể; None → để
    GemLogin tự sinh (hành vi cũ). `platform` KHÔNG dùng lúc tạo nữa (không gán nền tảng lúc tạo — pool tự
    phân loại + gán khi "Nạp tài khoản"); giữ lại cho FakeGemLoginAdapter/tương thích, mặc định None.
    """

    platform: str | None = None
    name: str | None = None
    proxy: str | None = None
    config: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# os.type trong ProfileConfig ĐÃ là giá trị GemLogin sẵn (Windows/macOS/Android/IOS/Linux) — chỉ validate.
_VALID_OS_TYPES = frozenset({"Windows", "macOS", "Android", "IOS", "Linux"})
# WebRTC — chỉ 2 chế độ Replace/Disable (bỏ 'real'/dùng IP thật). GemLogin dùng CHUỖI `web_rtc` cho CẢ create
# lẫn update, vocabulary RIÊNG: Replace='noise', Disable='disable' (KHÔNG phải 'disabled').
# (Trước đây create gửi SỐ `webrtc_mode` nhưng user test 2026-07-24: tạo profile chọn Replace vẫn ra Disable —
#  GemLogin KHÔNG áp dụng webrtc_mode lúc create → chuyển create sang chuỗi `web_rtc` giống update.)
_WEBRTC_STR = {"replace": "noise", "disabled": "disable"}


def _os_block(config: dict[str, Any]) -> dict[str, str] | None:
    os_type = config.get("os_type")
    if os_type not in _VALID_OS_TYPES:
        return None
    return {"type": str(os_type), "version": str(config.get("os_version") or "")}


def _common_profile_fields(config: dict[str, Any]) -> dict[str, Any]:
    """Field chung create+update: os, browser_version, user_agent (chỉ khi custom), country/language/time_zone."""
    p: dict[str, Any] = {}
    osb = _os_block(config)
    if osb:
        p["os"] = osb
    if config.get("browser_version"):
        p["browser_version"] = config["browser_version"]
    # UA chỉ gửi khi custom; auto → để GemLogin tự sinh (khuyến nghị của GemLogin).
    if config.get("user_agent_mode") == "custom" and config.get("user_agent"):
        p["user_agent"] = config["user_agent"]
    if config.get("country"):
        p["country"] = config["country"]
    if config.get("language"):
        p["language"] = config["language"]
    if config.get("time_zone"):
        p["time_zone"] = config["time_zone"]
    return p


def profile_config_create_fields(config: dict[str, Any] | None) -> dict[str, Any]:
    """Map ProfileConfig → body `POST /api/profiles/create`.

    Create dùng CỜ BOOLEAN `is_noise_*`/`is_masked_*`/`is_random_screen` + `startup_urls` (số nhiều) + `web_rtc`
    (CHUỖI, không phải webrtc_mode số — xem chú thích _WEBRTC_STR). Nhóm GUI-only không có field → không gửi.
    Fingerprint chi tiết (resolution/webgl_vendor/webgl_renderer) CHỈ set được lúc create (không có ở update).
    """
    if not config:
        return {}
    p = _common_profile_fields(config)
    if config.get("startup_url"):
        p["startup_urls"] = config["startup_url"]  # create: số nhiều
    p["is_noise_canvas"] = config.get("canvas") == "noise"
    p["is_noise_webgl"] = config.get("webgl_image") == "noise"
    p["is_noise_client_rect"] = config.get("client_rects") == "noise"
    p["is_noise_audio_context"] = config.get("audio_context") == "noise"
    p["is_random_screen"] = config.get("screen_resolution") == "random"
    # webgl_metadata: custom HOẶC random đều = mask; 'real' = không mask.
    p["is_masked_webgl_data"] = config.get("webgl_metadata") in ("custom", "random")
    p["is_masked_media_device"] = config.get("media_device") == "noise"
    web_rtc = config.get("web_rtc")
    if web_rtc in _WEBRTC_STR:
        p["web_rtc"] = _WEBRTC_STR[web_rtc]  # CHUỖI: noise=Replace, disable=Disable (create cũng dùng chuỗi)
    # Screen resolution CUSTOM → gửi giá trị cụ thể (best-effort: tên field `resolution` GemLogin chưa xác nhận).
    if config.get("screen_resolution") == "custom" and config.get("resolution"):
        p["resolution"] = config["resolution"]
    # WebGL metadata CUSTOM → Unmasked Vendor/Renderer (GemLogin schema CÓ 2 field này).
    if config.get("webgl_metadata") == "custom":
        if config.get("webgl_vendor"):
            p["webgl_vendor"] = config["webgl_vendor"]
        if config.get("webgl_renderer"):
            p["webgl_renderer"] = config["webgl_renderer"]
    return p


def profile_config_update_fields(config: dict[str, Any] | None) -> dict[str, Any]:
    """Map ProfileConfig → body `POST /api/profiles/update/{id}` — CHỈ field CHUỖI như example (đã test).

    QUAN TRỌNG (user test thực tế): update PHẢI giống example — CHỈ `web_rtc`/`webgl`/`canvas` (CHUỖI) +
    `startup_url` (số ít) + os/basics. TRỘN THÊM `webrtc_mode`/`is_noise_*` (create-style) khiến GemLogin KHÔNG
    NHẬN → tuyệt đối không gửi ở update. `web_rtc` dùng vocabulary WRITE riêng (noise=Replace, disable=Disable).
    Các field vân tay khác (audio/media/client_rects/screen/webgl_metadata) update KHÔNG có field → chỉ đổi
    được lúc create.
    """
    if not config:
        return {}
    p = _common_profile_fields(config)
    if config.get("startup_url"):
        p["startup_url"] = config["startup_url"]  # update: số ít
    if config.get("canvas"):
        p["canvas"] = config["canvas"]  # 'real'|'noise'
    if config.get("webgl_image"):
        p["webgl"] = config["webgl_image"]  # update dùng key `webgl`
    web_rtc = config.get("web_rtc")
    if web_rtc in _WEBRTC_STR:
        p["web_rtc"] = _WEBRTC_STR[web_rtc]  # CHUỖI: noise=Replace, disable=Disable
    return p


class GemLoginAdapter(Protocol):
    """Giao diện chung real/fake. Mọi thao tác idempotent ở tầng ws_client (command_id — INV-14)."""

    def create_profile(self, spec: ProfileSpec) -> str: ...

    def update_profile(self, gemlogin_profile_id: str, changes: dict[str, Any]) -> None: ...

    def delete_profile(self, gemlogin_profile_id: str) -> None: ...

    def list_profiles(self) -> list[ProfileSummary]: ...

    def open_browser(self, gemlogin_profile_id: str, cookie: str = "") -> BrowserHandle: ...

    def close_browser(self, gemlogin_profile_id: str) -> None: ...


# ── Fake (dev/test) ───────────────────────────────────────────────────────────
class FakeGemLoginAdapter:
    """Không cần GemLogin. Mở browser = spawn tiến trình con THẬT (cây tiến trình) để test kill cây (INV-9)."""

    def __init__(self, platform: str = "TIKTOK", fake_browser_ttl_seconds: float = 300.0) -> None:
        self._default_platform = platform
        self._ttl = fake_browser_ttl_seconds
        self._profiles: dict[str, ProfileSummary] = {}
        self._browsers: dict[str, subprocess.Popen[bytes]] = {}

    def create_profile(self, spec: ProfileSpec) -> str:
        gid = f"fake-{uuid.uuid4().hex[:12]}"
        self._profiles[gid] = ProfileSummary(
            gemlogin_profile_id=gid, platform=spec.platform or "", name=spec.name, gem_status="closed"
        )
        logger.info("fake: tạo profile %s (platform=%s)", gid, spec.platform)
        return gid

    def update_profile(self, gemlogin_profile_id: str, changes: dict[str, Any]) -> None:
        cur = self._profiles.get(gemlogin_profile_id)
        if cur is None:
            raise KeyError(f"profile không tồn tại: {gemlogin_profile_id}")
        self._profiles[gemlogin_profile_id] = ProfileSummary(
            gemlogin_profile_id=gemlogin_profile_id,
            platform=str(changes.get("platform", cur.platform)),
            name=changes.get("account_label", cur.name),
            gem_status=cur.gem_status,
        )
        logger.info("fake: cập nhật profile %s", gemlogin_profile_id)

    def delete_profile(self, gemlogin_profile_id: str) -> None:
        self.close_browser(gemlogin_profile_id)
        self._profiles.pop(gemlogin_profile_id, None)
        logger.info("fake: xoá profile %s", gemlogin_profile_id)

    def list_profiles(self) -> list[ProfileSummary]:
        return list(self._profiles.values())

    def open_browser(self, gemlogin_profile_id: str, cookie: str = "") -> BrowserHandle:
        # Idempotent theo profile (INV-6: 1 profile = 1 browser). Đang mở → trả handle cũ, KHÔNG spawn thêm.
        existing = self._browsers.get(gemlogin_profile_id)
        if existing is not None and existing.poll() is None:
            logger.info("fake: browser %s đã mở — trả handle cũ (idempotent)", gemlogin_profile_id)
            return self._handle(gemlogin_profile_id, existing.pid)
        # INV-2/§6.8e: cookie inject TRƯỚC điều hướng (fake chưa có browser thật — chỉ log độ dài, INV-12).
        logger.debug("fake: inject cookie trước điều hướng (len=%d)", len(cookie or ""))
        proc = subprocess.Popen(  # noqa: S603 — tiến trình 'browser giả' để test kill cây
            [sys.executable, "-m", "fastcheck_worker.browser._fake_browser", str(self._ttl)],
        )
        self._browsers[gemlogin_profile_id] = proc
        if gemlogin_profile_id not in self._profiles:
            self._profiles[gemlogin_profile_id] = ProfileSummary(
                gemlogin_profile_id=gemlogin_profile_id,
                platform=self._default_platform,
                gem_status="open",
            )
        logger.info("fake: mở browser %s pid=%d", gemlogin_profile_id, proc.pid)
        return self._handle(gemlogin_profile_id, proc.pid)

    def close_browser(self, gemlogin_profile_id: str) -> None:
        proc = self._browsers.pop(gemlogin_profile_id, None)
        if proc is None:
            return
        # Đóng = kill CẢ CÂY (INV-9). page.quit() ở đường DrissionPage tương ứng, nhưng cây tiến trình
        # 'browser giả' phải dọn bằng kill cây để không sót con mồ côi.
        killed = kill_process_tree(proc.pid)
        logger.info("fake: đóng browser %s (kill %d tiến trình)", gemlogin_profile_id, killed)

    def _handle(self, gemlogin_profile_id: str, pid: int) -> BrowserHandle:
        # CDP address GIẢ (không có browser thật). Đường thật trả remote debugging address của GemLogin.
        return BrowserHandle(
            profile_id=gemlogin_profile_id,
            cdp_address=f"127.0.0.1:0/fake/{gemlogin_profile_id}",
            pid=pid,
        )


# ── Real (máy trạm có GemLogin) ───────────────────────────────────────────────
class RealGemLoginAdapter:
    """Gọi API HTTP local của GemLogin (bản Electron, mặc định `http://127.0.0.1:1010`).

    Đường dẫn đã KIỂM CHỨNG trực tiếp với bản GemLogin đã cài (2026-07-18):
      - `GET  /api/profiles`              — liệt kê   → {success, message, data:[{id, name, note, ...}]}
      - `POST /api/profiles/create`       — tạo       → {success, message, data:{id, ...}}
      - `POST /api/profiles/update/{id}`  — sửa       → {success, message}
      - `GET  /api/profiles/delete/{id}`  — xoá       → BẢN FREE trả success=false (không hỗ trợ)
      - `GET  /api/profiles/start/{id}`   — mở        → data.remote_debugging_address (host:port); KHÔNG có pid
      - `GET  /api/profiles/close/{id}`   — đóng      → {success, message}
      - `GET  /api/status`                — trạng thái server (activeBrowsers, features)
    Envelope chung `{success, message, data}`. success=false → GemLoginError (không nuốt lỗi).
    DrissionPage attach vào `remote_debugging_address`. Chỉ chạy ở máy trạm thật (GEMLOGIN_MODE=real).
    """

    def __init__(
        self,
        api_url: str,
        timeout_seconds: float = 30.0,
        start_wait_seconds: float = 180.0,
        close_settle_seconds: float = 2.0,
        cdp_ready_wait_seconds: float = 30.0,
    ) -> None:
        self._base = api_url.rstrip("/")
        self._timeout = timeout_seconds
        # Lần mở browser ĐẦU TIÊN GemLogin có thể tải Chromium (chậm) → chờ tới ngưỡng này rồi mới bỏ cuộc.
        self._start_wait = start_wait_seconds
        # Nghỉ sau khi ĐÓNG để GemLogin kịp giải phóng profile TRƯỚC khi job kế mở lại — giảm kẹt "being
        # opened" khi dùng lại CÙNG profile liên tiếp (churn). Đủ profile thì gần như không chạm tới đây.
        self._close_settle = close_settle_seconds
        # Sau khi GemLogin trả CDP address, browser có thể CHƯA có tab 'page' sẵn sàng (rõ nhất khi mở profile
        # MỚI lần đầu). Chờ tới ngưỡng này để tab điều hướng được thực sự tồn tại rồi mới cho attach — chống
        # race "browser dựng lên mà kịch bản không thao tác gì, phải chạy lần 2 mới được".
        self._cdp_ready_wait = cdp_ready_wait_seconds

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self._base}{path}", data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 — API local
            raw = resp.read().decode("utf-8", errors="replace")
        parsed: Any = json.loads(raw) if raw else {}
        if not isinstance(parsed, dict):
            return {"data": parsed}
        return parsed

    def _unwrap(self, resp: dict[str, Any], what: str) -> Any:
        """Kiểm envelope {success, message, data}. success=false → GemLoginError (báo ra, INV-1)."""
        if resp.get("success") is False:
            raise GemLoginError(f"{what}: {resp.get('message', 'GemLogin trả success=false')}")
        return resp.get("data")

    def create_profile(self, spec: ProfileSpec) -> str:
        # Tên profile = `profile_name` (đúng Swagger GemLogin — KHÔNG phải `name`). KHÔNG gán platform lúc tạo
        # (pool tự phân loại + gán khi "Nạp tài khoản" — theo yêu cầu người dùng).
        payload: dict[str, Any] = {}
        if spec.name:
            payload["profile_name"] = spec.name
        # Vân tay cụ thể (dashboard) → format CREATE (cờ bool + web_rtc chuỗi + resolution/webgl_vendor/renderer).
        # Có config = KHÔNG để GemLogin random.
        payload.update(profile_config_create_fields(spec.config))
        # Proxy phẳng chỉ dùng nếu config CHƯA đặt raw_proxy (tương thích ngược tạo nhanh).
        if spec.proxy and "raw_proxy" not in payload:
            payload["raw_proxy"] = spec.proxy  # GemLogin lưu proxy ở field raw_proxy
        payload.update(spec.extra)  # hook mở rộng cuối cùng (nếu ai đó truyền field GemLogin thô)
        resp = self._request("POST", "/api/profiles/create", payload)
        data = self._unwrap(resp, "create_profile")
        gid = str((data or {}).get("id", ""))
        if not gid:
            raise GemLoginError("GemLogin không trả id profile khi tạo")
        return gid

    def update_profile(self, gemlogin_profile_id: str, changes: dict[str, Any]) -> None:
        # Map → field UPDATE của Swagger GemLogin: account_label→`profile_name`, proxy→`raw_proxy`, config→vân
        # tay (web_rtc/webgl/canvas chuỗi, startup_url số ít…). KHÔNG tự gán platform vào note (pool lo gán).
        body: dict[str, Any] = {}
        if changes.get("account_label") is not None:
            body["profile_name"] = changes["account_label"]
        # Vân tay (panel Update dashboard) → format UPDATE (CHUỖI như example — KHÔNG trộn webrtc_mode/bool).
        body.update(profile_config_update_fields(changes.get("config")))
        if changes.get("proxy") is not None and "raw_proxy" not in body:
            body["raw_proxy"] = changes["proxy"]
        resp = self._request("POST", f"/api/profiles/update/{gemlogin_profile_id}", body)
        self._unwrap(resp, "update_profile")

    def get_profile(self, gemlogin_profile_id: str) -> dict[str, Any]:
        """Đọc chi tiết một profile (GET /api/profile/{id}) — dùng để VERIFY field đã 'dính' sau create/update."""
        resp = self._request("GET", f"/api/profile/{gemlogin_profile_id}")
        data = self._unwrap(resp, "get_profile")
        return data if isinstance(data, dict) else {}

    def delete_profile(self, gemlogin_profile_id: str) -> None:
        # BẢN FREE: GemLogin trả success=false ("The free version does not work this feature").
        # KHÔNG nuốt: _unwrap ném GemLoginError → ws_client trả ack ok=false + detail (báo ra rõ ràng).
        resp = self._request("GET", f"/api/profiles/delete/{gemlogin_profile_id}")
        self._unwrap(resp, "delete_profile")

    def list_profiles(self) -> list[ProfileSummary]:
        resp = self._request("GET", "/api/profiles")
        data = self._unwrap(resp, "list_profiles")
        items = data if isinstance(data, list) else []
        out: list[ProfileSummary] = []
        for it in items:
            out.append(
                ProfileSummary(
                    gemlogin_profile_id=str(it.get("id", "")),
                    platform=_platform_from_note(it.get("note")),
                    name=it.get("name"),
                    # GemLogin không trả trạng thái open/closed ở list → để None (không suy đoán).
                    gem_status=it.get("status"),
                )
            )
        return out

    def open_browser(self, gemlogin_profile_id: str, cookie: str = "") -> BrowserHandle:
        # KHÔNG log cookie (INV-12). Cookie được nạp TRƯỚC điều hướng ở tầng page source (DrissionPage
        # set.cookies rồi mới .get(url)) — INV-2; open_browser chỉ lo mở GemLogin + lấy CDP address.
        logger.debug("real: mở profile %s (cookie len=%d)", gemlogin_profile_id, len(cookie or ""))
        addr = self._start_and_wait(gemlogin_profile_id)
        # Chờ browser có tab 'page' điều hướng được TRƯỚC khi trả handle (DrissionPage sẽ attach ngay sau đó).
        self._wait_cdp_page_target(addr)
        pid = self._pid_for_cdp_address(addr)
        return BrowserHandle(profile_id=gemlogin_profile_id, cdp_address=addr, pid=pid)

    def _wait_cdp_page_target(self, cdp_address: str) -> None:
        """Chờ tới khi CDP có target `type=page` (tab điều hướng được) rồi mới cho DrissionPage attach.

        Vì sao: `/api/profiles/start` trả `remote_debugging_address` NGAY khi cổng debug listen, nhưng lần đầu
        mở profile MỚI, Chrome còn dựng tab đầu + áp vân tay → chưa có target `page`. Attach + `.get()` trong
        khoảng này bị race: lệnh điều hướng rơi vào khoảng trống → "browser dựng lên mà không thao tác gì, chạy
        lần 2 mới được". Chờ tín hiệu THẬT (có tab page) thay vì đoán/sleep cứng (INV-1). Hết giờ mà vẫn chưa
        thấy → KHÔNG chặn hẳn (browser có thể vẫn dùng được) nhưng LOG cảnh báo để không hỏng âm thầm.
        """
        deadline = time.monotonic() + self._cdp_ready_wait
        url = f"http://{cdp_address}/json"
        last_err = "n/a"
        while True:
            try:
                with urllib.request.urlopen(url, timeout=self._timeout) as resp:  # noqa: S310 — CDP local
                    targets = json.loads(resp.read().decode("utf-8", errors="replace"))
                if isinstance(targets, list) and any(
                    isinstance(t, dict) and t.get("type") == "page" for t in targets
                ):
                    # Nghỉ rất ngắn cho tab ổn định hẳn trước khi attach (best-effort).
                    time.sleep(0.3)
                    return
            except Exception as exc:  # noqa: BLE001 — cổng CDP có thể chưa sẵn sàng ngay; chờ tiếp, không nuốt
                last_err = type(exc).__name__
            if time.monotonic() >= deadline:
                logger.warning(
                    "CDP %s chưa thấy target 'page' sau %.0fs (lỗi cuối=%s) — vẫn attach thử, "
                    "kịch bản có thể cần chạy lại",
                    cdp_address,
                    self._cdp_ready_wait,
                    last_err,
                )
                return
            time.sleep(0.5)

    def _start_and_wait(self, gid: str) -> str:
        """Gọi start, chờ tới khi có remote_debugging_address (lần đầu có thể tải Chromium — chậm)."""
        deadline = time.monotonic() + self._start_wait
        while True:
            resp = self._request("GET", f"/api/profiles/start/{gid}")
            # Đang mở dở ("Profile is currently being opened") → success=false NHƯNG là tạm thời: chờ tiếp.
            data = resp.get("data") if resp.get("success") else None
            addr = str((data or {}).get("remote_debugging_address", "")) if data else ""
            if addr:
                return addr
            msg = str(resp.get("message", ""))
            if resp.get("success") is False and "being opened" not in msg.lower():
                # Lỗi thực sự (profile không tồn tại, ...) — báo ra ngay, không chờ vô ích.
                raise GemLoginError(f"start_profile: {msg or 'không mở được profile'}")
            if time.monotonic() >= deadline:
                raise GemLoginError(f"start_profile: quá hạn chờ mở profile {gid} ({self._start_wait:.0f}s)")
            time.sleep(1.5)

    def _pid_for_cdp_address(self, cdp_address: str) -> int:
        """GemLogin KHÔNG trả pid → tìm PID Chrome qua cổng remote debugging (psutil), best-effort (INV-9).

        Không tìm được (quyền / cổng chưa listen) → 0; monitor bỏ qua pid<=0. GemLogin vẫn tự quản
        vòng đời browser qua /close, nên mất giám sát RAM một browser không gây rò (đóng vẫn sạch).
        """
        try:
            port = int(cdp_address.rsplit(":", 1)[-1])
        except (ValueError, IndexError):
            return 0
        try:
            import psutil  # noqa: PLC0415 — chỉ cần ở real mode

            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr and conn.laddr.port == port and conn.pid:
                    return int(conn.pid)
        except Exception as exc:  # noqa: BLE001 — best-effort, không chặn mở browser
            logger.debug("không tìm được pid cho cổng %s (%s)", port, type(exc).__name__)
        return 0

    def close_browser(self, gemlogin_profile_id: str) -> None:
        # Đóng qua API GemLogin (GemLogin tự kill cây tiến trình browser của nó). /close tha thứ id lạ.
        resp = self._request("GET", f"/api/profiles/close/{gemlogin_profile_id}")
        self._unwrap(resp, "close_browser")
        # Nghỉ ngắn để GemLogin hoàn tất giải phóng trước khi profile được mở lại (chống churn "being opened").
        if self._close_settle > 0:
            time.sleep(self._close_settle)

    def status(self) -> dict[str, Any]:
        """Trạng thái server GemLogin (health-check trước khi dùng): activeBrowsers, features, port."""
        return self._request("GET", "/api/status")


def create_adapter(
    mode: str,
    *,
    gemlogin_api_url: str = "http://127.0.0.1:1010",
    fake_platform: str = "TIKTOK",
    fake_browser_ttl_seconds: float = 300.0,
    start_wait_seconds: float = 180.0,
    close_settle_seconds: float = 2.0,
    cdp_ready_wait_seconds: float = 30.0,
) -> GemLoginAdapter:
    """Chọn adapter theo GEMLOGIN_MODE ('fake' | 'real')."""
    if mode == "fake":
        return FakeGemLoginAdapter(
            platform=fake_platform, fake_browser_ttl_seconds=fake_browser_ttl_seconds
        )
    if mode == "real":
        return RealGemLoginAdapter(
            gemlogin_api_url,
            start_wait_seconds=start_wait_seconds,
            close_settle_seconds=close_settle_seconds,
            cdp_ready_wait_seconds=cdp_ready_wait_seconds,
        )
    raise ValueError(f"GEMLOGIN_MODE không hợp lệ: {mode!r} (chỉ 'fake' | 'real')")
