# PROGRESS — FastCheck Automation

> Nhật ký tiến độ dựng source. Cập nhật mỗi khi hoàn thành một mốc. KHÔNG tự nhảy phase kế tiếp khi chưa được duyệt.

## Phase 0 — Khung xương chạy được ✅ HOÀN TẤT (2026-07-17)

Mục tiêu (docs/roadmap.md): monorepo + hạ tầng + migration + WS register/heartbeat, **chưa có nghiệp vụ** (detector/login/auto-switch để phase sau).

### Thay đổi kiến trúc đã áp dụng trong Phase 0
- **ADR-0006**: `apps/worker` chuyển **Playwright (Node) → DrissionPage (Python 3.12 + uv)**; GemLogin giữ nguyên (DrissionPage attach vào CDP endpoint). api/orchestrator/packages/* giữ TypeScript. Ranh giới TS↔Python là WS JSON, worker mirror contract bằng **pydantic**. Concurrency = process pool; kill cây tiến trình bằng `taskkill /T /F`. 16 file design đã đồng bộ + grep-verified.

### Thành phần đã dựng
| Loại | Tên | Nội dung |
|---|---|---|
| package | `@fastcheck/shared` | 8 enum khớp DB (UrlStatus ≠ ProfileHealth — INV-1/3), URL normalizer + `url_hash=sha256` (INV-13), detectPlatform, trace util, logger pino + redact cookie (INV-12) |
| package | `@fastcheck/config` | zod env schema tổng hợp theo app, `parseEnv` fail-fast, `loadApiEnv`/`loadOrchestratorEnv` |
| package | `@fastcheck/crypto` | AES-256-GCM cookie enc/dec qua `node:crypto`, keyring + `cookie_key_id` (xoay khoá) |
| package | `@fastcheck/contracts` | zod: `/check` DTO, queue payload, WS protocol (register/heartbeat/RUN/result), command mang `command_id` (INV-14) |
| package | `@fastcheck/db` | Kysely types + client, repositories (job/profile/station/log/proxy), `claimProfile()` `FOR UPDATE SKIP LOCKED` (INV-11), migration node-pg-migrate |
| app | `@fastcheck/api` | Fastify: `POST /check` (normalize+hash→Redis cache→dedupe check_jobs→push RabbitMQ→202+trace_id), `GET /check/:trace_id`, `/health` |
| app | `@fastcheck/orchestrator` | NestJS + Fastify adapter; WS Gateway (register/heartbeat, WSS token — INV-12); registry in-memory + Postgres; RabbitMQ consumer (khung, manual ack); `/health` |
| app | `@fastcheck/worker` | **Python 3.12 + DrissionPage (uv)**: websockets WS client (register/heartbeat ~10s/auto-reconnect backoff), pydantic contracts, `FakeGemLoginAdapter` (chưa mở browser), kill-tree stub |
| infra | docker-compose | Postgres 16 + Redis 7 (`allkeys-lru`) + RabbitMQ 3.13 |

### Tiêu chí hoàn thành Phase 0 — kết quả đo thực tế

| # | Tiêu chí | Lệnh | Kết quả |
|---|---|---|---|
| 1 | `pnpm install` không lỗi | `pnpm install` | ✅ PASS (pnpm 9.15.9 qua Corepack) |
| 2 | Docker lên PG+Redis+RabbitMQ | `docker compose up -d` | ✅ PASS — cả 3 container `healthy` |
| 3 | Migrate đủ 5 bảng + 3 cột dispatch + index claim + UNIQUE(url_hash) partial | `pnpm db:migrate` + psql | ✅ PASS — 5 bảng (+pgmigrations); `assigned_station_id/assigned_profile_id/dispatched_at`; `idx_profiles_claim`, `idx_check_jobs_status_station`, `uq_check_jobs_active_url_hash ... WHERE status IN ('PENDING','RUNNING')` |
| 4 | Unit test: normalizer / crypto / config fail-fast | `pnpm test` | ✅ PASS — shared 6, config 5, crypto 7, contracts 3, worker(pytest) 2 |
| 5 | `pnpm dev` 3 app; worker hiện trong registry | `pnpm dev` + `GET /health` | ✅ PASS — orchestrator log "station đã đăng ký (ONLINE)"; `/health` liệt kê `dev-station` ONLINE; dừng worker → OFFLINE |
| 6 | POST /check → 202+trace_id + 1 dòng PENDING; GET đọc lại; POST lại khi PENDING không tạo dòng thứ hai | curl POST×2 + GET + psql count | ✅ PASS — 202 + trace_id, POST lần 2 trả **cùng trace_id**, DB đúng **1 dòng** PENDING, GET 200 |
| 7 | `pnpm lint && pnpm typecheck` sạch | `pnpm lint` / `pnpm typecheck` | ✅ PASS — lint 8/8 (eslint + ruff), typecheck 12/12 (tsc + mypy strict 9 files) |

### Ghi chú kỹ thuật (để phase sau lưu ý)
- **ESM toàn TS repo**; worker chạy `tsc && node` cho orchestrator (tsx/esbuild không emit decorator metadata cho NestJS). NestJS chạy tốt trên ESM.
- **Ranh giới TS↔Python**: pydantic phát JSON `null` cho field vắng → zod dùng `.nullish()` cho `mac_address`/`ip_address` (bài học đã sửa trong Phase 0).
- Bind API/Orchestrator ở `0.0.0.0` (IPv4) → khi test dùng `127.0.0.1`, không dùng `localhost` (Windows resolve `::1` trước).
- check_logs hiện là bảng thường; PARTITION BY RANGE(checked_at) sẽ thêm ở phase sau.
- Consumer orchestrator ở Phase 0 chỉ log + ack (khung); dispatch thật xuống station ở Phase 1+.

## Hardening sau review Phase 0 — P1→P5 ✅ HOÀN TẤT (2026-07-17)

Sau khi review kiến trúc, đã vá 5 điểm chưa "chuẩn công nghiệp". Tất cả gate xanh lại sau khi vá (build/typecheck/lint/test) + smoke test runtime.

| # | Vấn đề | Cách vá | Verify |
|---|---|---|---|
| P1 | Contract zod↔pydantic viết tay đôi → drift (đã cắn bug null lúc verify) | Xuất **JSON Schema từ zod** (`packages/contracts/schema/*.json`, script `gen:schema`); **TS test** chống stale (`schema-parity.spec.ts`); **Python test** validate output pydantic theo schema đó (`test_contract_parity.py`, dùng `jsonschema`) | contracts 7 test (gồm 4 parity); worker 4 test (gồm 2 parity) — xanh |
| P2 | Auth WS đặt sau kết nối, so sánh `!==` | Xác thực ở **HTTP upgrade** (`verifyClient`) qua header `Authorization: Bearer`, so sánh **hằng-thời-gian** (`timingSafeEqual`); bỏ `token` khỏi message register (zod+pydantic+worker) | Smoke: worker đăng ký OK qua header; upgrade **không token → HTTP 401** |
| P3 | api/orchestrator mở amqp một lần, không reconnect | Chuyển sang **amqp-connection-manager**: tự reconnect, buffer publish khi broker rớt; `setup` chạy lại sau reconnect (re-assert + re-consume) | Smoke: POST/GET /check qua channel wrapper OK |
| P4 | Xoay khoá cookie chưa nối env | Thêm env optional **`COOKIE_ENC_KEYS`** (JSON keyId→base64 32B) + helper `cookieKeyringFromEnv` (validate fail-fast) | config 9 test (thêm 4 keyring) — xanh |
| P5 | `check_logs` chưa partition, PK `bigserial` | `check_logs` **PARTITION BY RANGE(checked_at)** + PK `(id, checked_at)` + default partition + index kế thừa | psql: `relkind='p'`, có `check_logs_default`, PK composite |

> P6 (integration test biên) và P7 (bắt buộc `wss://` production) — P1 đã phủ phần lớn P6 bằng test parity hai chiều; điều kiện `wss://` production ghi ở đây để triển khai lưu ý. Ba điểm nhỏ còn lại (2 HTTP framework, `noUncheckedIndexedAccess`) là đánh đổi có chủ đích, không vá.

## Phase 1 — CHƯA BẮT ĐẦU
Chờ được duyệt. Nội dung: một đường sống end-to-end + kỷ luật chính xác (1 platform, detector 3 nhánh + guard + vote, golden set).
