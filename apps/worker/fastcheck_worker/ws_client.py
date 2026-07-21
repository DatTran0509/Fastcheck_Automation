"""WS client: kết nối orchestrator, đăng ký, heartbeat ~10s, auto-reconnect (exponential backoff).

Xác thực token ở header handshake (INV-12). Lệnh Server→Client validate bằng pydantic (sinh từ JSON Schema)
và xử lý **idempotent theo command_id** (INV-14): lệnh trùng → bỏ qua + trả lại kết quả cũ.

Lệnh hỗ trợ (§4 station-management-design):
  - `script.run`            → chạy detector trong bounded thread pool (INV-10) → `job_result`.
  - `browser.open/close`    → GemLoginAdapter mở/đóng browser (cookie trước điều hướng — INV-2);
                              giám sát RAM/PID (INV-9); forward CDP an toàn (mặc định local — INV-12).
  - `profile.create/update/delete` → CRUD profile GemLogin qua adapter.

Ngoài ra: **đồng bộ danh sách profile** GemLogin lên server (§3) định kỳ + sau register, và **giám sát tài
nguyên** (kill browser vượt RAM). Kết quả check tách `url_status` khỏi `profile_health` (INV-3).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from uuid import UUID, getnode

import psutil
from pydantic import BaseModel, ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from .browser.adapter import ProfileSpec, create_adapter
from .browser.cdp_forward import CdpForwardPolicy
from .browser.adapter import GemLoginError
from .config import WorkerConfig
from .login import LoginError
from .login.execute import execute_login
from .contracts import (
    BrowserCloseCommand,
    BrowserOpenCommand,
    CommandAckMessage,
    CookieRefreshMessage,
    HeartbeatMessage,
    JobProgressMessage,
    JobProgressStep,
    JobResultMessage,
    LoginRunCommand,
    Platform,
    ProfileCreateCommand,
    ProfileDeleteCommand,
    ProfileHealth,
    ProfileSyncMessage,
    ProfileUpdateCommand,
    RegisteredMessage,
    RegisterMessage,
    RunCommand,
    ServerCommand,
    StationInfo,
    StationProfile,
    UrlStatus,
    parse_server_message,
)
from .process.monitor import ResourceMonitor, process_tree_rss_mb
from .runner import CheckRunner

logger = logging.getLogger("fastcheck.worker")


def _mac_address() -> str:
    node = getnode()
    return ":".join(f"{(node >> shift) & 0xFF:02X}" for shift in range(40, -1, -8))


class WorkerClient:
    def __init__(self, config: WorkerConfig) -> None:
        self._config = config
        self._url = f"{config.orchestrator_ws_url}/ws"
        self._station_uuid = UUID(config.station_id)
        # Idempotent (INV-14): command_id đã xử lý + kết quả đã gửi (để trả lại khi nhận trùng).
        self._processed_command_ids: set[str] = set()
        self._command_results: dict[str, BaseModel] = {}
        self._adapter = create_adapter(
            config.gemlogin_mode,
            gemlogin_api_url=config.gemlogin_api_url,
            fake_browser_ttl_seconds=config.fake_browser_ttl_seconds,
            start_wait_seconds=config.browser_start_wait_seconds,
            close_settle_seconds=config.browser_close_settle_seconds,
        )
        # real mode: detector chạy trên browser GemLogin THẬT (adapter dùng chung với browser.open/CRUD).
        # fake mode: adapter=None → detector đọc qua FakePageSource (urllib + fixture), không mở browser.
        self._runner = CheckRunner(
            config.max_concurrency,
            config.job_timeout_seconds,
            adapter=self._adapter if config.gemlogin_mode == "real" else None,
        )
        self._monitor = ResourceMonitor(config.browser_ram_limit_mb)
        # Fail-fast nếu bật forward CDP mà thiếu token (INV-12 — không phơi CDP trần).
        self._cdp_policy = CdpForwardPolicy(config.cdp_forward_enabled, config.cdp_forward_token)
        self._send_lock = asyncio.Lock()  # websockets: không gửi song song trên cùng socket
        self._active_jobs = 0  # current_load cho heartbeat (backpressure — INV-10)
        psutil.cpu_percent()  # prime: lần đọc đầu trả 0.0, các lần sau là delta kể từ lần trước

    async def run_forever(self) -> None:
        backoff = 1.0
        try:
            while True:
                try:
                    await self._connect_and_serve()
                    backoff = 1.0
                except (OSError, ConnectionClosed) as exc:
                    logger.warning("mất kết nối orchestrator (%s) — thử lại sau %.1fs", exc, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
        finally:
            self._runner.shutdown()
            self._close_all_browsers()

    async def _connect_and_serve(self) -> None:
        # INV-12: token đi ở header Authorization của handshake, không nằm trong message.
        headers = [("Authorization", f"Bearer {self._config.ws_auth_token}")]
        async with connect(self._url, additional_headers=headers) as ws:
            await self._register(ws)
            logger.info("đã gửi register cho station %s tới %s", self._config.station_id, self._url)
            await self._sync_profiles(ws)  # đồng bộ ngay sau register (§3)
            await asyncio.gather(
                self._heartbeat_loop(ws),
                self._recv_loop(ws),
                self._profile_sync_loop(ws),
                self._resource_monitor_loop(ws),
            )

    async def _send(self, ws: ClientConnection, payload: str) -> None:
        async with self._send_lock:
            await ws.send(payload)

    async def _register(self, ws: ClientConnection) -> None:
        message = RegisterMessage(
            type="register",
            station=StationInfo(
                station_id=self._station_uuid,
                name=self._config.station_name,
                mac_address=_mac_address(),
                agent_version=self._config.agent_version,
                max_concurrency=self._config.max_concurrency,
            ),
        )
        await self._send(ws, message.model_dump_json())

    async def _heartbeat_loop(self, ws: ClientConnection) -> None:
        interval = self._config.heartbeat_interval_ms / 1000.0
        while True:
            # RAM = footprint cây tiến trình worker (nơi rò RAM lộ ra — INV-9); CPU = tải máy trạm.
            ram_mb = process_tree_rss_mb(os.getpid())
            cpu_percent = psutil.cpu_percent()
            heartbeat = HeartbeatMessage(
                type="heartbeat",
                station_id=self._station_uuid,
                current_load=self._active_jobs,
                ts=datetime.now(timezone.utc).isoformat(),
                ram_mb=ram_mb,
                cpu_percent=cpu_percent,
            )
            await self._send(ws, heartbeat.model_dump_json())
            logger.debug("heartbeat gửi đi (load=%d)", self._active_jobs)
            await asyncio.sleep(interval)

    async def _recv_loop(self, ws: ClientConnection) -> None:
        async for raw in ws:
            try:
                message = parse_server_message(raw)
            except ValidationError as exc:
                logger.warning("message server không hợp lệ (bỏ qua): %s", exc)
                continue
            if isinstance(message, RegisteredMessage):
                logger.info("orchestrator xác nhận đăng ký (registered)")
            elif isinstance(message, ServerCommand):
                # Chạy nền để không chặn recv loop (nhận thêm lệnh trong khi job chạy).
                asyncio.create_task(self._handle_command(ws, message))  # noqa: RUF006

    # ── Idempotency (INV-14) ────────────────────────────────────────────────────
    async def _handle_command(self, ws: ClientConnection, command: ServerCommand) -> None:
        command_id = str(command.command_id)
        # Check + add ĐỒNG BỘ (không await xen giữa) → hai lệnh trùng command_id chỉ 1 tác dụng.
        if command_id in self._processed_command_ids:
            logger.info("bỏ qua command trùng %s (idempotent, INV-14) — trả kết quả cũ", command_id)
            cached = self._command_results.get(command_id)
            if cached is not None:
                await self._send(ws, cached.model_dump_json())
            return
        self._processed_command_ids.add(command_id)

        cmd = command.command
        if isinstance(cmd, RunCommand):
            await self._run_job(ws, command.command_id, cmd)
        elif isinstance(cmd, BrowserOpenCommand):
            await self._handle_browser_open(ws, command.command_id, cmd)
        elif isinstance(cmd, BrowserCloseCommand):
            await self._handle_browser_close(ws, command.command_id, cmd)
        elif isinstance(cmd, (ProfileCreateCommand, ProfileUpdateCommand, ProfileDeleteCommand)):
            await self._handle_profile_crud(ws, command.command_id, cmd)
        elif isinstance(cmd, LoginRunCommand):
            await self._handle_login_run(ws, command.command_id, cmd)

    async def _reply(self, ws: ClientConnection, message: BaseModel, command_id: str) -> None:
        self._command_results[command_id] = message
        await self._send(ws, message.model_dump_json())

    def _ack(self, command_id: UUID, *, ok: bool, detail: str, profile_id: str | None) -> CommandAckMessage:
        return CommandAckMessage(
            type="command_ack",
            command_id=command_id,
            station_id=self._station_uuid,
            ok=ok,
            detail=detail,
            profile_id=profile_id,
        )

    # ── script.run (detector) ────────────────────────────────────────────────────
    async def _run_job(self, ws: ClientConnection, command_id: UUID, cmd: RunCommand) -> None:
        self._active_jobs += 1
        loop = asyncio.get_running_loop()

        def on_progress(step: str, detail: str | None = None) -> None:
            # Gọi TỪ thread pool → lên lịch gửi progress trên event loop (không chặn thread, send-lock lo race).
            asyncio.run_coroutine_threadsafe(self._emit_progress(ws, cmd, step, detail), loop)

        try:
            outcome = await self._runner.run(
                {
                    "platform": cmd.platform.value,
                    "target_url": cmd.target_url,
                    "cookie": cmd.cookie,  # KHÔNG log (INV-12)
                    "fixture_base_url": self._config.fixture_base_url,
                    "gemlogin_profile_id": cmd.gemlogin_profile_id,  # real mode: mở đúng browser
                    "render_settle_seconds": self._config.browser_render_settle_seconds,
                },
                on_progress=on_progress,
            )
            result = JobResultMessage(
                type="job_result",
                command_id=command_id,
                trace_id=cmd.trace_id,
                job_id=cmd.job_id,
                # INV-3: hai chiều thông tin RIÊNG BIỆT.
                url_status=UrlStatus(outcome["url_status"]),
                profile_health=ProfileHealth(outcome["profile_health"]),
                block_reason=outcome["block_reason"],
                response_time_ms=outcome["response_time_ms"],
            )
            await self._reply(ws, result, str(command_id))
            await self._emit_progress(ws, cmd, "DONE", outcome["url_status"])
            # Refresh cookie sau phiên OK (spec §4.4): gửi cookie mới lên orchestrator để MÃ HOÁ & lưu.
            fresh_cookie = outcome.get("fresh_cookie")
            if fresh_cookie:
                await self._emit_cookie_refresh(ws, cmd, fresh_cookie)
            logger.info(
                "job xong trace_id=%s job=%s url_status=%s profile_health=%s (%dms)",
                cmd.trace_id,
                cmd.job_id,
                outcome["url_status"],
                outcome["profile_health"],
                outcome["response_time_ms"],
            )
        finally:
            self._active_jobs -= 1

    async def _emit_progress(
        self, ws: ClientConnection, cmd: RunCommand, step: str, detail: str | None
    ) -> None:
        # Stream tiến trình job (§8). KHÔNG chứa cookie/credential (INV-12) — chỉ nhãn bước + chi tiết an toàn.
        msg = JobProgressMessage(
            type="job_progress",
            station_id=self._station_uuid,
            trace_id=cmd.trace_id,
            job_id=cmd.job_id,
            step=JobProgressStep(step),
            detail=detail,
            ts=datetime.now(timezone.utc).isoformat(),
        )
        await self._send(ws, msg.model_dump_json())

    async def _emit_cookie_refresh(
        self, ws: ClientConnection, cmd: RunCommand, cookie: str
    ) -> None:
        # Worker KHÔNG tự mã hoá (ADR-0006) — gửi cookie mới qua WSS, orchestrator mã hoá & lưu. KHÔNG log.
        msg = CookieRefreshMessage(
            type="cookie_refresh",
            station_id=self._station_uuid,
            profile_id=cmd.profile_id,
            gemlogin_profile_id=cmd.gemlogin_profile_id,
            cookie=cookie,
        )
        await self._send(ws, msg.model_dump_json())

    # ── browser.open / browser.close ─────────────────────────────────────────────
    async def _handle_browser_open(
        self, ws: ClientConnection, command_id: UUID, cmd: BrowserOpenCommand
    ) -> None:
        gid = cmd.gemlogin_profile_id or str(cmd.profile_id)
        try:
            # Adapter là blocking I/O (spawn browser / gọi API GemLogin) → chạy ngoài event loop.
            handle = await asyncio.to_thread(self._adapter.open_browser, gid, cmd.cookie or "")
            # Giám sát RAM/PID cây tiến trình browser (INV-9): vượt ngưỡng → kill + giải phóng.
            self._monitor.track(gid, handle.pid)
            # Forward CDP an toàn (mặc định local, không phơi CDP trần — INV-12).
            decision = self._cdp_policy.decide(handle.cdp_address)
            ack = self._ack(
                command_id,
                ok=True,
                detail=f"browser open (pid={handle.pid}); cdp={decision.reason}",
                profile_id=gid,
            )
        except GemLoginError as exc:
            # Hiện message GemLogin (vd "Profile id not exist" = sai id) để operator chẩn đoán nhanh.
            logger.warning("browser.open: GemLogin lỗi (%s) cho profile %s", exc, gid)
            ack = self._ack(command_id, ok=False, detail=f"gemlogin_error:{exc}", profile_id=gid)
        except Exception as exc:  # noqa: BLE001 — không nuốt lỗi: log phân loại + trả ok=false
            logger.warning("browser.open lỗi (%s) cho profile %s", type(exc).__name__, gid)
            ack = self._ack(command_id, ok=False, detail=f"open_error:{type(exc).__name__}", profile_id=gid)
        await self._reply(ws, ack, str(command_id))

    async def _handle_browser_close(
        self, ws: ClientConnection, command_id: UUID, cmd: BrowserCloseCommand
    ) -> None:
        gid = cmd.gemlogin_profile_id or str(cmd.profile_id)
        try:
            await asyncio.to_thread(self._adapter.close_browser, gid)
            self._monitor.untrack(gid)
            ack = self._ack(command_id, ok=True, detail="browser closed", profile_id=gid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("browser.close lỗi (%s) cho profile %s", type(exc).__name__, gid)
            ack = self._ack(command_id, ok=False, detail=f"close_error:{type(exc).__name__}", profile_id=gid)
        await self._reply(ws, ack, str(command_id))

    # ── profile.create / update / delete ─────────────────────────────────────────
    async def _handle_profile_crud(
        self,
        ws: ClientConnection,
        command_id: UUID,
        cmd: ProfileCreateCommand | ProfileUpdateCommand | ProfileDeleteCommand,
    ) -> None:
        try:
            profile_id: str | None
            if isinstance(cmd, ProfileCreateCommand):
                profile_id = await asyncio.to_thread(
                    self._adapter.create_profile,
                    ProfileSpec(platform=cmd.platform.value, name=cmd.account_label, proxy=cmd.proxy),
                )
                detail = "profile created"
            elif isinstance(cmd, ProfileUpdateCommand):
                profile_id = cmd.gemlogin_profile_id
                changes = {"account_label": cmd.account_label, "proxy": cmd.proxy}
                await asyncio.to_thread(self._adapter.update_profile, profile_id, changes)
                detail = "profile updated"
            else:  # ProfileDeleteCommand
                profile_id = cmd.gemlogin_profile_id
                await asyncio.to_thread(self._adapter.delete_profile, profile_id)
                self._monitor.untrack(profile_id)
                detail = "profile deleted"
            ack = self._ack(command_id, ok=True, detail=detail, profile_id=profile_id)
            # CRUD làm danh sách profile đổi → đồng bộ lại lên server (§4 "đồng bộ lại").
            await self._sync_profiles(ws)
        except GemLoginError as exc:
            # HIỆN message GemLogin để operator biết NGAY lý do — KHÔNG nuốt (INV-1). Vd bản FREE trả
            # "The free version does not work this feature" khi xoá → hiểu là phải xoá tay trong GemLogin.
            # An toàn: chỉ message API GemLogin, không credential (INV-12).
            logger.warning("profile CRUD: GemLogin lỗi (%s)", exc)
            ack = self._ack(command_id, ok=False, detail=f"gemlogin_error:{exc}", profile_id=None)
        except Exception as exc:  # noqa: BLE001 — lỗi khác: log phân loại + trả ok=false (không nuốt)
            logger.warning("profile CRUD lỗi (%s)", type(exc).__name__)
            ack = self._ack(command_id, ok=False, detail=f"crud_error:{type(exc).__name__}", profile_id=None)
        await self._reply(ws, ack, str(command_id))

    # ── login.run (Server GỌI chạy kịch bản đăng nhập — §7) ──────────────────────
    async def _handle_login_run(
        self, ws: ClientConnection, command_id: UUID, cmd: LoginRunCommand
    ) -> None:
        gid = cmd.gemlogin_profile_id or str(cmd.profile_id)
        try:
            # Blocking I/O (mở browser + chạy kịch bản) → ngoài event loop. KHÔNG log credential (INV-12).
            result = await asyncio.to_thread(
                execute_login,
                adapter=self._adapter,
                gemlogin_mode=self._config.gemlogin_mode,
                platform=cmd.platform,
                method=cmd.method.value if hasattr(cmd.method, "value") else str(cmd.method),
                gemlogin_profile_id=gid,
                cookie=cmd.cookie,
                username=cmd.username,
                password=cmd.password,
                otp_secret=cmd.otp_secret,
                confirm_username=cmd.confirm_username,
            )
            # ok=true CHỈ khi đăng nhập thật sự thành công; các outcome khác (cookie chết/captcha/OTP) là kết
            # quả CÓ NGHĨA của lệnh (đã chạy) nhưng chưa đăng nhập → ok=false + lý do (không nuốt, INV-1).
            detail = result.outcome.value + (f":{result.detail}" if result.detail else "")
            ack = self._ack(command_id, ok=result.logged_in, detail=detail, profile_id=gid)
            # Đăng nhập OK → cookie mới về orchestrator để mã hoá & refresh (spec §4.4). KHÔNG log giá trị.
            if result.logged_in and result.fresh_cookie:
                await self._send(
                    ws,
                    CookieRefreshMessage(
                        type="cookie_refresh",
                        station_id=self._station_uuid,
                        profile_id=cmd.profile_id,
                        gemlogin_profile_id=cmd.gemlogin_profile_id,
                        cookie=result.fresh_cookie,
                    ).model_dump_json(),
                )
            logger.info("login.run profile=%s outcome=%s", gid, result.outcome.value)
        except LoginError as exc:
            # (platform, method) không hỗ trợ (vd FB/YT + info) — báo ra rõ ràng, KHÔNG đoán.
            ack = self._ack(command_id, ok=False, detail=f"login_unsupported:{exc}", profile_id=gid)
        except GemLoginError as exc:
            # Lỗi phía GemLogin (id profile không tồn tại / kẹt "being opened"...) — HIỆN message để operator
            # thấy ngay (vd "Profile id not exist" = nạp sai id). An toàn: message API GemLogin, không credential.
            logger.warning("login.run: GemLogin lỗi (%s) cho profile %s", exc, gid)
            ack = self._ack(command_id, ok=False, detail=f"gemlogin_error:{exc}", profile_id=gid)
        except Exception as exc:  # noqa: BLE001 — không nuốt: log phân loại + trả ok=false
            logger.warning("login.run lỗi (%s) cho profile %s", type(exc).__name__, gid)
            ack = self._ack(command_id, ok=False, detail=f"login_error:{type(exc).__name__}", profile_id=gid)
        await self._reply(ws, ack, str(command_id))

    # ── Đồng bộ danh sách profile (§3) ───────────────────────────────────────────
    async def _profile_sync_loop(self, ws: ClientConnection) -> None:
        interval = self._config.profile_sync_interval_seconds
        while True:
            await asyncio.sleep(interval)
            await self._sync_profiles(ws)

    async def _sync_profiles(self, ws: ClientConnection) -> None:
        try:
            summaries = await asyncio.to_thread(self._adapter.list_profiles)
        except Exception as exc:  # noqa: BLE001 — không nuốt: log rồi bỏ qua vòng này
            logger.warning("liệt kê profile GemLogin lỗi (%s) — bỏ qua vòng sync", type(exc).__name__)
            return
        # MIRROR TOÀN BỘ (§3): gửi MỌI profile GemLogin, kể cả profile chưa gán nền tảng (platform=None) — để
        # "Xem profile" trên server khớp đúng GemLogin. Nhãn `fastcheck-platform=` ở note → platform cụ thể;
        # không nhãn → None (server giữ NULL, không dispatch được tới khi "Nạp tài khoản" gán nền tảng).
        profiles: list[StationProfile] = []
        for summary in summaries:
            platform: Platform | None = None
            if summary.platform:
                try:
                    platform = Platform(summary.platform)
                except ValueError:
                    logger.debug(
                        "profile %s nhãn platform lạ (%s) — coi như chưa gán",
                        summary.gemlogin_profile_id,
                        summary.platform,
                    )
                    platform = None
            profiles.append(
                StationProfile(
                    gemlogin_profile_id=summary.gemlogin_profile_id,
                    platform=platform,
                    name=summary.name,
                    gem_status=summary.gem_status,
                )
            )
        # TẤT CẢ id GemLogin hiện có → server đồng bộ XOÁ profile đã biến mất khỏi GemLogin.
        all_ids = [s.gemlogin_profile_id for s in summaries if s.gemlogin_profile_id]
        msg = ProfileSyncMessage(
            type="profile_sync",
            station_id=self._station_uuid,
            profiles=profiles,
            all_gemlogin_ids=all_ids,
        )
        await self._send(ws, msg.model_dump_json())
        assigned = sum(1 for p in profiles if p.platform is not None)
        logger.debug(
            "mirror %d profile GemLogin lên server (%d đã gán nền tảng)", len(profiles), assigned
        )

    # ── Giám sát tài nguyên (INV-9) ──────────────────────────────────────────────
    async def _resource_monitor_loop(self, ws: ClientConnection) -> None:
        interval = self._config.resource_monitor_interval_seconds
        while True:
            await asyncio.sleep(interval)
            # Vượt ngưỡng RAM → kill cây (trong monitor) + cảnh báo (alert=RAM bò lên = rò tiến trình).
            self._monitor.sweep(self._on_browser_killed)

    def _on_browser_killed(self, key: str) -> None:
        # Giải phóng slot cục bộ; profile được orchestrator trả về pool khi lease hết / khi job re-queue.
        logger.warning("ALERT: kill browser %s do vượt RAM — giải phóng tài nguyên máy trạm", key)

    def _close_all_browsers(self) -> None:
        for key in list(self._monitor.tracked()):
            try:
                self._adapter.close_browser(key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("đóng browser %s khi shutdown lỗi (%s)", key, type(exc).__name__)
            self._monitor.untrack(key)
