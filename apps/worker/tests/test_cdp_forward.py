"""Phase 4/§5 — Forward CDP an toàn (INV-12).

- Mặc định KHÔNG forward (chạy login/detector local).
- Bật forward BẮT BUỘC có token; thiếu token → fail-fast (không bao giờ phơi CDP trần).
- Transport THẬT: `_pump` bắc cầu (bridge) mọi khung giữa hai đầu ws (CDP local ↔ relay orchestrator) mà
  KHÔNG diễn giải nội dung — chuyển tiếp nguyên vẹn tới khi một đầu đóng.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from fastcheck_worker.browser.cdp_forward import CdpForwardConfigError, CdpForwardPolicy, _pump


def test_default_is_local_no_forward() -> None:
    policy = CdpForwardPolicy(enabled=False, token=None)
    assert policy.enabled is False
    decision = policy.decide("127.0.0.1:9222")
    assert decision.forwarded is False
    assert decision.secure_endpoint is None  # KHÔNG phơi CDP address trần


def test_enabled_without_token_fails_fast() -> None:
    # Bật forward mà không có token = cấu hình nguy hiểm → từ chối chạy (INV-12).
    with pytest.raises(CdpForwardConfigError):
        CdpForwardPolicy(enabled=True, token=None)


def test_enabled_with_token_ready_to_forward() -> None:
    policy = CdpForwardPolicy(enabled=True, token="secret-token")
    assert policy.enabled is True
    assert policy.token == "secret-token"
    decision = policy.decide("127.0.0.1:9222")
    assert decision.forwarded is True
    # Endpoint attach THẬT nằm ở relay orchestrator (per-session), KHÔNG sinh CDP trần ở đây.
    assert decision.secure_endpoint is None


class _FakeWs:
    """Đầu websocket giả (không mạng) để test _pump: phát các khung `incoming`, thu các khung `sent`."""

    def __init__(self, incoming: list[Any]) -> None:
        self._q = list(incoming)
        self.sent: list[Any] = []

    async def send(self, data: Any) -> None:
        self.sent.append(data)

    def __aiter__(self) -> "_FakeWs":
        return self

    async def __anext__(self) -> Any:
        if not self._q:
            raise StopAsyncIteration
        return self._q.pop(0)


def test_pump_forwards_all_frames_until_source_closes() -> None:
    # Bơm chuyển tiếp NGUYÊN VẸN mọi khung CDP từ nguồn sang đích, không đổi/không bỏ sót.
    src = _FakeWs(['{"id":1,"method":"Page.navigate"}', b"\x81binary", '{"id":2}'])
    dst = _FakeWs([])
    asyncio.run(_pump(src, dst, "test"))
    assert dst.sent == ['{"id":1,"method":"Page.navigate"}', b"\x81binary", '{"id":2}']
