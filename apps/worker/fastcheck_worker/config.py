"""Cấu hình worker bằng pydantic-settings (mirror packages/config), fail-fast khi thiếu biến."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_dotenv_upwards() -> None:
    """Nạp .env tìm ngược từ cwd lên gốc repo (giống packages/config phía TS)."""
    start = Path.cwd()
    for directory in (start, *start.parents):
        candidate = directory / ".env"
        if candidate.exists():
            load_dotenv(candidate)
            return


class WorkerConfig(BaseSettings):
    """Đọc env (alias khớp .env.example). Thiếu STATION_ID/WS_AUTH_TOKEN → ValidationError (fail-fast)."""

    model_config = SettingsConfigDict(populate_by_name=True, case_sensitive=False, extra="ignore")

    station_id: str = Field(alias="STATION_ID")
    station_name: str = Field(default="dev-station", alias="STATION_NAME")
    max_concurrency: int = Field(default=4, alias="WORKER_MAX_CONCURRENCY")
    agent_version: str = Field(default="0.0.1", alias="AGENT_VERSION")
    orchestrator_ws_url: str = Field(default="ws://localhost:3002", alias="ORCHESTRATOR_WS_URL")
    ws_auth_token: str = Field(alias="WS_AUTH_TOKEN")
    heartbeat_interval_ms: int = Field(default=10000, alias="HEARTBEAT_INTERVAL_MS")
    gemlogin_mode: str = Field(default="fake", alias="GEMLOGIN_MODE")
    # Timeout cứng cho một job (INV-9, ≤2 phút). Quá hạn → INCONCLUSIVE + dọn.
    job_timeout_seconds: float = Field(default=120.0, alias="JOB_TIMEOUT_SECONDS")
    # CHỈ fake mode: map URL nền tảng → fixture server để chạy end-to-end không cần TikTok thật.
    fixture_base_url: str | None = Field(default=None, alias="FIXTURE_BASE_URL")

    # ── GemLogin (real mode) ───────────────────────────────────────────────────
    # Base URL API local GemLogin (CRUD profile + mở/tắt + lấy CDP address). Chỉ dùng khi mode='real'.
    gemlogin_api_url: str = Field(default="http://127.0.0.1:1010", alias="GEMLOGIN_API_URL")
    # Ngưỡng chờ MỞ browser (giây) trước khi bỏ cuộc → GemLoginError (INCONCLUSIVE + re-queue). Lần đầu tải
    # Chromium có thể chậm nên cần rộng; nhưng KHÔNG quá lớn để không "treo" khi GemLogin kẹt (fail-fast +
    # self-heal thay vì block. INV-9: timeout là một loại kết quả, không nuốt). Hạ mặc định 180→90.
    browser_start_wait_seconds: float = Field(default=90.0, alias="BROWSER_START_WAIT_SECONDS")
    # Nghỉ (giây) sau khi ĐÓNG browser trước khi profile được mở lại — chống kẹt "being opened" khi dùng lại
    # cùng profile liên tiếp (churn). Đủ profile thì hầu như không chạm; đặt 0 để tắt.
    browser_close_settle_seconds: float = Field(default=2.0, alias="BROWSER_CLOSE_SETTLE_SECONDS")
    # Giây chờ browser có tab 'page' (CDP target) SAU khi start trả địa chỉ, TRƯỚC khi DrissionPage attach.
    # Chống race lần đầu mở profile MỚI: browser dựng lên nhưng kịch bản không thao tác gì, phải chạy lần 2.
    browser_cdp_ready_wait_seconds: float = Field(default=30.0, alias="BROWSER_CDP_READY_WAIT_SECONDS")
    # Giây chờ SPA (FB/TikTok/YouTube) render client-side SAU `load` trước khi chụp text detect. Chụp sớm →
    # trang trắng → no_decisive_signal oan → retry/DLQ. Tăng nếu mạng chậm; giảm để nhanh hơn (đánh đổi độ tin).
    browser_render_settle_seconds: float = Field(default=3.0, alias="BROWSER_RENDER_SETTLE_SECONDS")

    # ── Process hygiene / giám sát tài nguyên (INV-9) ───────────────────────────
    # Ngưỡng RAM (MB) cho một browser (cây tiến trình). Vượt → kill cây + giải phóng slot + trả profile.
    browser_ram_limit_mb: float = Field(default=1500.0, alias="BROWSER_RAM_LIMIT_MB")
    # Chu kỳ giám sát RAM/PID browser đang mở.
    resource_monitor_interval_seconds: float = Field(
        default=5.0, alias="RESOURCE_MONITOR_INTERVAL_SECONDS"
    )
    # CHỈ fake mode: số giây sống tối đa của tiến trình "browser giả" (chống rò nếu quên đóng).
    fake_browser_ttl_seconds: float = Field(default=300.0, alias="FAKE_BROWSER_TTL_SECONDS")

    # ── Đồng bộ danh sách profile GemLogin → server (§3) ────────────────────────
    profile_sync_interval_seconds: float = Field(
        default=60.0, alias="PROFILE_SYNC_INTERVAL_SECONDS"
    )

    # ── Forward CDP an toàn (INV-12) — mặc định TẮT (chạy script login local) ────
    cdp_forward_enabled: bool = Field(default=False, alias="CDP_FORWARD_ENABLED")
    cdp_forward_token: str | None = Field(default=None, alias="CDP_FORWARD_TOKEN")


def load_config() -> WorkerConfig:
    _load_dotenv_upwards()
    # Các field bắt buộc được nạp từ env (BaseSettings) lúc runtime → type checker không thấy;
    # thiếu STATION_ID/WS_AUTH_TOKEN vẫn fail-fast bằng ValidationError.
    return WorkerConfig()  # type: ignore[call-arg]
