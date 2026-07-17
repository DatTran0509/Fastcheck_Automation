"""WS client: kết nối orchestrator, đăng ký, heartbeat ~10s, auto-reconnect (exponential backoff).

Phase 0: chưa mở browser (ADR-0006) — chỉ register + heartbeat để hiện trong registry orchestrator.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from .config import WorkerConfig
from .contracts import HeartbeatMessage, RegisterMessage, StationInfo

logger = logging.getLogger("fastcheck.worker")


class WorkerClient:
    def __init__(self, config: WorkerConfig) -> None:
        self._config = config
        self._url = f"{config.orchestrator_ws_url}/ws"

    async def run_forever(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self._connect_and_serve()
                backoff = 1.0
            except (OSError, ConnectionClosed) as exc:
                logger.warning("mất kết nối orchestrator (%s) — thử lại sau %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _connect_and_serve(self) -> None:
        # INV-12: token đi ở header Authorization của handshake, không nằm trong message.
        headers = [("Authorization", f"Bearer {self._config.ws_auth_token}")]
        async with connect(self._url, additional_headers=headers) as ws:
            await self._register(ws)
            logger.info("đã gửi register cho station %s tới %s", self._config.station_id, self._url)
            await asyncio.gather(self._heartbeat_loop(ws), self._recv_loop(ws))

    async def _register(self, ws: ClientConnection) -> None:
        message = RegisterMessage(
            station=StationInfo(
                station_id=self._config.station_id,
                name=self._config.station_name,
                agent_version=self._config.agent_version,
                max_concurrency=self._config.max_concurrency,
            ),
        )
        await ws.send(message.model_dump_json())

    async def _heartbeat_loop(self, ws: ClientConnection) -> None:
        interval = self._config.heartbeat_interval_ms / 1000.0
        while True:
            heartbeat = HeartbeatMessage(
                station_id=self._config.station_id,
                current_load=0,  # Phase 0: chưa chạy job
                ts=datetime.now(timezone.utc).isoformat(),
            )
            await ws.send(heartbeat.model_dump_json())
            logger.debug("heartbeat gửi đi")
            await asyncio.sleep(interval)

    async def _recv_loop(self, ws: ClientConnection) -> None:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("message không phải JSON, bỏ qua")
                continue
            message_type = data.get("type")
            if message_type == "registered":
                logger.info("orchestrator xác nhận đăng ký (registered)")
            elif message_type == "command":
                # Phase 0: chưa mở browser. Lệnh idempotent theo command_id sẽ xử lý ở Phase sau (INV-14).
                logger.info("nhận command %s (Phase 0: chưa thực thi)", data.get("command_id"))
            else:
                logger.debug("message loại: %s", message_type)
