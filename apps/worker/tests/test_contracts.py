from __future__ import annotations

from fastcheck_worker.contracts import RegisterMessage, StationInfo


def test_register_message_has_type_and_station() -> None:
    message = RegisterMessage(
        station=StationInfo(
            station_id="00000000-0000-4000-8000-000000000001",
            name="dev-station",
            agent_version="0.0.1",
            max_concurrency=4,
        ),
    )
    data = message.model_dump()
    assert data["type"] == "register"
    assert data["station"]["max_concurrency"] == 4


def test_register_message_json_roundtrip() -> None:
    message = RegisterMessage(
        station=StationInfo(
            station_id="00000000-0000-4000-8000-000000000001",
            name="dev-station",
            agent_version="0.0.1",
            max_concurrency=4,
        ),
    )
    restored = RegisterMessage.model_validate_json(message.model_dump_json())
    assert restored == message
