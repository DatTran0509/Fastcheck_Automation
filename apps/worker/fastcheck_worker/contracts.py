"""Contract WS/queue cho worker — model pydantic SINH TỪ JSON Schema của packages/contracts.

Nguồn sự thật là zod (packages/contracts) → JSON Schema (packages/contracts/schema) → model pydantic
(_contracts_gen.py, sinh bằng `pnpm worker:gen`). ĐỪNG sửa tay _contracts_gen.py. (ADR-0006, review P1)
"""

from __future__ import annotations

from typing import Annotated, Union

from pydantic import Field, TypeAdapter

from ._contracts_gen import (
    BrowserCloseCommand,
    BrowserOpenCommand,
    CdpForwardCommand,
    CheckJobMessage,
    CommandAckMessage,
    CookieRefreshMessage,
    HeartbeatMessage,
    JobProgressMessage,
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
    Step,
    UrlStatus,
)

# Alias tên rõ nghĩa cho enum bước tiến trình (datamodel-codegen đặt tên `Step`).
JobProgressStep = Step

__all__ = [
    "BrowserCloseCommand",
    "BrowserOpenCommand",
    "CdpForwardCommand",
    "CheckJobMessage",
    "CommandAckMessage",
    "CookieRefreshMessage",
    "HeartbeatMessage",
    "JobProgressMessage",
    "JobProgressStep",
    "JobResultMessage",
    "LoginRunCommand",
    "Platform",
    "ProfileCreateCommand",
    "ProfileDeleteCommand",
    "ProfileHealth",
    "ProfileSyncMessage",
    "ProfileUpdateCommand",
    "RegisterMessage",
    "RegisteredMessage",
    "RunCommand",
    "ServerCommand",
    "StationInfo",
    "StationProfile",
    "Step",
    "UrlStatus",
    "parse_server_message",
]

# Union lệnh Server→Client, phân biệt bằng field `type`.
ServerMessage = Annotated[Union[RegisteredMessage, ServerCommand], Field(discriminator="type")]
_server_adapter: TypeAdapter[RegisteredMessage | ServerCommand] = TypeAdapter(ServerMessage)


def parse_server_message(raw: str | bytes) -> RegisteredMessage | ServerCommand:
    """Validate message Server→Client bằng model sinh từ JSON Schema; ném ValidationError nếu sai shape."""
    return _server_adapter.validate_json(raw)
