"""Forward CDP an toàn (INV-12, §5, §6.8e) — hoà giải yêu cầu Excel với bảo mật.

File Excel *yêu cầu* Client forward CDP/websocket điều khiển browser → forwarding là BẮT BUỘC CÓ. Điều
cấm là để **CDP thô, không xác thực, ra internet công cộng** (ai bắt được cũng điều khiển browser của bạn).

Chính sách ở đây (mặc định an toàn):
- **Mặc định KHÔNG forward**: chạy kịch bản login/detector LOCAL rồi trả kết quả. Đây là đường mặc định.
- Chỉ khi `CDP_FORWARD_ENABLED=true` mới forward, và **bắt buộc** có token (`CDP_FORWARD_TOKEN`), bọc qua
  **WSS + token**, ưu tiên giữ trong mạng nội bộ/tunnel. Thiếu token khi bật → lỗi cấu hình (fail-fast),
  KHÔNG bao giờ phơi CDP trần.

Module này quyết định CHÍNH SÁCH (có forward hay không, có đủ điều kiện an toàn không). Việc dựng tunnel
WSS thật thuộc hạ tầng triển khai; ở đây bảo đảm bất biến "không phơi CDP trần" luôn giữ và test được.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("fastcheck.worker.cdp")


class CdpForwardConfigError(Exception):
    """Bật forward nhưng thiếu điều kiện an toàn (vd không có token) — fail-fast, không phơi CDP trần."""


@dataclass(frozen=True)
class CdpForwardDecision:
    forwarded: bool
    reason: str
    # KHÔNG bao giờ chứa CDP address trần khi forwarded=False. Khi forwarded=True là endpoint WSS có token.
    secure_endpoint: str | None = None


class CdpForwardPolicy:
    """Áp chính sách forward CDP an toàn. Mặc định: chạy login local (không forward)."""

    def __init__(self, enabled: bool, token: str | None) -> None:
        # Fail-fast: bật forward mà không có token = cấu hình nguy hiểm (INV-12). Không cho phép chạy.
        if enabled and not token:
            raise CdpForwardConfigError(
                "CDP_FORWARD_ENABLED=true nhưng thiếu CDP_FORWARD_TOKEN — từ chối phơi CDP trần (INV-12)."
            )
        self._enabled = enabled
        self._token = token

    def decide(self, cdp_address: str) -> CdpForwardDecision:
        """Quyết định có forward CDP hay không cho một phiên browser."""
        if not self._enabled:
            # Đường mặc định (an toàn): không forward, chạy script login local rồi trả kết quả.
            return CdpForwardDecision(forwarded=False, reason="local-login (mặc định, không forward CDP)")
        # Bật + có token: forward qua WSS + token (không log CDP address trần — INV-12).
        logger.info("forward CDP qua WSS + token (mạng nội bộ/tunnel) cho một phiên browser")
        return CdpForwardDecision(
            forwarded=True,
            reason="wss+token",
            secure_endpoint=self._wrap_secure(cdp_address),
        )

    def _wrap_secure(self, cdp_address: str) -> str:
        # Bọc CDP trong kênh WSS có token. Endpoint trả về đã xác thực; KHÔNG phải CDP trần.
        # (Dựng tunnel WSS thật thuộc hạ tầng; ở đây trả endpoint bọc token để tầng trên dùng.)
        return f"wss://cdp-forward/{self._token}"
