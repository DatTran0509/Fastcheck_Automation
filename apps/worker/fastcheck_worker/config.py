"""Cấu hình worker từ env, fail-fast khi thiếu biến bắt buộc."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel


def _load_dotenv_upwards() -> None:
    """Nạp .env tìm ngược từ cwd lên gốc repo (giống packages/config phía TS)."""
    start = Path.cwd()
    for directory in (start, *start.parents):
        candidate = directory / ".env"
        if candidate.exists():
            load_dotenv(candidate)
            return


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Thiếu biến môi trường bắt buộc: {name} (fail-fast).")
    return value


class WorkerConfig(BaseModel):
    station_id: str
    station_name: str
    max_concurrency: int
    agent_version: str
    orchestrator_ws_url: str
    ws_auth_token: str
    heartbeat_interval_ms: int
    gemlogin_mode: str


def load_config() -> WorkerConfig:
    _load_dotenv_upwards()
    return WorkerConfig(
        station_id=_require("STATION_ID"),
        station_name=os.environ.get("STATION_NAME", "dev-station"),
        max_concurrency=int(os.environ.get("WORKER_MAX_CONCURRENCY", "4")),
        agent_version=os.environ.get("AGENT_VERSION", "0.0.1"),
        orchestrator_ws_url=os.environ.get("ORCHESTRATOR_WS_URL", "ws://localhost:3002"),
        ws_auth_token=_require("WS_AUTH_TOKEN"),
        heartbeat_interval_ms=int(os.environ.get("HEARTBEAT_INTERVAL_MS", "10000")),
        gemlogin_mode=os.environ.get("GEMLOGIN_MODE", "fake"),
    )
