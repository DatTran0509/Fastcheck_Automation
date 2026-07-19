"""Đối chiếu output pydantic của worker với JSON Schema xuất từ zod (nguồn sự thật — ADR-0006, P1).

Bắt drift biên TS↔Python: nếu pydantic phát field lệch với contract zod, test này fail.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from fastcheck_worker.contracts import HeartbeatMessage, RegisterMessage, StationInfo
from uuid import UUID

def _schema_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "packages" / "contracts" / "schema"
        if candidate.exists():
            return candidate
    raise RuntimeError("Không tìm thấy packages/contracts/schema — chạy `pnpm --filter @fastcheck/contracts gen:schema`")


def _load(name: str) -> dict[str, Any]:
    return json.loads((_schema_dir() / name).read_text(encoding="utf-8"))


def test_register_conforms_to_zod_schema() -> None:
    schema = _load("ws-client-message.schema.json")
    message = RegisterMessage(
        type="register",
        station=StationInfo(
            station_id=UUID("00000000-0000-4000-8000-000000000001"),
            name="dev-station",
            agent_version="0.0.1",
            max_concurrency=4,
        ),
    )
    jsonschema.validate(instance=message.model_dump(mode="json"), schema=schema)


def test_heartbeat_conforms_to_zod_schema() -> None:
    schema = _load("ws-client-message.schema.json")
    message = HeartbeatMessage(
        type="heartbeat",
        station_id=UUID("00000000-0000-4000-8000-000000000001"),
        current_load=0,
        ts="2026-07-17T00:00:00+00:00",
    )
    jsonschema.validate(instance=message.model_dump(mode="json"), schema=schema)
