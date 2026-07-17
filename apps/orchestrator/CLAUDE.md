# CLAUDE.md — apps/orchestrator

Vùng điều phối. NestJS + WebSocket Gateway. Bộ não: quyết định job chạy ở đâu, giữ pool profile, xử lý block.

## Trách nhiệm
- **Consume** job từ RabbitMQ (manual ack, prefetch đồng bộ với công suất).
- **Claim profile** khoẻ atomic (`FOR UPDATE SKIP LOCKED`, INV-11) → chọn station còn slot (`current_load < max_concurrency`) → gửi WS `RUN {url, cookie, command_id}`.
- **Profile pool**: lease, cooldown, health_score, auto-switch (→ skill `profile-lifecycle`).
- **Station registry**: nhận đăng ký, heartbeat; station quá ngưỡng không ping → `OFFLINE` + thu hồi job + re-queue (INV-15).
- Nhận kết quả → ghi `check_logs` (url_status + profile_health riêng) → cập nhật `check_jobs` → set cache → trả profile về pool.
- Circuit breaker theo platform (spec §8.6).

## Luật cục bộ
- **SPOF**: tách state (registry, pool) xuống Redis/PG để chạy đa instance (INV-5).
- Lệnh WS **idempotent + `command_id`** (INV-14). WSS + token.
- Không forward CDP thô ra internet — chỉ gửi lệnh cấp cao (INV-12).
- Auto-switch phải có `max_retries` + alert khi pool thấp — tránh switch vô hạn khi pool cạn.

## Skills liên quan
- `profile-lifecycle` — khi động tới pool, claim, auto-switch.
- `platform-detector` — khi phối hợp với worker về ngữ nghĩa kết quả.

Chi tiết: `docs/station-management-design.md` (thiết kế Hạng mục 2 đầy đủ), `docs/job-lifecycle.md`, `docs/data-model.md`, spec `§4.6`, `§4.7`, `§4.8`, `§6.3`.
