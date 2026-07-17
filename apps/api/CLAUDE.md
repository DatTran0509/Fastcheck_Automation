# CLAUDE.md — apps/api (FastCheck API)

Vùng tiếp nhận. Fastify, **stateless**, nhân bản nhiều instance sau load balancer. Nhiệm vụ: nhận request, trả nhanh, đẩy việc nặng xuống queue.

## Trách nhiệm
- `POST /check {url}` → validate → **normalize URL** → `url_hash = sha256` (INV-13) → tra Redis → (hit: trả `<500ms`) / (miss: lock stampede → upsert `check_jobs` `ON CONFLICT DO NOTHING` → push RabbitMQ → trả `202` + `trace_id`).
- `GET /check/{trace_id}` — poll. Tuỳ chọn webhook/SSE.
- Rate-limit theo client (chống spam). Circuit breaker: platform bị mở circuit → trả `503` + `retry_after`.
- Nhận diện platform + loại target từ URL bằng regex/parser theo domain.

## Luật cục bộ
- API **không giữ trạng thái** — mọi thứ ở Postgres/Redis (INV-4, INV-5).
- Dùng normalizer từ `packages/shared` (đảm bảo `url_hash` khớp với worker). DTO khai báo ở `packages/contracts` (zod).
- Rate-limit dùng Lua script (atomic INCR+EXPIRE) để tránh race giữa nhiều instance.
- Không cache INCONCLUSIVE. TTL LIVE ngắn hơn DEAD.
- Trả lỗi rõ ràng khi platform không hỗ trợ.

## Nhớ
- Cache hit `<500ms` là KPI. Đừng làm route này nặng thêm.
- "50 concurrent ở API" là dễ vì async — đừng nhầm với "50 check thật" (đó là việc của worker). Xem `docs/architecture.md`.
- OpenAPI/Swagger cho các endpoint: schema, mã lỗi, ví dụ, rate limit, ngữ nghĩa LIVE/DEAD/INCONCLUSIVE, SLA.

Chi tiết: `docs/job-lifecycle.md`, spec `§4.1`, `§4.2`, `§6.1`.
