"""Phase 4 — Test 1: lệnh WS idempotent theo command_id (INV-14).

Gửi cùng một `command_id` "mở browser" HAI lần → adapter chỉ mở browser MỘT lần (station lưu command_id
đã xử lý, trùng thì bỏ qua và trả lại kết quả cũ). Dùng một WS server giả đóng vai orchestrator, không cần
hạ tầng thật — idempotency là trách nhiệm phía station.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

from websockets.asyncio.server import serve

from fastcheck_worker.browser.adapter import BrowserHandle
from fastcheck_worker.config import WorkerConfig
from fastcheck_worker.ws_client import WorkerClient

STATION_ID = "00000000-0000-4000-8000-0000000000f1"
PROFILE_UUID = "00000000-0000-4000-8000-0000000000f2"


class _SpyAdapter:
    """Đếm số lần open_browser thực sự được gọi (không spawn tiến trình — dùng pid tiến trình test)."""

    def __init__(self) -> None:
        self.open_calls = 0

    def open_browser(self, gemlogin_profile_id: str, cookie: str = "") -> BrowserHandle:
        self.open_calls += 1
        return BrowserHandle(profile_id=gemlogin_profile_id, cdp_address="fake", pid=os.getpid())

    def close_browser(self, gemlogin_profile_id: str) -> None:
        pass

    def list_profiles(self) -> list:  # type: ignore[type-arg]
        return []

    def create_profile(self, spec: object) -> str:
        return "x"

    def update_profile(self, gemlogin_profile_id: str, changes: dict) -> None:  # type: ignore[type-arg]
        pass

    def delete_profile(self, gemlogin_profile_id: str) -> None:
        pass


async def _run() -> tuple[int, int]:
    acks: list[dict] = []  # type: ignore[type-arg]
    command_id = str(uuid.uuid4())

    async def handler(ws: object) -> None:
        send = ws.send  # type: ignore[attr-defined]

        async def drain() -> None:
            async for raw in ws:  # type: ignore[attr-defined]
                data = json.loads(raw)
                if data.get("type") == "command_ack":
                    acks.append(data)

        drain_task = asyncio.create_task(drain())
        await send(json.dumps({"type": "registered", "station_id": STATION_ID}))
        cmd = {
            "type": "command",
            "command_id": command_id,
            "command": {
                "name": "browser.open",
                "profile_id": PROFILE_UUID,
                "gemlogin_profile_id": "g1",
                "cookie": None,
            },
        }
        await send(json.dumps(cmd))
        await send(json.dumps(cmd))  # TRÙNG command_id → chỉ 1 tác dụng (INV-14)
        await asyncio.sleep(1.2)
        drain_task.cancel()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        config = WorkerConfig(
            station_id=STATION_ID,
            ws_auth_token="test-token",
            orchestrator_ws_url=f"ws://127.0.0.1:{port}",
            gemlogin_mode="fake",
            heartbeat_interval_ms=10_000,
            profile_sync_interval_seconds=3600.0,
            resource_monitor_interval_seconds=3600.0,
        )
        client = WorkerClient(config)
        spy = _SpyAdapter()
        client._adapter = spy  # type: ignore[assignment]  # thay adapter bằng spy để đếm open

        task = asyncio.create_task(client.run_forever())
        await asyncio.sleep(1.6)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return spy.open_calls, len(acks)


def test_duplicate_browser_open_opens_once() -> None:
    open_calls, ack_count = asyncio.run(_run())
    assert open_calls == 1, f"mở browser {open_calls} lần — phải đúng 1 (idempotent command_id, INV-14)"
    # Nhận ít nhất 1 ack; lệnh trùng được trả lại kết quả cũ (không mở thêm browser).
    assert ack_count >= 1
