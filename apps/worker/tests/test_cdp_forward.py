"""Phase 4 — Forward CDP an toàn (INV-12): mặc định KHÔNG forward (chạy login local); bật forward BẮT
BUỘC có token, thiếu token → fail-fast (không bao giờ phơi CDP trần)."""

from __future__ import annotations

import pytest

from fastcheck_worker.browser.cdp_forward import CdpForwardConfigError, CdpForwardPolicy


def test_default_is_local_no_forward() -> None:
    policy = CdpForwardPolicy(enabled=False, token=None)
    decision = policy.decide("127.0.0.1:9222")
    assert decision.forwarded is False
    assert decision.secure_endpoint is None  # KHÔNG phơi CDP address trần


def test_enabled_without_token_fails_fast() -> None:
    # Bật forward mà không có token = cấu hình nguy hiểm → từ chối chạy (INV-12).
    with pytest.raises(CdpForwardConfigError):
        CdpForwardPolicy(enabled=True, token=None)


def test_enabled_with_token_wraps_wss() -> None:
    policy = CdpForwardPolicy(enabled=True, token="secret-token")
    decision = policy.decide("127.0.0.1:9222")
    assert decision.forwarded is True
    assert decision.secure_endpoint is not None
    assert decision.secure_endpoint.startswith("wss://")  # bọc WSS + token, không CDP trần
