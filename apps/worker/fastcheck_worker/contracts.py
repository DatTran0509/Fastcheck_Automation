"""Mirror pydantic của giao thức WS. Nguồn sự thật là packages/contracts (zod) — ADR-0006.

Đổi contract: sửa zod trước, cập nhật các model ở đây theo.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class StationInfo(BaseModel):
    station_id: str
    name: str
    mac_address: str | None = None
    ip_address: str | None = None
    agent_version: str
    max_concurrency: int


class RegisterMessage(BaseModel):
    type: Literal["register"] = "register"
    # Token KHÔNG nằm ở message này — gửi qua header Authorization của handshake (INV-12).
    station: StationInfo


class HeartbeatMessage(BaseModel):
    type: Literal["heartbeat"] = "heartbeat"
    station_id: str
    current_load: int
    ts: str
