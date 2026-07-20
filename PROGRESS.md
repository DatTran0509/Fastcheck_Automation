# PROGRESS — FastCheck Automation

> Nhật ký tiến độ dựng source. Cập nhật mỗi khi hoàn thành một mốc. KHÔNG tự nhảy phase kế tiếp khi chưa được duyệt.

## Làm lại Dashboard + enabler backend + audit Excel (2026-07-19)

**Audit vs `refs/GHN_DPA_DanhGiaThuViec_Dat_v1.1.xlsx`:** mọi yêu cầu Mục 1+2 đều CÓ trong code & đã wire (X login cả info+cookie, TT info-login, FB/YT cookie, auto-switch, multi-profile, station mgmt, sync profile). Gap = PROOF/OPS, không phải thiếu tính năng: G1 ≥98% accuracy chưa đo trên tài khoản thật (mới golden), G2 info-login là kênh operator (chưa auto-fallback trong job khi cookie chết giữa chừng), G3 KPI 50-concurrent đo ở fake mode, G4 CDP forward mới là stub chính sách (chưa có transport), G5 xoá profile không được trên GemLogin Free.

**Backend enabler:**
- `GET /dashboard/jobs` — lịch sử job có filter (platform/status/url ILIKE) + phân trang + JOIN check_log mới nhất (result/profile_health/block_reason/response_time). `jobRepo.listJobs`/`countJobs` + `dashboard.dto` (jobHistory) + `DashboardService.jobsHistory`.
- LÝ DO cooldown: migration `profile_last_error` (`last_error`,`last_error_at`); `recordFailure`/`cooldownProfile` ghi lý do ("CHALLENGED/BLOCKED/THROTTLED: …"), success/release/register xoá; `dispatch` build reason; `StationProfileView` thêm `status_reason`/`status_reason_at`/`cooldown_until`/`consecutive_fails`.

**Dashboard làm lại từ đầu (React + react-router + recharts + SheetJS):** shell sidebar+topbar+theme tối; 6 trang — Tổng quan (KPI + pie + bar theo platform + cảnh báo), Stations (bảng realtime + circuit), Profiles (bảng rõ ràng + LÝ DO cooldown + khuyến nghị, thay JSON), Kết quả (bảng job: link, search, filter, lazy-load IntersectionObserver, **export Excel**), Tài khoản (nạp/login/mở-tắt browser/check + OTP), Hướng dẫn. Bỏ banner "pool thấp" ở header → đưa vào trang Tổng quan.

**Gates:** build 8/8, typecheck 13/13, lint 7/7, orchestrator 21/21, worker pytest 88. Cần RESTART `pnpm dev` (orchestrator+dashboard) + `pnpm dev:worker`.

### Tinh chỉnh sau phản hồi (2026-07-20)
- **X post ra INCONCLUSIVE (`no_decisive_signal`)** = race SPA: settle cố định 3s chụp trước khi X render tweet/thông báo lỗi. Fix: `DrissionPageSource` **chờ tín hiệu quyết định** (union live/dead/block selector + text của detector) tới trần `render_wait` (~12s), trả ngay khi thấy → hết chụp sớm oan. runner truyền `detector.spec`. Broaden X dead_texts (đổi "tweets"→"posts"). CẦN restart worker.
- **Trang CHƯA render** (JS/asset không tải — vd X `ChunkLoadError` do IP bị chặn/thiếu proxy): thêm `text_length()` cho PageView; detect khi INCONCLUSIVE + text < 40 ký tự → **THROTTLED + `page_not_rendered:assets_failed`** (lỗi hạ tầng, không phạt tài khoản, báo rõ để operator biết cần proxy/đổi IP) thay vì `no_decisive_signal` mơ hồ. 2 test mới (pytest 90).
- **reapExpiredCooldowns** dọn cả COOLDOWN `cooldown_until` NULL (chống kẹt) + test.
- **UI**: gộp "Nạp tài khoản" vào trang Pool (component `AccountControls`, nạp xong tự refresh bảng); chuyển "Gửi check" lên đầu trang Kết quả (gửi xong tự refresh); thêm **cột ID** (trace_id, bấm copy) ở bảng job; auto-detect nền tảng từ domain cookie (cảnh báo chọn nhầm); khối login-by-info LUÔN hiện. Nav còn 5 trang.

## Sửa nghiệp vụ: `profiles` là BẢN SAO (MIRROR) của GemLogin (2026-07-19)

Lỗi phát hiện khi test thật: xoá profile bên GemLogin → DB không đổi; "Xem profile" chỉ hiện profile đã nạp chứ không phản ánh số profile GemLogin. Nguyên nhân: sync **BỎ QUA** profile không có nhãn `fastcheck-platform=` ở note → DB không bao giờ mirror đúng GemLogin. Vi phạm design §3 ("danh sách profile trên máy KHỚP bảng profiles sau đồng bộ").

Sửa (mô hình mirror, 1 GemLogin profile = 1 dòng — INV-6):
- **DB**: migration `profile_platform_nullable` — `profiles.platform` **NULLABLE**. NULL = profile đã mirror nhưng CHƯA gán nền tảng. `claimProfile` lọc `platform = X` → NULL không bao giờ được cấp job (không dispatch nhầm).
- **contracts/worker**: `stationProfileSchema.platform` + `stationProfileViewSchema.platform` nullable (regen zod→JSON→pydantic); worker `_sync_profiles` gửi **MỌI** profile GemLogin (không nhãn → platform=None), không skip.
- **repo**: `upsertStationProfiles` mirror toàn bộ + **KHÔNG clobber** platform đã gán bằng NULL (sync sau note trống không xoá gán). `registerAccount` dedup theo **gid** (= "Nạp tài khoản" GÁN nền tảng cho dòng mirror, không tạo trùng). `countByStatusAll` loại profile chưa gán khỏi metric pool.
- **Kết quả**: "Xem profile" khớp đúng danh sách GemLogin; xoá bên GemLogin → prune tự gỡ; profile mới chưa gán vẫn hiện (platform null) tới khi nạp.
- **Gates**: build 8/8, typecheck 13/13, worker mypy 30 + ruff + pytest 88, orchestrator **21/21** (+test mirror/no-clobber). Cần RESTART worker + orchestrator để nạp.

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

## Tinh chỉnh kiến trúc DrissionPage (theo lưu ý người dùng) ✅ (2026-07-17)

Chốt lại ranh giới TS↔Python cho đúng thiết kế:

- **contracts**: thêm `messages.schema.json` (JSON Schema có `$defs` đặt tên, xuất từ zod) — dùng để **sinh model pydantic** cho worker. `gen:schema` xuất đủ union + messages.
- **worker (Python)**:
  - **pydantic-settings** cho env (`WorkerConfig(BaseSettings)`), fail-fast — mirror `packages/config`.
  - Model pydantic **SINH TỪ JSON Schema** qua `pnpm worker:gen` (datamodel-code-generator → `_contracts_gen.py`); `contracts.py` re-export + `parse_server_message` (validate message Server→Client bằng chính model sinh ra).
  - Register gửi kèm **`mac`** (uuid.getnode); **lưu `command_id` đã xử lý → idempotent** (INV-14); validate message vào bằng pydantic.
  - GemLoginAdapter/FakeGemLoginAdapter giữ nguyên (chưa mở browser).
- **Root/turbo**: worker **KHÔNG còn là workspace pnpm/turbo** (đã xoá `apps/worker/package.json`). `pnpm dev` chỉ chạy app TS; thêm script **`dev:worker`**, `worker:lint|typecheck|test|gen`. docker-compose giữ nguyên.
- Verify: `worker:lint` ✓ · `worker:typecheck` (mypy strict, 10 files) ✓ · `worker:test` 4 ✓ · TS gate build/typecheck/lint/test ✓ (contracts 8 test gồm schema-parity 5) · smoke: worker đăng ký qua header auth, WS không token → 401, POST/GET /check ✓.

## Phase 1 — Một đường sống end-to-end (TikTok) + kỷ luật chính xác ✅ HOÀN TẤT (2026-07-18)

Mục tiêu (docs/roadmap.md): dựng MỘT đường sống end-to-end cho 1 platform (TikTok), chạy với
`GEMLOGIN_MODE=fake`, kèm detector 3 nhánh + guard + vote + golden set. Chạy được thật với Docker
hạ tầng Phase 0 (Postgres/Redis/RabbitMQ).

