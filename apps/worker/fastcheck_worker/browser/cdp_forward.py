"""Forward CDP an toàn (INV-12, §5, §6.8e) — hoà giải yêu cầu Excel với bảo mật.

File Excel *yêu cầu* Client forward CDP/websocket điều khiển browser → forwarding là BẮT BUỘC CÓ. Điều
cấm là để **CDP thô, không xác thực, ra internet công cộng** (ai bắt được cũng điều khiển browser của bạn).

Kiến trúc (reverse tunnel, đúng INV-12):
    controller  ──WSS(/cdp?role=controller)──►  ORCHESTRATOR relay  ◄──WSS(/cdp?role=worker)──  WORKER
                                                                                                   │
                                                                                     ws local (DevTools) │
                                                                                                   ▼
                                                                                        GemLogin CDP (browser)

- `CdpForwardPolicy`: chính sách bật/tắt + FAIL-FAST (bật mà thiếu token → từ chối, không phơi CDP trần).
- `CdpTunnel`: transport THẬT — worker mở ws tới CDP local của GemLogin + ws tới relay orchestrator (WSS +
  token ở header), rồi BƠM GÓI hai chiều. Relay ghép cầu worker↔controller theo `session_id`. Kênh luôn có
  lớp xác thực + mã hoá (WSS) — không bao giờ là CDP trần ra ngoài.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger("fastcheck.worker.cdp")


class CdpForwardConfigError(Exception):
    """Bật forward nhưng thiếu điều kiện an toàn (vd không có token) — fail-fast, không phơi CDP trần."""


@dataclass(frozen=True)
class CdpForwardDecision:
    forwarded: bool
    reason: str
    # KHÔNG chứa CDP address trần. Endpoint attach THẬT cho controller nằm ở relay orchestrator (per-session),
    # không sinh ở đây; field giữ lại để tương thích chỗ gọi cũ (luôn None ở thiết kế tunnel mới).
    secure_endpoint: str | None = None


class CdpForwardPolicy:
    """Áp chính sách forward CDP an toàn. Mặc định: KHÔNG forward (chạy login/detector local)."""

    def __init__(self, enabled: bool, token: str | None) -> None:
        # Fail-fast: bật forward mà không có token = cấu hình nguy hiểm (INV-12). Không cho phép chạy.
        if enabled and not token:
            raise CdpForwardConfigError(
                "CDP_FORWARD_ENABLED=true nhưng thiếu CDP_FORWARD_TOKEN — từ chối phơi CDP trần (INV-12)."
            )
        self._enabled = enabled
        self._token = token

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def token(self) -> str | None:
        return self._token

    def decide(self, cdp_address: str) -> CdpForwardDecision:  # noqa: ARG002 — địa chỉ chỉ để log ngữ cảnh
        """Ngữ cảnh forward cho một phiên browser (dùng ở browser.open để log). Forward THẬT do lệnh
        `cdp.forward` + CdpTunnel thực hiện; ở đây chỉ cho biết forward có KHẢ DỤNG hay không."""
        if not self._enabled:
            return CdpForwardDecision(forwarded=False, reason="local (forward tắt — mặc định an toàn)")
        return CdpForwardDecision(forwarded=True, reason="sẵn sàng forward WSS+token qua lệnh cdp.forward")


class _WsLike(Protocol):
    async def send(self, data: Any) -> None: ...
    def __aiter__(self) -> Any: ...


async def _pump(src: _WsLike, dst: _WsLike, label: str) -> None:
    """Bơm mọi khung từ `src` sang `dst` tới khi một đầu đóng. Không diễn giải nội dung CDP (chỉ chuyển tiếp)."""
    async for frame in src:
        await dst.send(frame)
    logger.debug("cdp pump %s: nguồn đóng", label)


def _resolve_ws_debugger_url(cdp_address: str, timeout: float) -> str:
    """Lấy webSocketDebuggerUrl của browser từ CDP HTTP endpoint (`http://host:port/json/version`)."""
    url = f"http://{cdp_address}/json/version"
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — CDP local của GemLogin
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    ws_url = str(data.get("webSocketDebuggerUrl", "")) if isinstance(data, dict) else ""
    if not ws_url:
        raise CdpForwardConfigError(f"không lấy được webSocketDebuggerUrl từ {url}")
    return ws_url


class CdpTunnel:
    """Bắc cầu (bridge) CDP local GemLogin ↔ relay orchestrator, qua WSS + token (INV-12). Một tunnel/session."""

    def __init__(
        self,
        *,
        orchestrator_ws_url: str,
        token: str,
        station_id: str,
        session_id: str,
        cdp_address: str,
        connect_timeout: float = 15.0,
    ) -> None:
        self._orch_url = orchestrator_ws_url.rstrip("/")
        self._token = token
        self._station_id = station_id
        self._session_id = session_id
        self._cdp_address = cdp_address
        self._connect_timeout = connect_timeout
        self._task: asyncio.Task[None] | None = None

    @property
    def session_id(self) -> str:
        return self._session_id

    def start(self) -> None:
        """Khởi động tunnel nền (không chặn recv loop). Idempotent: đã chạy thì bỏ qua."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — dừng là dừng, không nuốt ý nghĩa
                pass
        self._task = None

    async def _run(self) -> None:
        # relay endpoint: ws(s)://orchestrator/cdp?role=worker&session=..&station=.. ; token ở header (INV-12).
        relay_url = (
            f"{self._orch_url}/cdp?role=worker&session={self._session_id}&station={self._station_id}"
        )
        headers = [("Authorization", f"Bearer {self._token}")]
        try:
            ws_debug_url = await asyncio.to_thread(
                _resolve_ws_debugger_url, self._cdp_address, self._connect_timeout
            )
        except Exception as exc:  # noqa: BLE001 — không nuốt: log + kết thúc tunnel (báo ra, INV-1)
            logger.warning("cdp tunnel %s: không resolve được CDP local (%s)", self._session_id, exc)
            return
        try:
            async with (
                connect(ws_debug_url, max_size=None) as local,
                connect(relay_url, additional_headers=headers, max_size=None) as relay,
            ):
                logger.info("cdp tunnel %s: bắc cầu GemLogin↔relay (WSS+token)", self._session_id)
                up = asyncio.create_task(_pump(local, relay, f"{self._session_id}:local→relay"))
                down = asyncio.create_task(_pump(relay, local, f"{self._session_id}:relay→local"))
                done, pending = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
        except ConnectionClosed as exc:
            logger.info("cdp tunnel %s đóng (%s)", self._session_id, exc)
        except Exception as exc:  # noqa: BLE001 — không nuốt: phân loại + log, tunnel kết thúc
            logger.warning("cdp tunnel %s lỗi (%s)", self._session_id, type(exc).__name__)
        finally:
            logger.debug("cdp tunnel %s: kết thúc", self._session_id)
