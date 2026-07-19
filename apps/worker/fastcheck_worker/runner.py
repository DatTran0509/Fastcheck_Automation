"""Chạy một check trong bounded thread pool (INV-10 + ADR-0007).

ADR-0007: browser thật do GemLogin chạy trong TIẾN TRÌNH RIÊNG (mỗi profile một process + vân tay +
proxy sticky) — worker chỉ gửi lệnh CDP (blocking I/O) và chờ. Nên concurrency worker là
`ThreadPoolExecutor` size = max_concurrency = prefetch RabbitMQ; mỗi thread điều khiển ĐÚNG một
browser của một profile (cách ly do GemLogin, không chia sẻ context — INV-6). Mọi lỗi/timeout →
INCONCLUSIVE (INV-1), KHÔNG BAO GIỜ DEAD. Phân loại lỗi qua `block_reason`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import TYPE_CHECKING, NotRequired, TypedDict

from .browser.adapter import GemLoginError
from .browser.page_source import DrissionPageSource, FakePageSource
from .contracts import Platform, ProfileHealth, UrlStatus
from .detectors import get_detector

if TYPE_CHECKING:
    from .browser.adapter import GemLoginAdapter

logger = logging.getLogger("fastcheck.worker.runner")

# Callback báo tiến trình (§8): (step, detail). Gọi TỪ THREAD pool → hiện thực phải tự threadsafe.
ProgressCallback = Callable[[str, str | None], None]


class CheckPayload(TypedDict):
    platform: str
    target_url: str
    cookie: str
    fixture_base_url: str | None
    # real mode: id profile GemLogin cần mở đúng browser (fake mode để None).
    gemlogin_profile_id: str | None
    # real mode: giây chờ SPA render trước khi chụp text (mặc định 3.0 nếu vắng). Optional cho tương thích.
    render_settle_seconds: NotRequired[float]


class CheckOutcome(TypedDict):
    url_status: str
    profile_health: str
    block_reason: str | None
    response_time_ms: int
    # Cookie mới thu được sau phiên OK (real mode, profile khoẻ) để refresh (spec §4.4). None nếu không có.
    fresh_cookie: str | None


def _notify(on_progress: ProgressCallback | None, step: str, detail: str | None = None) -> None:
    if on_progress is not None:
        on_progress(step, detail)


def run_check(
    payload: CheckPayload,
    adapter: GemLoginAdapter | None = None,
    on_progress: ProgressCallback | None = None,
) -> CheckOutcome:
    """Thực thi trong MỘT THREAD của pool. Không raise ra ngoài — mọi lỗi thành INCONCLUSIVE (INV-1).

    `adapter=None` → fake mode (FakePageSource + urllib). `adapter` có → real mode: mở browser GemLogin,
    DrissionPage attach CDP, nạp cookie trước điều hướng (INV-2), detect, rồi ĐÓNG browser (dọn tài nguyên).
    `on_progress` (nếu có) nhận các bước để stream lên dashboard (§8).
    """
    start = time.monotonic()
    gid = payload.get("gemlogin_profile_id")
    fresh_cookie: str | None = None
    try:
        platform = Platform(payload["platform"])
        detector = get_detector(platform)
        if adapter is None:
            # ── fake mode ──
            _notify(on_progress, "DETECT", "fake page source")
            source = FakePageSource(fixture_base_url=payload["fixture_base_url"])
            page = source.open_page(payload["target_url"], payload["cookie"])
            elapsed = int((time.monotonic() - start) * 1000)
            result = detector.detect(page, response_time_ms=elapsed)
        else:
            # ── real mode: 1 job = 1 profile = 1 browser (INV-6). Mở → detect → ĐÓNG (dọn — INV-9). ──
            if not gid:
                raise ValueError("real mode cần gemlogin_profile_id (orchestrator phải gửi)")
            _notify(on_progress, "OPEN_BROWSER", f"profile={gid}")
            handle = adapter.open_browser(gid, payload["cookie"])
            drission = DrissionPageSource(
                handle.cdp_address,
                render_settle_seconds=payload.get("render_settle_seconds", 3.0),
            )
            try:
                _notify(on_progress, "DETECT", payload["platform"])
                page = drission.open_page(payload["target_url"], payload["cookie"])
                elapsed = int((time.monotonic() - start) * 1000)
                result = detector.detect(page, response_time_ms=elapsed)
                # Chỉ refresh cookie khi profile KHOẺ (đã đăng nhập) — cookie phiên còn giá trị (spec §4.4).
                if result.profile_health == ProfileHealth.OK:
                    fresh_cookie = drission.cookies_string() or None
            finally:
                drission.close()
                adapter.close_browser(gid)  # GemLogin kill cây tiến trình browser (INV-9)
        return CheckOutcome(
            url_status=result.url_status.value,
            profile_health=result.profile_health.value,
            block_reason=result.block_reason,
            response_time_ms=result.response_time_ms or int((time.monotonic() - start) * 1000),
            fresh_cookie=fresh_cookie,
        )
    except GemLoginError as exc:
        # Browser MỞ KHÔNG ĐƯỢC / GemLogin kẹt ("being opened") = lỗi HẠ TẦNG phía profile → THROTTLED:
        # orchestrator cho profile NGHỈ NGẮN (cắt vòng hammer, để GemLogin hồi), KHÔNG DEAD (tài khoản vẫn tốt),
        # KHÔNG làm mở circuit breaker platform. Vẫn INCONCLUSIVE về target (INV-1). Đóng để dọn (INV-9).
        elapsed = int((time.monotonic() - start) * 1000)
        logger.warning("run_check: mở browser lỗi (%s) → THROTTLED (nghỉ profile, không hammer)", type(exc).__name__)
        if adapter is not None and gid:
            try:
                adapter.close_browser(gid)
            except Exception:  # noqa: BLE001, S110 — dọn best-effort, không che lỗi gốc
                pass
        return CheckOutcome(
            url_status=UrlStatus.INCONCLUSIVE.value,
            profile_health=ProfileHealth.THROTTLED.value,
            block_reason=f"browser_open_failed:{type(exc).__name__}",
            response_time_ms=elapsed,
            fresh_cookie=None,
        )
    except Exception as exc:  # noqa: BLE001 — biên tiến trình con: gói mọi lỗi hạ tầng thành kết quả
        # Lỗi hạ tầng KHÁC (tải trang lỗi, detect lỗi sau khi đã mở browser...) = INCONCLUSIVE, KHÔNG DEAD
        # (INV-1). Profile OK (chưa kết tội — có thể tạm thời) → re-queue bình thường.
        elapsed = int((time.monotonic() - start) * 1000)
        logger.warning("run_check lỗi hạ tầng (%s) → INCONCLUSIVE", type(exc).__name__)
        if adapter is not None and gid:
            # Đảm bảo đóng browser kể cả khi mở/detect lỗi (chống rò tiến trình — INV-9).
            try:
                adapter.close_browser(gid)
            except Exception:  # noqa: BLE001, S110 — dọn best-effort, không che lỗi gốc
                pass
        return CheckOutcome(
            url_status=UrlStatus.INCONCLUSIVE.value,
            profile_health=ProfileHealth.OK.value,  # lỗi hạ tầng: chưa kết tội profile (re-queue)
            block_reason=f"infra_error:{type(exc).__name__}",
            response_time_ms=elapsed,
            fresh_cookie=None,
        )


class CheckRunner:
    """Bounded thread pool (size = max_concurrency = prefetch, INV-10 + ADR-0007) + timeout cứng (INV-9).

    Số check chạy đồng thời KHÔNG vượt `max_concurrency` (pool giới hạn số thread) → số browser mở
    đồng thời trên station bị chặn trên, khớp backpressure với prefetch RabbitMQ.
    """

    def __init__(
        self,
        max_concurrency: int,
        job_timeout_seconds: float = 120.0,
        adapter: GemLoginAdapter | None = None,
    ) -> None:
        self._max_concurrency = max_concurrency
        self._pool = ThreadPoolExecutor(
            max_workers=max_concurrency, thread_name_prefix="fc-check"
        )
        self._timeout = job_timeout_seconds
        # adapter=None → fake mode (detector đọc qua FakePageSource). adapter có → real mode
        # (mở browser GemLogin thật + DrissionPage). Quyết định ở ws_client theo GEMLOGIN_MODE.
        self._adapter = adapter

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    async def run(
        self, payload: CheckPayload, on_progress: ProgressCallback | None = None
    ) -> CheckOutcome:
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            self._pool, partial(run_check, payload, self._adapter, on_progress)
        )
        try:
            return await asyncio.wait_for(future, timeout=self._timeout)
        except asyncio.TimeoutError:
            # INV-9/INV-1: quá hạn cứng → INCONCLUSIVE, KHÔNG DEAD. Ở đường thật, việc dừng là KILL
            # tiến trình browser GemLogin (taskkill /T /F) khiến lệnh CDP blocking bung lỗi → thread
            # thoát; fake mode dựa socket timeout của urllib nên thread không treo (ADR-0007).
            logger.warning("job quá hạn %.0fs → INCONCLUSIVE (timeout)", self._timeout)
            return CheckOutcome(
                url_status=UrlStatus.INCONCLUSIVE.value,
                profile_health=ProfileHealth.OK.value,
                block_reason="timeout",
                response_time_ms=int(self._timeout * 1000),
                fresh_cookie=None,
            )

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