### Thành phần đã dựng / sửa

| Vùng | Thay đổi |
|---|---|
| **worker/detectors** | `base.py`: `PageView` Protocol, `SignalSpec`, `Signals`, `verify_logged_in` (guard INV-2), `vote_engine` (3 nhánh, KHÔNG `else DEAD` — INV-1), `BaseDetector` (block→guard→vote; mọi exception→INCONCLUSIVE). `tiktok.py`: bảng tín hiệu khởi đầu (spec §10.5). `html_view.py`: engine selector bền dựa `html.parser` (tag/#id/.class/[attr op value], op `= *= ^= $= ~=`), selector rỗng khớp **KHÔNG** (chống hỏng âm thầm), bỏ text trong `<script>/<style>`. `__init__.py`: registry `get_detector(platform)`. |
| **worker/browser** | `page_source.py`: `FakePageSource` — thay thế dev/test cho GemLogin+DrissionPage; inject cookie TRƯỚC điều hướng (INV-2), tải qua urllib (status thật), map URL nền tảng→fixture. |
| **worker/runner** | `runner.py`: `run_check` (hàm module picklable) + `CheckRunner` (ProcessPoolExecutor size=max_concurrency — INV-10, timeout cứng — INV-9; timeout/lỗi→INCONCLUSIVE). |
| **worker/ws_client** | Xử lý lệnh `script.run`: chạy detector trong pool → gửi `job_result` (url_status TÁCH BIỆT profile_health — INV-3); send-lock cho socket; `current_load` = số job đang chạy; idempotent command_id (INV-14). `config.py`: thêm `JOB_TIMEOUT_SECONDS`, `FIXTURE_BASE_URL`. |
| **api** | `services/lock.ts`: `StampedeLock` (`SET lock:{hash} NX EX 10`); route `POST /check` giành khoá trước khi tạo job (chống stampede §6.2); log `trace_id` khi nhận + khi cache hit. |
| **orchestrator** | `dispatch/dispatch.service.ts`: consume→`pickAvailableStation` (reserve slot realtime chống cấp quá — INV-10)→`claimProfile` atomic (INV-11)→`markRunning`+cột dispatch (INV-15)→giải mã cookie (`packages/crypto`, INV-12)→WS `RUN`. Nhận `job_result`→`insertCheckLog` (url_status+profile_health riêng — INV-3)→`markDone`→set cache (LIVE ngắn hơn DEAD; KHÔNG cache INCONCLUSIVE — INV-1)→trả profile→**ack** (manual ack — INV-4). Providers Redis + CookieCipher. Consumer requeue có trễ khi thiếu tài nguyên (Phase 3: retry+backoff thật). |
| **db** | `job.repo`: `markRunning` (RUNNING + 3 cột dispatch), `markDone` (DONE + result + finished_at). |
| **contracts** | Xuất type `JobResultMessage`, `ServerCommand` (shape không đổi → schema/pydantic không đổi). |
| **config** | `orchestratorEnvSchema`: thêm `RESULT_CACHE_TTL_LIVE/DEAD_SECONDS` (orchestrator set cache). |
| **golden set** | `apps/worker/tests/fixtures/`: `live/dead_404/soft404_200/login_wall/captcha/missing_selector.html` + `fixture_server.py` (HTTP status thật: dead_404→404). |
| **E2E harness** | `scripts/e2e_phase1.py` (khởi động api+orchestrator+worker + fixture server, chạy C/D/F). `apps/orchestrator/src/scripts/seed-tiktok-profile.ts` (seed profile TikTok + cookie mã hoá). |

### Ràng buộc chính xác đã giữ (quyết định KPI 98%)
- Đủ 3 nhánh; DEAD chỉ trả từ MỘT chỗ (`vote_engine`, nhánh có phiếu dead & không phiếu live). Không `else DEAD` (INV-1).
- Guard đăng nhập + phát hiện block chạy TRƯỚC vote target; chưa login→INCONCLUSIVE+CHALLENGED, captcha→INCONCLUSIVE+BLOCKED (INV-2/INV-3).
- Vote đa tín hiệu (HTTP status + DOM + URL cuối); selector bền + fallback (INV-8). Soft-404 bắt bằng NỘI DUNG, không chỉ status.
- Mọi exception/timeout → INCONCLUSIVE, không DEAD.

### Kết quả đo thực tế (báo cáo A–F)

| Test | Nội dung | Lệnh | Kết quả |
|---|---|---|---|
| A | Unit vote engine: mỗi tổ hợp tín hiệu → đúng nhánh; thiếu tín hiệu→INCONCLUSIVE; guard/block trước vote; exception→INCONCLUSIVE | `uv run pytest tests/test_vote_engine.py` | ✅ 10/10 |
| B | Golden set TikTok qua static server thật: live→LIVE·OK; dead_404→DEAD; soft404_200→DEAD; **login_wall→INCONCLUSIVE+CHALLENGED**; captcha→INCONCLUSIVE+BLOCKED; **missing_selector→INCONCLUSIVE** | `uv run pytest tests/test_golden_tiktok.py` | ✅ 7/7 (2 ca cốt tử xanh) |
| C | E2E: POST→PENDING→RUNNING→DONE; check_logs 1 dòng `LIVE|OK|201ms` (CẢ url_status LẪN profile_health); check_jobs.result=LIVE; cache set | `python scripts/e2e_phase1.py` | ✅ PASS |
| D | Cache hit **31ms** (<500ms) + KHÔNG tạo job mới (before=after=1); login_wall→INCONCLUSIVE **KHÔNG vào cache** (redis nil) | `python scripts/e2e_phase1.py` | ✅ PASS |
| E | Grep tĩnh: `UrlStatus.DEAD` chỉ xuất hiện ở `base.py:136` (nhánh có phiếu dead trong `vote_engine`); mọi `except`/`timeout` trả INCONCLUSIVE; không có `else DEAD` | `grep -rn DEAD apps/worker/fastcheck_worker` | ✅ 1 chỗ trả DEAD, đúng nhánh tín hiệu |
| F | Cùng `trace_id` xuất hiện ở log **api + orchestrator + worker** và trong **check_logs** | `python scripts/e2e_phase1.py` | ✅ PASS (4/4) |

Gate toàn repo: `pnpm build` ✓ · `pnpm typecheck` 12/12 ✓ · `pnpm lint` 7/7 ✓ · `pnpm test` (TS) 17 ✓ · worker `ruff` ✓ · `mypy` strict 16 files ✓ · `pytest` **21** ✓.

### Ghi chú kỹ thuật (để phase sau lưu ý)
- **FakePageSource** (urllib) là bản thay thế dev/test cho GemLogin+DrissionPage. Đường thật (DrissionPage attach CDP) hiện thực cùng `PageView` Protocol ở Phase 2+ — detector KHÔNG phải đổi.
- **Engine selector tự viết** (`html_view.py`) chỉ phủ tập con selector đủ cho detector, chạy không cần Chromium (golden set/CI ổn định). Bài học: selector không phân tích được phải khớp **rỗng**, tuyệt đối không "khớp tất cả" (từng gây mọi trang thành BLOCKED khi thiếu op `*=`).
- **Phase 1 chốt INCONCLUSIVE thành kết quả job** (DONE + result=INCONCLUSIVE) cho đủ đường sống; **auto-switch/re-queue** đúng INV-1 (đẩy lại queue check bằng profile khác) là **Phase 3** (roadmap). Cooldown theo profile_health cũng để Phase 3.
- Consumer requeue có trễ 1s khi thiếu station/profile — chống hot-loop; **retry+backoff qua `job.retry` + DLQ** là Phase 3.
- Worker chạy Python 3.14 trong venv uv (pyproject target 3.12; mypy target 3.12 vẫn xanh).

## Phase 4 — Station Management đầy đủ + phục hồi & tài nguyên ✅ HOÀN TẤT (2026-07-18)

Mục tiêu (docs/roadmap.md, station-management-design §1–§10, INV-9/12/14/15): Client App đầy đủ (CRUD
profile GemLogin + đồng bộ + mở/tắt browser), **phát hiện station chết → thu hồi job** (INV-15), lệnh WS
**idempotent** (INV-14), **process hygiene** (kill cây, giám sát RAM/PID), **forward CDP an toàn** (INV-12),
backpressure nhất quán (INV-10), và **load test 50 concurrent**. Vẫn `GEMLOGIN_MODE=fake` cho đo pipeline.

### Thành phần đã dựng / sửa
| Vùng | Thay đổi |
|---|---|
| **contracts** | WS protocol: `profile_sync` (Station→Server, §3), `command_ack` giàu hơn (`ok/detail/profile_id`), lệnh `profile.create/update/delete` (§4); regen JSON Schema + pydantic (`worker:gen`) — parity giữ nguyên. |
| **db** | Migration `station_profile_sync`: cột `gemlogin_profile_id` + unique partial `(assigned_station_id, gemlogin_profile_id)`. Repo: `job.findRunningByStation`/`findAllRunning`/`getJobById`, `profile.upsertStationProfiles` (không đụng status/health pool — INV-11). |
| **orchestrator/dispatch** | **`JobPublisher`** (kênh publish tách khỏi consumer — hết phụ thuộc vòng); dùng cho retry/DLQ + **thu hồi** (job.pending). **`recoverStationJobs`** (station chết → RUNNING→PENDING + clear cột dispatch + trả profile + re-queue, GIỮ retry_count vì lỗi hạ tầng) + **`recoverOrphanRunning`** (startup sweep). Guard **INV-4**: message mồ côi (job không còn PENDING) → ACK bỏ, không dispatch (chống double-dispatch + message rác). |
| **orchestrator/consumer** | Sau khi bind queue → gọi `recoverOrphanRunning` (thu hồi mọi RUNNING sau orchestrator restart nhờ cột dispatch). |
| **orchestrator/lifecycle** | **`StationMonitorService`** — cron phát hiện station quá hạn heartbeat (`HEARTBEAT_TIMEOUT_MS`) → OFFLINE + thu hồi. |
| **orchestrator/registry** | `takeStale` (phát hiện quá hạn, lật OFFLINE — idempotent), `isActiveSocket` (chống race reconnect), `syncProfiles` (§3). |
| **orchestrator/ws.gateway** | Handler **catch-all** (một message lỗi KHÔNG làm sập gateway — self-healing); `profile_sync` → upsert; socket-close → `recoverStationJobs` (bảo vệ bằng `isActiveSocket`). |
| **worker/browser** | `GemLoginAdapter` đầy đủ: **RealGemLoginAdapter** (gọi API local GemLogin CRUD + start/stop + lấy CDP address; DrissionPage `ChromiumOptions().set_address` attach) + **FakeGemLoginAdapter** (CRUD in-memory; mở browser = spawn **cây tiến trình THẬT** để test hygiene; `open_browser` idempotent theo profile — INV-6). `cdp_forward.py`: chính sách forward an toàn (mặc định local, bật cần token, thiếu token → fail-fast — INV-12). |
| **worker/process** | `kill.py` cross-platform qua **psutil** (Windows `taskkill /T /F`; Linux SIGTERM→SIGKILL; chụp cây con TRƯỚC khi kill cha). `monitor.py`: `ResourceMonitor` (RAM/PID cây tiến trình; vượt ngưỡng → kill + callback giải phóng). |
| **worker/ws_client** | Xử lý `browser.open/close` + `profile.*` qua adapter, **idempotent + trả kết quả cũ** khi trùng command_id (INV-14); `command_ack` (ok/detail); **đồng bộ profile định kỳ** + sau CRUD (§3); **giám sát tài nguyên** loop; forward CDP theo chính sách. Adapter chạy `asyncio.to_thread` (blocking I/O). |
| **config** | orchestrator: `HEARTBEAT_TIMEOUT_MS`, `STATION_MONITOR_INTERVAL_MS`. worker: `GEMLOGIN_API_URL`, `BROWSER_RAM_LIMIT_MB`, `RESOURCE_MONITOR_INTERVAL_SECONDS`, `FAKE_BROWSER_TTL_SECONDS`, `PROFILE_SYNC_INTERVAL_SECONDS`, `CDP_FORWARD_ENABLED/TOKEN`. `.env.example` cập nhật. |
| **load test** | `scripts/loadtest.js` (k6, artifact chuẩn) + `scripts/e2e_phase4_loadtest.py` (driver ThreadPool 50 concurrent + đo — k6 chưa cài ở đây). |

### Kết quả đo thực tế (báo cáo 1–6)
| # | Test | Cách tái hiện | Kết quả |
|---|---|---|---|
| 1 | **Idempotency**: cùng command_id "mở browser" 2 lần → chỉ 1 browser | `pytest tests/test_command_idempotency.py` (WS server giả gửi trùng) + `test_gemlogin_adapter.py` (open idempotent/profile) | ✅ open_calls=1 |
| 2 | **Station chết**: job RUNNING → dừng heartbeat/kill → OFFLINE, mọi RUNNING re-queue (check_jobs), profile về pool, cột dispatch clear; **orchestrator restart** vẫn thu hồi nhờ cột dispatch | `python scripts/e2e_phase4.py` (2a kill worker, 2b restart) + `vitest test/recovery.int.spec.ts` (4) | ✅ 2a PENDING+clear+AVAILABLE; 2b startup sweep; int 4/4 |
| 3 | **Reconnect**: bounce orchestrator → worker TỰ reconnect + đăng ký lại; job đang dở xử lý đúng | `python scripts/e2e_phase4.py` (3) | ✅ ONLINE lại + job DONE |
| 4 | **Process hygiene**: mở browser (fake) rồi kill → KHÔNG còn con mồ côi (psutil children); job timeout → dọn + INCONCLUSIVE | `pytest tests/test_process_hygiene.py` (kill cây, RAM breach, hard timeout) | ✅ 3/3 (0 orphan) |
| 5 | **Load test 50 concurrent** (fake): không crash; browser đồng thời ≤ tổng max_concurrency; queue xếp hàng rồi rút; p95 + RAM đỉnh; prefetch>pool vs =pool | `uv run python scripts/e2e_phase4_loadtest.py` | ✅ (số đo bên dưới) |
| 6 | lint/typecheck (TS) + ruff/mypy (Python) + test sạch | các gate | ✅ (bên dưới) |

**Số đo load test** (50 concurrent, GEMLOGIN_MODE=fake, pool=8):
- Hoàn tất **50/50** cả hai phase; **không crash** (health 200 sau tải).
- **Browser đồng thời tối đa = 8 = pool** (fixture đếm in-flight) → cap concurrency giữ đúng (INV-10).
- **Queue depth đỉnh 18–19 → rút về 0** (backpressure: tải vượt công suất thì XẾP HÀNG rồi tiêu hết).
- **p95 POST latency 190–244ms** (< 500ms KPI); p95 hoàn tất job 1.4–2.4s; **RAM đỉnh worker ~76MB**.
- **prefetch>pool → churn "không có station còn slot" = 16** vs **prefetch=pool = 0** (INV-10: backpressure lệch gây requeue lãng phí — chỉnh bằng nhau thì sạch).

Gate toàn repo: `pnpm typecheck` 12/12 · `pnpm lint` 7/7 · `pnpm test` (shared 6, config 9, crypto 7,
contracts 8, **orchestrator 12** gồm 4 recovery int) · worker `ruff` ✓ · `mypy` strict **19 files** ✓ ·
`pytest` **32** (thêm 10: hygiene 3, adapter 3, cdp 3, idempotency 1). Regression: `e2e_phase1` (C/D/F) ✅ ·
`e2e_phase3` (3a/3b/4) ✅.

### Ghi chú kỹ thuật (để phase sau lưu ý)
- **Hardening resilience (INV-4/INV-5)**: (a) WS gateway bắt lỗi handler — một `job_result` mồ côi / lỗi DB KHÔNG làm sập orchestrator (trước đây `void handle()` là unhandled rejection → crash cả service). (b) `dispatch` bỏ message mồ côi (job không còn PENDING trong check_jobs) → chống double-dispatch + tự lành trước message rác từ lần chạy trước. Đây là bug tiềm ẩn có từ Phase 0, lộ ra khi queue tích message cũ.
- **RealGemLoginAdapter code-complete nhưng chưa test** (không có GemLogin trong CI): đường dẫn endpoint API GemLogin (`/api/v3/profiles/...`) chỉnh theo bản đã cài; DrissionPage attach qua `set_address`.
- **FakeGemLoginAdapter** mở "browser giả" (`browser/_fake_browser.py` — cây tiến trình THẬT) để test kill cây tất định, không cần Chromium/GemLogin.
- **Heartbeat interval < timeout** là bắt buộc (nếu không station khoẻ bị đánh OFFLINE nhầm giữa hai nhịp ping). E2E dùng interval 2s / timeout 6s.
- **Forward CDP**: hiện là CHÍNH SÁCH an toàn (mặc định local, bật cần token — INV-12) + test; dựng tunnel WSS thật thuộc hạ tầng triển khai.
- **Phase 2 còn nợ**: 3 platform còn lại (FB/X/YouTube) + login-by-info (TikTok & X). Dashboard (§8, điểm cộng) cũng chưa dựng.

## Phase 3 — Pool + auto-switch + rate-limit + đồng thời/phục hồi ✅ HOÀN TẤT (2026-07-18)

Mục tiêu (docs/roadmap.md): vòng đời profile đầy đủ (claim atomic, lease, health/cooldown/consecutive_fails),
rate-limit, **auto-switch** khi bị block (re-queue backoff → DLQ), và chạy nhiều profile song song có
backpressure. Vẫn TikTok + `GEMLOGIN_MODE=fake`; **Phase 2 (đủ 4 platform + login-by-info) CHƯA làm** —
tính năng Phase 3 độc lập platform nên dựng trên nền TikTok của Phase 1.

### Quyết định kiến trúc: ADR-0007 (worker concurrency)
Theo BRIDGE của người dùng, worker chuyển **process pool → bounded thread pool** (size = max_concurrency
= prefetch). Đây là điểm **mâu thuẫn câu chữ với INV-10/ADR-0006** ("không dùng thread điều khiển browser")
nên KHÔNG làm lặng lẽ: đã ghi **ADR-0007** hoà giải — browser thật do **GemLogin** chạy trong tiến trình
RIÊNG (cách ly INV-6 vẫn nguyên), worker chỉ gửi lệnh CDP blocking I/O nên thread-per-browser hợp lý;
phần bất biến **pool size = max_concurrency = prefetch** giữ nguyên. Đã cập nhật `docs/invariants.md` INV-10.

### Thành phần đã dựng / sửa
| Vùng | Thay đổi |
|---|---|
| **worker/runner** | `ProcessPoolExecutor` → **`ThreadPoolExecutor`** (bounded, size=max_concurrency), thêm `max_concurrency` property; timeout cứng giữ nguyên (INV-9). |
| **db/profile.repo** | `claimProfile` (đã có, INV-11) + **`recordSuccess`** (AVAILABLE + hồi health cap100 + reset fails), **`recordFailure`** (giảm health, tăng fails; ≥ngưỡng→DEAD, ngược lại→COOLDOWN+cooldown_until), **`reapExpiredLeases`** (cron), **`countAvailable`**, **`getProfile`**. |
| **db/proxy.repo** | **`noteProxyFailure`** (fail_count++, ≥ngưỡng→COOLDOWN), **`rotateProfileProxy`** (gán proxy ACTIVE khác — phiên SAU, INV-7). |
| **db/job.repo** | **`markRetrying`** (→PENDING + retry_count + clear dispatch), **`markDeadLetter`** (→DEAD_LETTER). |
| **orchestrator/ratelimit** | **`RateLimiter`** — token bucket ATOMIC bằng Lua trong Redis; key `rl:{platform}` + `rl:{platform}:{profile}` (§4.1d/§8.1). |
| **orchestrator/lifecycle** | **`LeaseReaperService`** — cron mỗi phút gọi `reapExpiredLeases` (INV-11). |
| **orchestrator/dispatch** | Rate-limit trước/sau claim; **auto-switch** trong `handleResult`: thành công→chốt+cache+hồi health; lỗi profile→COOLDOWN/DEAD (+xoay proxy nếu BLOCKED); INCONCLUSIVE-profile-khoẻ→trả profile; rồi **re-queue `job.retry` (backoff `expiration`)** hoặc **DLQ+DEAD_LETTER+ALERT** khi vượt max_retries; cảnh báo pool thấp. |
| **orchestrator/consumer** | Khai báo topology **`job.retry`** (x-dead-letter → `job.pending`, backoff bằng per-message `expiration`) + **`job.dlq`**. |
| **config** | orchestrator env: max_retries, backoff base/max, health penalty/bump, dead_threshold, cooldown, pool watermark, proxy ban, lease reap interval, lease minutes, rate-limit capacity/refill (platform+profile). `.env.example` cập nhật. |

### Kết quả đo thực tế (báo cáo 1–7)
| # | Test | Cách tái hiện | Kết quả |
|---|---|---|---|
| 1 | Claim đồng thời (Postgres THẬT, không mock): 20 claim/pool20 → 20 profile khác nhau; 20 claim/pool5 → đúng 5 thành công + 15 null (SKIP LOCKED, không treo) | `pnpm --filter @fastcheck/orchestrator exec vitest run test/claim.int.spec.ts` | ✅ 2/2 |
| 2 | Lease: claim rồi để lease quá hạn → `reapExpiredLeases` → AVAILABLE | `... vitest run test/lease-health.int.spec.ts` | ✅ |
| 3 | Auto-switch: (3a) flaky BLOCKED→LIVE: job DONE=LIVE, retry_count=1, check_logs `BLOCKED,OK` (đổi profile); (3b) captcha luôn BLOCKED → DEAD_LETTER, retry_count dừng ở max=2 (không vô hạn), ALERT log DLQ, 3 dòng log thử | `python scripts/e2e_phase3.py` (3a, 3b) | ✅ PASS |
| 4 | Pool cạn: mọi profile COOLDOWN → job KHÔNG hoàn tất (chờ, không sai) + log "pool cạn profile" + requeue có trễ (không tight-loop); phục hồi 1 profile → job DONE | `python scripts/e2e_phase3.py` (4) | ✅ PASS |
| 5 | Rate-limit token bucket (Redis THẬT): tiêu hết capacity → bị từ chối (retryAfter>0); refill theo thời gian → cho phép lại | `... vitest run test/rate-limiter.int.spec.ts` | ✅ 2/2 |
| 6 | Multi-profile: 9 job qua pool size 3 → số request đồng thời ≤ 3 (server đếm), mỗi job một fetch độc lập, tất cả LIVE | `uv --directory apps/worker run pytest tests/test_concurrency.py` | ✅ |
| 7 | lint/typecheck/test sạch | `pnpm typecheck` · `pnpm lint` · `pnpm test` · worker `ruff`/`mypy`/`pytest` | ✅ typecheck 12/12 · lint 7/7 · TS test (config 9, contracts 8, **orchestrator 8 integration**) · worker **22 pytest** + ruff + mypy strict 16 files |

Regression: `python scripts/e2e_phase1.py` vẫn ✅ (C/D/F) — đã cập nhật ca login_wall theo ngữ nghĩa
Phase 3 (INCONCLUSIVE/CHALLENGED **không còn chốt DONE** mà auto-switch; bất biến "INCONCLUSIVE không vào
cache" vẫn giữ). Grep tĩnh: `UrlStatus.DEAD` vẫn CHỈ ở `base.py:136` (INV-1 nguyên vẹn sau Phase 3).

### Ghi chú kỹ thuật (để phase sau lưu ý)
- **Circuit breaker (§10.6) CHƯA làm** — không nằm trong DỰNG/tests Phase 3; đã đọc để hiểu, để lại Phase 4 (chịu tải/bảo vệ pool diện rộng: API `503`+`retry_after` khi tỷ lệ BLOCKED của platform vượt ngưỡng).
- **Xoay proxy** đã có code (`noteProxyFailure`/`rotateProfileProxy`) nhưng chỉ thực sự tác dụng khi có pool proxy (chưa seed) — an toàn, no-op khi không có proxy thay thế.
- Backoff dùng **per-message `expiration`** trên `job.retry` + dead-letter về `job.pending` → backoff tăng dần mà không cần nhiều queue.
- Test tích hợp TS đặt ở `apps/orchestrator/test/*.int.spec.ts` (ngoài `src` → không vào build), chạy với **Postgres/Redis THẬT** từ `.env`; cách ly bằng platform (FACEBOOK/YOUTUBE) + prefix label, tự dọn.
- E2E harness flush cache Redis + reset `cooldown_until` khi bắt đầu để chạy lại tất định.
- **Phase 2 còn nợ**: 3 platform còn lại (FB/X/YouTube) + login-by-info (TikTok & X). Detector/login theo `platform-detector`.

## Phase 5 — Hoàn thiện: circuit breaker + API docs + observability + dashboard ✅ HOÀN TẤT (2026-07-18)

Mục tiêu (roadmap Phase 5, §6.9/§6.11/§10.6, tech-stack observability): circuit breaker theo platform,
tài liệu API (/docs), metric Prometheus (/metrics), dashboard React realtime. Vẫn `GEMLOGIN_MODE=fake`.

### Thành phần đã dựng / sửa
| Vùng | Thay đổi |
|---|---|
| **contracts** | `dashboard.dto.ts` (snapshot SSE: stations/ratios/pool/recent_jobs/circuits/alerts — KHÔNG cookie), `checkCircuitOpenSchema` (503), heartbeat thêm `ram_mb`/`cpu_percent`. Regen JSON Schema + pydantic. |
| **shared** | `circuit.ts`: `circuitKeys(platform)` + `CircuitState` — schema KEY Redis dùng chung giữa orchestrator (ghi) và API (đọc). |
| **orchestrator/circuit** | **`CircuitBreakerService`** (§10.6): cửa sổ trượt ZSET Redis; tỷ lệ BLOCKED/lỗi ≥ ngưỡng (≥ MIN_SAMPLES) → MỞ (`open_until`); cooldown → HALF_OPEN thăm dò → ĐÓNG/MỞ lại. Trạng thái ở Redis (INV-5: mất Redis → reset đóng, KHÔNG sai). |
| **orchestrator/metrics** | **`MetricsService`** (prom-client): counter result theo platform+url_status (3 nhánh TÁCH BIỆT — INV-1/3) + profile_health, histogram duration (p95), gauge queue depth / profiles theo status / proxy fail / station load / worker RAM+CPU / circuit open. `/metrics` controller. Counter inline khi có kết quả; gauge refresh định kỳ. |
| **orchestrator/dashboard** | **`DashboardService`** + SSE `/dashboard/stream` (@Sse) + `/dashboard/snapshot`. Ratios từ `check_logs` (cửa sổ), pool từ `profiles`, recent jobs, circuits, alerts (circuit mở / block spike / pool thấp). CORS GET cho dashboard. |
| **orchestrator/dispatch** | `handleResult` ghi **metric** + **circuit** (blocked = profile_health≠OK). registry lưu ram/cpu từ heartbeat; consumer poll độ sâu queue vào gauge. |
| **api** | **Circuit check** ở `POST /check`: platform mở → **503 + Retry-After + retry_after_seconds** (cache HIT vẫn phục vụ — không tiêu pool). **@fastify/swagger + fastify-type-provider-zod**: OpenAPI sinh TỪ zod DTO, phục vụ `/docs` (+`/docs/json`). **prom-client** `/metrics` (request duration). |
| **worker** | Heartbeat kèm `ram_mb` (footprint cây tiến trình — psutil) + `cpu_percent`. |
| **dashboard** (mới) | React + Vite: bảng station (status/load/RAM/CPU), tỷ lệ **LIVE/DEAD/INCONCLUSIVE tách biệt** (3 màu + BLOCKED), pool, circuit, job gần đây theo trace_id, alert. Nhận realtime qua SSE; **chỉ `import type`** từ contracts (không kéo runtime Node vào bundle). |
| **config** | orchestrator env: `CIRCUIT_*` (window/min_samples/threshold/cooldown), `QUEUE_METRICS_INTERVAL_MS`, `DASHBOARD_STREAM_INTERVAL_MS`, `DASHBOARD_RATIO_WINDOW_MINUTES`. |

### Kết quả đo thực tế (báo cáo 1–6)
| # | Test | Cách tái hiện | Kết quả |
|---|---|---|---|
| 1 | **Circuit breaker**: chuỗi BLOCKED vượt ngưỡng → API 503 + retry_after; platform khác vẫn 202; sau cooldown → nhận job lại | `vitest test/circuit-breaker.int.spec.ts` (5) + `python scripts/e2e_phase5.py` (1) | ✅ 503+retry_after=6s; FACEBOOK 202; hồi→202; int 5/5 |
| 2 | **API docs**: /docs mở; schema khớp DTO thực (sinh từ zod) | `e2e_phase5.py` (2): /docs 200, /docs/json chứa url/trace_id/retry_after_seconds/url_hash/INCONCLUSIVE | ✅ |
| 3 | **/metrics**: đủ metric bắt buộc; số khớp thực tế | `e2e_phase5.py` (3): 7 metric bắt buộc có mặt; `result_total{TIKTOK,LIVE}=5` khớp 5 job; api duration | ✅ |
| 4 | **Dashboard**: realtime; 3 tỷ lệ TÁCH BIỆT; tắt station → OFFLINE; không lộ cookie | `e2e_phase5.py` (4): snapshot+SSE, ratio đủ live/dead/inconclusive, kill worker→OFFLINE, snapshot không chứa cookie/ciphertext | ✅ |
| 5 | **Regression toàn hệ** | `pnpm test` + `pnpm test:golden` + e2e_phase1/3/4 | ✅ TS 47 test · golden 17 · worker 32 · e2e P1/P3/P4 xanh |
| 6 | lint/typecheck sạch | `pnpm typecheck` 13/13 · `pnpm lint` 7/7 · worker ruff+mypy | ✅ |

Gate toàn repo: `pnpm build` 8/8 · `pnpm typecheck` 13/13 · `pnpm lint` 7/7 · `pnpm test` (config 9, shared 6,
crypto 7, contracts 8, **orchestrator 17** gồm circuit 5 + recovery 4) · `pnpm test:golden` **17** · worker
`ruff` ✓ · `mypy` strict 19 files ✓ · `pytest` **32**.

### Ghi chú kỹ thuật
- **Circuit state ở Redis** (INV-5): key `cb:{platform}:{open_until,total,bad}`. Mất Redis → reset về đóng (bảo vệ ít hơn, không sai). Circuit ghi trên MỌI kết quả nên regression BLOCKED-nặng (Phase 3) phải xoá `cb:*` giữa kịch bản (đã cập nhật harness).
- **Docs sinh từ zod**: đổi field trong `packages/contracts` → rebuild → `/docs/json` đổi theo (một nguồn sự thật cho runtime + tài liệu).
- **Dashboard chỉ `import type`** từ contracts → bundle trình duyệt không dính pino/zod (Node). Realtime qua SSE (vite proxy khi dev; CORS GET cho bản build). KHÔNG endpoint nào trả cookie (INV-12).
- **ELK KHÔNG dựng** (đúng yêu cầu) — phân kỳ observability: pino JSON + prom-client `/metrics` là đủ cho giai đoạn này (tech-stack §observability).
- Xem `TEST_REPORT.md` cho tổng hợp Phase 0–5 + hạn chế + việc cần làm khi lên máy trạm thật.

## Phase 2 — Đủ 4 platform + login module + GemLogin THẬT ✅ HOÀN TẤT (2026-07-18)

Mục tiêu (roadmap Phase 2 + đóng §5.1–§5.4/§5.8 TEST_REPORT): detector đủ 4 platform, module kịch bản login
(cookie ×4, info TT&X), refresh cookie, stream tiến trình, và **chuyển worker sang GemLogin THẬT** (đã cài
5.0.8 trên máy) — kiểm chứng plumbing browser thật. Load test nâng lên **100 concurrent**.

### Thành phần đã dựng / sửa
| Vùng | Thay đổi |
|---|---|
| **worker/detectors** | Thêm `facebook.py` (post/profile/group/page), `twitter.py` (post/profile), `youtube.py` (video/channel) — kế thừa `BaseDetector`, chỉ khai báo `SignalSpec` (INV-8). Registry `get_detector` đủ 4. Sửa selector over-broad (`ytd-watch-flexy`) + bỏ tổ hợp con cháu (lệch fake↔real). Quote giá trị attr có dấu chấm (real CSS querySelector). |
| **worker/browser** | **`RealGemLoginAdapter` sửa khớp API GemLogin 5.0.8 THẬT** (`/api/profiles` + `create`/`update`/`start`/`close`, envelope `{success,message,data}`, free-tier delete→`GemLoginError`, tìm PID Chrome qua cổng CDP vì start không trả pid). **`DrissionPageSource`** + `DrissionPageView`: attach CDP GemLogin, nạp cookie TRƯỚC điều hướng (INV-2), bắt HTTP status qua network listen, query selector phòng thủ. |
| **worker/runner** | real mode: mở browser GemLogin → `DrissionPageSource` → detect → ĐÓNG (INV-6/INV-9). `on_progress` callback (§8) + thu `fresh_cookie` khi profile khoẻ (§4.4). |
| **worker/login** (mới) | `base.py` (LoginPage protocol, Credential, LoginResult, LoginOutcome), `cookie_login.py` (verify guard, tái dùng `SignalSpec`), `info_login.py` (TT&X: gõ char-by-char, captcha→BLOCKED, OTP→TOTP/OTP_REQUIRED), `forms.py` (LoginFormSpec/platform), `drission_page.py` (LoginPage thật, gõ mô phỏng người). Registry `get_login_strategy(platform, method)` — FB/YT info→`LoginError`. |
| **worker/ws_client** | Truyền adapter vào runner khi `GEMLOGIN_MODE=real`; gửi `gemlogin_profile_id` vào payload; phát `job_progress` (threadsafe từ thread pool) + `cookie_refresh` sau phiên OK. |
| **contracts** | Thêm `gemlogin_profile_id` vào `script.run`; message mới `cookie_refresh` + `job_progress` (Station→Server); `dashboard.dto` thêm `progress`. Regen JSON Schema + pydantic (parity giữ). |
| **orchestrator** | `dispatch.refreshCookie` (mã hoá cookie mới + lưu — INV-12); `dashboard.recordProgress` (ring buffer 50) + snapshot có `progress`; gateway thêm 2 case `cookie_refresh`/`job_progress`. `db.profile.updateCookie`. `dispatch` gửi `gemlogin_profile_id`. |
| **dashboard** | Panel "Tiến trình job đang chạy (stream)" theo trace_id. |
| **db/scripts** | `seed-proxies.ts` (proxy pool, `proxy_url_enc` mã hoá, gán sticky — INV-7). |
| **test/scripts** | `test_golden_platforms.py` (22 ca 3 platform), `test_login.py` (14 ca), `scripts/e2e_real_gemlogin.py` (E2E GemLogin thật), load test tham số hoá `LOADTEST_N`. `test:golden` gồm cả 4 platform. |

### Kết quả đo (báo cáo)
| # | Test | Kết quả |
|---|---|---|
| 1 | Golden 4 platform | `pnpm test:golden` **39** ✅ (golden bắt 2 lỗi selector thật khi dựng → đã sửa) |
| 2 | Login module | `pytest tests/test_login.py` **14** ✅ (cookie ×4, info TT&X, captcha/OTP/bad-cred, FB/YT info→LoginError) |
| 3 | **GemLogin THẬT** | `e2e_real_gemlogin.py`: mở browser thật 4 platform → attach DrissionPage → detect → chưa login = INCONCLUSIVE (KHÔNG DEAD, INV-1/2); activeBrowsers về 0 (không rò). Adapter khớp API 5.0.8. ✅ |
| 4 | **Load test 100 concurrent** | 100/100 hoàn tất cả 2 phase, KHÔNG crash; browser đồng thời=8=pool; queue depth đỉnh 60–68→0; p95 POST 268–294ms (<500ms); churn prefetch>pool=28 vs =pool=0 (INV-10). ✅ |
| 5 | Regression | `pnpm test` (orchestrator 17, contracts 8 gồm parity) ✅ · worker `pytest` **68** ✅ · `mypy` strict 28 files ✅ · `ruff`/`lint` ✅ · `e2e_phase1` (C/D/F) ✅ |

### Ghi chú kỹ thuật
- **Bản GemLogin FREE**: tối đa 5 profile, KHÔNG xoá được (adapter báo `GemLoginError` rõ ràng thay vì nuốt);
  start KHÔNG trả pid → tìm PID Chrome qua cổng remote-debugging (psutil) cho giám sát RAM (INV-9).
- **fake↔real đồng nhất selector**: HtmlPageView (fake) KHÔNG hỗ trợ tổ hợp con cháu → chỉ dùng selector đơn
  trong `SignalSpec`; giá trị attr có dấu chấm phải trích dẫn (real querySelector ném lỗi nếu để trần).
- **Chính xác 98% thật CHƯA đo** — cần cookie/tài khoản thật; golden fixtures là lưới an toàn. Xem
  `docs/huong-dan-test-thuc-te.md` để nạp cookie thật + chạy đo.
- **Login-by-info chưa auto-fallback trong luồng job**: cần gửi credential mã hoá xuống worker (như cookie) —
  chưa bật vì chưa có tài khoản (tránh lockout). Module đã sẵn sàng.

## Bề mặt điều khiển Station Management (theo phản hồi người dùng) ✅ HOÀN TẤT (2026-07-18)

Người dùng chỉ ra: mục 2 có "máy móc" (WS command + handler) nhưng **không vận hành được bằng tay** — dashboard
chỉ hiển thị. Đã bổ sung bề mặt điều khiển cho operator để test mục 2 THẬT (Swagger + Dashboard) với tài khoản thật.

### Thành phần đã dựng / sửa
| Vùng | Thay đổi |
|---|---|
| **contracts** | Lệnh WS mới `login.run` (method COOKIE/INFO + credential, INV-12) vào `commandPayloadSchema`; regen JSON Schema + pydantic (`LoginRunCommand`, parity giữ). `control.dto.ts` (REST DTO: station/profile view, create/update profile, browser action, run login, register account, command result). Export `CommandAckMessage`, `LoginMethodDto`. |
| **config** | `COMMAND_ACK_TIMEOUT_MS` (mặc định 60s — mở browser lần đầu tải Chromium chậm). |
| **db/profile.repo** | `listByStation` (đọc profile theo station), `registerAccount` (upsert dòng `profiles` AVAILABLE + cookie ĐÃ MÃ HOÁ — INV-12). |
| **orchestrator/control** | **`PendingCommandsService`** (tương quan command↔ack theo `command_id`, timeout, reject-per-station khi rớt). **`StationControlService`** (list/CRUD profile, browser open/close, `runLogin`, `registerAccount` — mã hoá cookie qua packages/crypto, giải mã cookie đã lưu để inject/login). **`StationControlController`** (REST validate zod + Swagger decorators). |
| **orchestrator/ws.gateway** | `command_ack` → `pending.resolve` (khớp REST đang chờ); station down → `pending.rejectStation`. |
| **orchestrator/main** | `@nestjs/swagger` (v7 khớp Nest10/Fastify4 + `@fastify/static@7`) phơi `/docs`; CORS mở POST/PATCH/DELETE cho dashboard bấm nút. |
| **api/main** | `@fastify/cors` cho dashboard gửi `POST /check` từ trình duyệt. |
| **worker/login** | `browser/cookies.parse_cookies` (một nguồn parse cookie — dùng chung page_source + login); `DrissionLoginPage.set_cookies`; **`login/execute.py`** (`execute_login`: real=mở browser GemLogin→attach→chạy strategy→đóng; fake=`_FakeLoginPage` tất định). |
| **worker/ws_client** | Xử lý `login.run` → `execute_login` (thread) → `command_ack` (ok=logged_in + outcome) + `cookie_refresh` khi OK. KHÔNG log credential (INV-12). |
| **dashboard** | Panel **"Điều khiển Station"**: chọn station/platform/gemId, nhập cookie/user/pass, nút Xem profile / Tạo profile / Mở-Tắt browser / Chạy login / Nạp tài khoản / Gửi check + hiện phản hồi. |

### Kết quả đo
| # | Test | Kết quả |
|---|---|---|
| 1 | login.run executor (fake) | `pytest tests/test_login_execute.py` **6** ✅ (cookie→LOGGED_IN, rỗng→COOKIE_DEAD, info TT/X→LOGGED_IN, FB/YT info→LoginError) |
| 2 | **Bề mặt điều khiển E2E** | `python scripts/e2e_control.py` **9/9** ✅ (list station · tạo profile ok+command_id · mở/tắt browser · login COOKIE→LOGGED_IN · login INFO YT→ok=false unsupported · nạp tài khoản AVAILABLE+has_cookie + cookie MÃ HOÁ ở DB · GET profiles không lộ cookie INV-12 · station rớt→REST 503 tức thì) |
| 3 | Swagger phơi | orchestrator `/docs` 200 (stations/profile CRUD/browser/login/accounts); api `/docs` 200 (check) |
| 4 | Regression | `pnpm typecheck` 13/13 · `pnpm lint` 7/7 · `pnpm build` 8/8 · `pnpm test` (orchestrator 17) · `test:golden` 39 · worker `pytest` **74** · mypy 30 files · `e2e_phase1` C/D/F ✅ |

## Cookie-first login guard (calibrate với FB thật) ✅ HOÀN TẤT (2026-07-19)

Khi test FACEBOOK thật (tài khoản VN, link `?locale=vi_VN`), guard đăng nhập trả **CHALLENGED sai** dù đã đăng
nhập → job INCONCLUSIVE → dead-letter. Chẩn đoán trực tiếp trên GemLogin (mở profile thật, soi DOM + cookie):
- Profile ĐÃ đăng nhập (`c_user`+`xs` cookie có, title `(10) Facebook`, không redirect login).
- Selector guard **tiếng Anh** (`[aria-label="Your profile"]`...) KHÔNG khớp DOM **tiếng Việt**; trang group
  (SPA) còn không render marker `role=banner/navigation` ổn định → guard DOM **không đáng tin cho FB**.

**Fix (INV-2/INV-8): guard ưu tiên COOKIE session** — locale-independent, chắc chắn.
- `SignalSpec.auth_cookies` (cookie cốt lõi mỗi platform): FB `(c_user, xs)`, TikTok `(sessionid,)`, X
  `(auth_token, ct0)`, YouTube `(SID, SAPISID)`. `verify_logged_in`: đủ (all) cookie → đã đăng nhập; không có
  → **fallback DOM** như cũ (golden/fake KHÔNG đổi — `HtmlPageView.cookie_names()` trả rỗng).
- `PageView.cookie_names()`: `DrissionPageView` đọc cookie thật (CHỈ tên, không giá trị — INV-12);
  `HtmlPageView` trả rỗng → guard fallback DOM. `_page_cookie_names` phòng thủ (test double thiếu method → rỗng).
- **Kiểm chứng trên FB group THẬT**: `logged_in=True` → **url_status=LIVE**, profile_health=OK (trước là
  INCONCLUSIVE+CHALLENGED).
- **Calibrate thêm target REEL** (test link reel thật → INCONCLUSIVE dù profile_health=OK vì detector chưa có
  selector reel): soi DOM reel thật → container `[data-pagelet^="Reels"]` + `div[data-video-id]` → thêm vào
  `live_selectors` FB → reel thật giờ **LIVE**. Thêm fixture golden `facebook/reel_live.html`.
- Golden **40** (+reel_live) + guard 4 (test_login_guard) + worker pytest **79** ✅, mypy 30 files ✅.
- **Calibrate TikTok trang PROFILE** (@user): detector cũ chỉ có `live_selectors` cho trang VIDEO → link profile
  → INCONCLUSIVE (no_decisive_signal) → retry nhiều lần (TikTok SPA render chậm). Soi DOM profile thật →
  thêm `[data-e2e=followers-count|follow-button|user-title|user-post-item]` → profile thật **LIVE ngay lần đầu**
  (hết retry). Thêm fixture golden `profile_live.html`. Golden **41**, worker pytest **80** ✅.
  - Ghi chú retry: retry chỉ xảy ra khi `url_status=INCONCLUSIVE` (target chưa xác định — có thể tạm thời:
    render chậm/chặn nhất thời), KHÔNG phải vì profile. LIVE/DEAD (chắc chắn) → KHÔNG retry. Sửa detector để
    LIVE ngay lần đầu là cách đúng để giảm retry (thay vì tắt retry — retry cứu được ca transient).
- **Fail-fast khi mở browser (chống "treo" khi spam nhiều link/1 profile)**: đẩy 2-3 link liên tiếp trên 1
  profile → GemLogin đóng/mở dồn dập kẹt "being opened" → adapter chờ tới **180s** → job block → trông như treo.
  Hạ ngưỡng `start_wait` **180→90s**, cấu hình qua env `BROWSER_START_WAIT_SECONDS` (wire config→create_adapter
  →RealGemLoginAdapter). Quá hạn → GemLoginError → INCONCLUSIVE → re-queue (self-heal) thay vì block 3 phút.
  Bài học vận hành: **đồng thời/platform = số profile đã login của platform** (1 job=1 profile=1 browser, INV-6);
  muốn check song song nhiều link → thêm profile; đừng spam 1 profile.
- **Self-heal khi GemLogin kẹt (cắt vòng hammer) — THROTTLED**: gốc rễ "treo" là mở/đóng dồn dập 1 profile →
  GemLogin kẹt "being opened" → mở không được. Trước đây worker báo `profile_health=OK` cho mọi lỗi hạ tầng →
  orchestrator giữ profile AVAILABLE → re-queue → **hammer vô hạn → treo**. Sửa:
  - **Worker** (`runner`): tách `GemLoginError` (mở browser lỗi) → `profile_health=THROTTLED` +
    `block_reason=browser_open_failed:...` (lỗi hạ tầng KHÁC sau khi đã mở vẫn OK/re-queue thường).
  - **Orchestrator** (`dispatch.autoSwitch`): `THROTTLED` → `profileRepo.cooldownProfile` (COOLDOWN NGẮN
    `PROFILE_THROTTLE_COOLDOWN_SECONDS`=30s, **KHÔNG** phạt health/**không** tăng consecutive_fails → không DEAD
    oan) → claimProfile bỏ qua → **ngừng hammer, GemLogin hồi → hết cooldown chạy lại (self-heal)**. THROTTLED
    cũng **loại khỏi circuit breaker** (lỗi phía ta, không phải platform chặn).
  - **Adapter**: hạ `start_wait` 180→**90s** (env `BROWSER_START_WAIT_SECONDS`) — fail-fast thay vì treo 3 phút;
    thêm **close-settle** 2s (env `BROWSER_CLOSE_SETTLE_SECONDS`) cho GemLogin kịp giải phóng trước khi mở lại
    (giảm churn "being opened" khi dùng lại cùng profile liên tiếp).
  - Test: `test_runner_throttle.py` (GemLoginError→THROTTLED) + `lease-health` cooldownProfile (COOLDOWN nhưng
    KHÔNG đụng health/fails). worker pytest **81** ✅, orchestrator **18** ✅, mypy 30 ✅, build 8/8 ✅.
  - Lưu ý: cooldown NGĂN hệ tự làm GemLogin kẹt cứng. Nếu GemLogin ĐÃ kẹt cứng (do thao tác tay dồn dập trước
    đó) → phải **restart app GemLogin** (reset state của nó — lỗi phía GemLogin, không sửa được từ code).
- **Fix cookie auto-refresh (spec §4.4) bị hỏng âm thầm**: `cookies_string()` (page_source + login/drission_page)
  gọi `page.cookies(as_dict=False)` — DrissionPage 4.x KHÔNG có tham số `as_dict` → luôn ném → except → trả ""
  → `fresh_cookie` luôn rỗng → **cookie KHÔNG BAO GIỜ được lưu vào DB** sau phiên OK (has_cookie mãi false).
  Sửa dùng `page.cookies()` (đã kiểm chứng thật: trả 8 cookie gồm c_user+xs). Giờ sau mỗi check OK, worker tự
  thu cookie sống → gửi `cookie_refresh` → orchestrator mã hoá & lưu `profiles.cookie_ciphertext` (INV-12).
- **Calibrate DEAD thật (chống LIVE giả) — soi DOM link không tồn tại**:
  - **YouTube LIVE GIẢ cho video không tồn tại**: `#movie_player`/`.html5-video-player` (khung player) hiện CẢ ở
    trang video-đã-gỡ (báo lỗi bên trong) → khớp → LIVE sai. Soi DOM thật (video sống dQw4w9WgXcQ vs id sai):
    video THẬT có tiêu đề `[itemprop="name"]`/`.slim-video-information-title`; không tồn tại có
    `.player-error-overlay` + text "this video is unavailable", KHÔNG có tiêu đề. → **bỏ selector khung player
    khỏi LIVE**, dùng **tiêu đề** làm LIVE + thêm `.player-error-overlay`/text làm DEAD → video không tồn tại =
    **DEAD**, video thật = LIVE. Cập nhật fixture `youtube/live.html` + thêm `youtube/video_dead.html`.
  - **FB reel/bài không tồn tại → INCONCLUSIVE (nên DEAD)**: FB tiếng Việt báo "**trang này hiện không hiển
    thị**" — detector chỉ có dead_text tiếng Anh → không khớp. Thêm dead_text tiếng Việt + fixture
    `facebook/reel_dead.html`. Giờ reel sai = **DEAD**.
  - Golden **43** (+video_dead +reel_dead), worker pytest **85** ✅. Nguyên tắc: DEAD = target KHÔNG tồn tại
    hiện tại (dù chưa từng tồn tại hay đã xoá) + có tín hiệu CHẮC CHẮN (404/"không hiển thị"/"unavailable");
    trang mơ hồ/redirect/login-wall = INCONCLUSIVE (không đoán, INV-1).
- **COOLDOWN kẹt vĩnh viễn — FIX**: `claimProfile` chỉ lấy `status='AVAILABLE'`; profile `COOLDOWN` (dù
  `cooldown_until` đã qua) không được trả về AVAILABLE → kẹt mãi. Thêm `reapExpiredCooldowns` (COOLDOWN +
  cooldown_until<now → AVAILABLE, KHÔNG reset consecutive_fails) wire vào cron `LeaseReaperService`. Test
  `lease-health` + orchestrator **19** ✅.
- **Chống nhồi bừa pool**: dedup `registerAccount` theo (gemlogin_profile_id, platform) — nạp lại cùng
  profile+platform → CẬP NHẬT, không tạo dòng trùng. (Trước: nạp nhiều lần / sai platform → pool đầy trùng.)
- **Verify khi nạp (mặc định bật)**: `POST /accounts` mở profile trên station + kiểm đã đăng nhập ĐÚNG platform
  (qua login.run) TRƯỚC khi nạp; sai → từ chối (không vào pool → không cooldown loạn). `verify=false` để bỏ.
- **`assigned_station_id` = station SỞ HỮU (cố định), KHÔNG xoá khi release**: trước đây release/success/failure/
  reap xoá `assigned_station_id=NULL` → profile "mất" khỏi station → "Xem profile" (lọc theo station) chỉ còn
  cái chưa từng chạy. Sửa: giữ persistent (lease tạm = `status`/`lease_expires_at`). "Xem profile" hiện đủ, ổn
  định sau mỗi check. (Đã kiểm: readers chỉ có listByStation + profile_sync; recovery dùng check_jobs — an toàn.)
  orchestrator **19** ✅ trên pool sạch.
- **TikTok video sai → LIVE giả (FIX)**: bare selector `video` khớp CẢ trang "Video hiện không khả dụng"
  (thẻ <video> shell) → LIVE giả (id video 20 chữ số vẫn LIVE). Bỏ `video` khỏi live (giữ `[data-e2e=video-
  detail/browse-video/video-player]` — chỉ video THẬT), thêm dead_text tiếng Việt "video hiện không khả dụng".
  Kiểm chứng thật: link sai giờ → **DEAD**. Golden **44** (+tiktok video_notfound).
- **SPA render-settle (chống INCONCLUSIVE oan / flaky → DLQ)**: `open_page` chụp `body_text` NGAY sau `load`
  event, nhưng FB/TikTok/YouTube render nội dung (kể cả chữ "video không khả dụng") bằng JS SAU load → chụp
  trắng → `no_decisive_signal` → retry→DLQ (và LIVE cũng flaky). Thêm **settle chờ render** trước khi chụp
  (`BROWSER_RENDER_SETTLE_SECONDS`, mặc định 3s) wire config→ws_client(payload)→run_check→DrissionPageSource.
  Kiểm chứng thật: link TikTok sai chạy full run_check → **DEAD ổn định 2/2** (trước flaky INCONCLUSIVE). Giảm
  retry cho mọi platform (SPA render kịp ngay lần đầu). worker pytest **86** ✅.
- **RELOAD LIÊN TỤC (gốc bug retry/DLQ) — FIX**: `load_mode.normal()` chờ sự kiện `load` HOÀN TẤT; SPA (TikTok/
  FB/YT) có kết nối bền (websocket/long-poll) → `load` lâu xong → `page.get` timeout → **TỰ RETRY = reload lại
  trang**, lặp liên tục → trang chưa render đã reload. Fix: **`load_mode.eager()`** (DOMContentLoaded, trả sớm)
  + **`page.get(retry=0)`** (điều hướng ĐÚNG 1 lần, không tự reload) + settle chờ render. Kiểm chứng thật:
  profile sai → DEAD (9.4s), profile thật → LIVE (13.1s), **navigate 1 lần, hết reload loop**. golden 45, pytest 87.
- **TikTok account-not-found text**: TikTok VN báo "Không **THỂ** tìm thấy tài khoản này" (dead_text cũ thiếu
  "thể" → không khớp). Dùng substring bền "tìm thấy tài khoản này" + fixture golden `account_notfound.html`.
- **TikTok anti-bot/rate-limit → BLOCKED**: khi check dồn dập 1 profile/IP, TikTok trả trang chặn TẠM THỜI
  "Page not available. Please try again later." (hoặc trang rỗng). Trước đây → INCONCLUSIVE+OK → retry-hammer→
  DLQ. Thêm block_texts "page not available / try again later / sorry about that / vui lòng thử lại sau" →
  `dom_block` → **BLOCKED** (cooldown + xoay proxy + circuit breaker; "try again later"=tạm thời nên KHÔNG DEAD).
  Fixture golden `rate_limited.html`. golden **46**, pytest **88**. LƯU Ý: đây là platform CHẶN thật — fix code
  chỉ phân loại đúng + ngừng hammer; giải pháp GỐC là vận hành (proxy residential, giảm tốc, profile warmed).
- **Đồng bộ XOÁ profile (profile_sync 2 chiều)**: trước đây `profile_sync` chỉ upsert (thêm/sửa), KHÔNG gỡ →
  xoá profile bên GemLogin thì DB giữ "profile ma" → dispatch vào → GemLoginError. Thêm: worker gửi
  `all_gemlogin_ids` (TẤT CẢ id GemLogin hiện có), orchestrator `pruneDeletedProfiles` → DELETE profile của
  station có `gemlogin_profile_id` KHÔNG còn trong danh sách (FK ON DELETE SET NULL → an toàn, giữ log). Chỉ
  prune khi client gửi `all_gemlogin_ids` (list THÀNH CÔNG — tránh wipe oan khi API hiccup). Pool tự khớp
  GemLogin sau mỗi vòng sync (register + định kỳ). Test `lease-health` pruneDeletedProfiles → orchestrator **20** ✅.
- **registerAccount làm sạch cooldown**: nạp lại profile → AVAILABLE + xoá cooldown/lease + reset fails (tránh
  AVAILABLE-mà-còn-cooldown-tương-lai → claim bỏ qua → job kẹt PENDING).

## Ghi chú kỹ thuật (bề mặt điều khiển)
- **@nestjs/swagger v8 KHÔNG dùng được với Nest 10** (SwaggerModule.setup nạp `@fastify/static@9` cần Fastify 5,
  còn NestFastify@10 chạy Fastify 4 → `FST_ERR_PLUGIN_VERSION_MISMATCH`). Hạ **v7 + pin `@fastify/static@7`**.
- **Tương quan command↔ack**: gửi WS (sync) rồi `waitFor(command_id)` NGAY (cùng tick, không await xen) → không
  race với ack đến sau round-trip. Timeout + reject-per-station để REST không treo khi station rớt.
- **Nhập tài khoản thật an toàn**: cookie/credential đi VÀO qua REST (WSS/TLS ở prod), mã hoá at-rest (packages/
  crypto), KHÔNG endpoint nào TRẢ ra, KHÔNG log (INV-12). `POST /accounts` không cookie = dùng session đăng nhập
  tay sẵn trong profile GemLogin.
