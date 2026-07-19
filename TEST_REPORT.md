# TEST_REPORT — FastCheck Automation (Phase 0 → 5)

> Tổng hợp kết quả kiểm thử toàn hệ + hạn chế còn lại + checklist khi lên máy trạm thật.
> Nhật ký chi tiết từng phase: `PROGRESS.md`. Luật bất biến: `docs/invariants.md`.

## 1. Hệ thống là gì

Dịch vụ kiểm tra trạng thái **LIVE / DEAD / INCONCLUSIVE** của link social (TikTok/Facebook/X/YouTube) ở
quy mô lớn: API nhận request → queue → orchestrator điều phối pool profile + station → worker (máy trạm
Windows + GemLogin + DrissionPage) chạy detector → trả kết quả. Triết lý bao trùm: **một lỗi báo ra tốt
hơn một lỗi âm thầm** — thà `INCONCLUSIVE`/`503` còn hơn trả sai.

Ba vùng: **api** (Fastify, stateless) · **orchestrator** (NestJS + WS gateway) · **worker** (Python 3.12 +
DrissionPage). Hạ tầng: Postgres (nguồn sự thật) · Redis (cache/lock/rate-limit/circuit) · RabbitMQ (queue).

## 2. Trạng thái theo phase

| Phase | Nội dung                                                                              | Trạng thái                             |
| ----- | -------------------------------------------------------------------------------------- | ---------------------------------------- |
| 0     | Khung xương: monorepo + hạ tầng + migration + WS register/heartbeat                | ✅                                       |
| 1     | Một đường sống end-to-end (TikTok) + detector 3 nhánh + guard + golden set       | ✅                                       |
| 2     | **4 platform đầy đủ (FB/X/YT) + login module (cookie ×4, info TT&X) + refresh cookie** | ✅ (xem §4.1) |
| 3     | Pool + auto-switch + rate-limit + đồng thời/phục hồi                              | ✅                                       |
| 4     | Station Management đầy đủ + phát hiện station chết + chịu tải (50→100 concurrent) | ✅                                       |
| 5     | Circuit breaker + API docs + observability (/metrics) + dashboard + stream tiến trình  | ✅                                       |
| —     | **GemLogin THẬT**: adapter khớp API thật + DrissionPage attach + E2E 4 platform thật   | ✅ (xem §4.2) |
| —     | **Bề mặt điều khiển** mục 2: Swagger :3002/docs + panel dashboard + login.run + accounts | ✅ (xem §4.3) |

## 3. Cách chạy kiểm thử

```bash
# Điều kiện: docker infra + build
docker compose up -d          # Postgres + Redis + RabbitMQ
pnpm install && pnpm build

# Gate tĩnh
pnpm typecheck                # tsc toàn repo (13 task)
pnpm lint                     # eslint + ruff (7 task)
pnpm test                     # unit/integration TS (Postgres/Redis THẬT từ .env)
pnpm test:golden              # golden set detector (accuracy 98% — chạy sau khi sửa detector)
pnpm worker:check             # ruff + mypy strict + pytest (worker Python)

# E2E (khởi động api+orchestrator+worker fake + fixture server, tự dọn)
python scripts/e2e_phase1.py           # đường sống + cache + trace_id (C/D/F)
python scripts/e2e_phase3.py           # auto-switch + DLQ + pool cạn (3a/3b/4)
python scripts/e2e_phase4.py           # station chết + reconnect + orchestrator restart (2a/2b/3)
python scripts/e2e_phase5.py           # circuit breaker + /docs + /metrics + dashboard (1/2/3/4)
python scripts/e2e_control.py          # BỀ MẶT ĐIỀU KHIỂN mục 2: stations/profile CRUD/browser/login/accounts (9)
LOADTEST_N=100 uv --directory apps/worker run python scripts/e2e_phase4_loadtest.py   # 100 concurrent load test

# Quan sát + ĐIỀU KHIỂN thủ công (khi services chạy)
#   API docs (check)     : http://127.0.0.1:3001/docs
#   Orch docs (điều khiển): http://127.0.0.1:3002/docs   ← stations/profile CRUD/browser/login/accounts
#   API metrics          : http://127.0.0.1:3001/metrics
#   Orch metrics         : http://127.0.0.1:3002/metrics
#   Dashboard data       : http://127.0.0.1:3002/dashboard/snapshot  (SSE: /dashboard/stream)
#   Dashboard UI + nút   : pnpm --filter @fastcheck/dashboard dev  → http://localhost:5173
```

## 4. Kết quả đo (gate + E2E)

### 4.1. Phase 2 — đủ 4 platform + login module (mới)

- **Detector 3 platform mới**: `facebook.py`, `twitter.py`, `youtube.py` (kế thừa `base.BaseDetector`, chỉ khai
  báo `SignalSpec` — KHÔNG copy-paste logic, INV-8). Registry `get_detector` đủ 4 platform.
- **Golden set mở rộng** (`tests/test_golden_platforms.py`, 22 ca): FB post/profile/group/page, X post/profile,
  YT video/channel — mỗi platform đủ live→LIVE, dead_404/soft404→DEAD, login_wall→INCONCLUSIVE+CHALLENGED,
  captcha→INCONCLUSIVE+BLOCKED, missing_selector→INCONCLUSIVE. Golden BẮT được 2 lỗi thật khi dựng (selector
  over-broad `ytd-watch-flexy` + tổ hợp con cháu `[primaryColumn] article` gây lệch fake↔real) → đã sửa.
- **Login module** (`fastcheck_worker/login/`, `tests/test_login.py` 14 ca): interface `login(page, credential)
  → LoginResult`; **login-by-cookie cho CẢ 4 platform** (verify guard, tái dùng `SignalSpec.login_selectors`);
  **login-by-info cho TikTok & X** (gõ mô phỏng người char-by-char + delay, captcha→BLOCKED, OTP→tự sinh TOTP
  hoặc OTP_REQUIRED, sai creds→BAD_CREDENTIAL); FB/YT yêu cầu info → `LoginError` (đúng phạm vi Excel).
- **Refresh cookie** (spec §4.4): worker thu cookie mới sau phiên OK → `cookie_refresh` (WSS) → orchestrator
  **mã hoá** (packages/crypto) + lưu `profiles.cookie_ciphertext` (worker KHÔNG tự mã hoá — ADR-0006, INV-12).
- **Stream tiến trình** (§8): worker phát `job_progress` (OPEN_BROWSER→LOGIN→DETECT→DONE) → dashboard hiển thị.

### 4.2. GemLogin THẬT — đã kiểm chứng trực tiếp trên máy (mới)

Bản đã cài: **GemLogin 5.0.8 (Electron, Free, tối đa 5 profile, Lifetime)**, API local `http://127.0.0.1:1010`.

- **`RealGemLoginAdapter` đã sửa cho khớp API THẬT** (đường dẫn cũ `/api/v3/...` là SAI — đã thay):
  `GET /api/profiles` (list), `POST /api/profiles/create`, `POST /api/profiles/update/{id}`,
  `GET /api/profiles/start/{id}` (→ `data.remote_debugging_address`), `GET /api/profiles/close/{id}`,
  `GET /api/status`. Envelope `{success,message,data}`; `success:false` → `GemLoginError` (không nuốt).
- **Ràng buộc bản FREE đã xử lý tường minh**: `delete` trả `"The free version does not work this feature"` →
  `GemLoginError` (báo ra, không im lặng); start KHÔNG trả pid → tự tìm PID Chrome qua cổng CDP (psutil, INV-9);
  lần mở đầu tải Chromium (chậm) → poll tới khi có `remote_debugging_address`.
- **E2E thật** (`apps/worker/scripts/e2e_real_gemlogin.py`) — mở browser THẬT 4 platform tuần tự:
  | Platform | Mở browser→attach DrissionPage→điều hướng site thật→detect | Kết quả (chưa có cookie) | activeBrowsers sau đóng |
  |---|---|---|---|
  | TikTok | ✅ (16.7s, lần đầu tải Chromium) | INCONCLUSIVE + CHALLENGED | 0 |
  | Facebook | ✅ (6.0s) | INCONCLUSIVE + CHALLENGED | 0 |
  | X/Twitter | ✅ (7.6s) | INCONCLUSIVE + CHALLENGED | 0 |
  | YouTube | ✅ (7.1s) | INCONCLUSIVE + OK (no_decisive_signal) | 0 |
  Chưa đăng nhập → **KHÔNG BAO GIỜ DEAD/LIVE** (đúng INV-1/INV-2 — guard chặn trước); browser **đóng sạch,
  0 tiến trình sót** (INV-9). Đã kiểm chứng DrissionPage `set_address` attach CDP GemLogin đọc được title/URL/DOM.
- **Đường thật đã nối vào pipeline**: `GEMLOGIN_MODE=real` → runner mở browser GemLogin + `DrissionPageSource`
  (nạp cookie TRƯỚC điều hướng — INV-2, bắt HTTP status qua network listen) thay `FakePageSource`; orchestrator
  gửi `gemlogin_profile_id` xuống để mở đúng browser.

> **Đã validate LIVE thật cho FACEBOOK** (tài khoản VN đăng nhập trong GemLogin): check group FB thật →
> **url_status=LIVE, profile_health=OK**. Phát hiện & sửa lỗi guard: selector DOM tiếng Anh KHÔNG khớp FB tiếng
> Việt/SPA → chuyển sang **guard bằng cookie session** (`auth_cookies`: c_user/xs...) — locale-independent, chắc
> chắn (xem PROGRESS "Cookie-first login guard"). Các platform khác: có tài khoản thật thì làm tương tự để đo
> đủ 98% (`docs/huong-dan-test-thuc-te.md`).

### 4.3. Bề mặt điều khiển Station Management (Swagger + Dashboard) — MỚI

Trước đây mục 2 chỉ có "máy móc" (WS command + handler) nhưng **không vận hành được bằng tay** — dashboard chỉ
xem. Đã bổ sung **bề mặt điều khiển cho operator**:

- **Swagger orchestrator** `http://127.0.0.1:3002/docs` (@nestjs/swagger): `GET /stations`,
  `GET|POST /stations/{id}/profiles`, `PATCH|DELETE .../{gemId}`, `POST .../browser/open|close`,
  `POST .../login`, `POST /accounts`. API service cũng có `/docs` (POST /check) + CORS cho dashboard.
- **Tương quan command↔ack** (`PendingCommandsService`): REST gửi lệnh xuống station qua WSS rồi **CHỜ
  `command_ack`** (khoá theo `command_id` — INV-14, timeout `COMMAND_ACK_TIMEOUT_MS`) mới trả HTTP → operator
  thấy kết quả thật của lệnh. Station rớt → giải phóng REST đang chờ của RIÊNG station đó (không treo).
- **Lệnh `login.run`** (mới): Server GỌI Client chạy kịch bản login (cookie ×4 / info TT&X) — kịch bản lưu phía
  client; real mode chạy trên browser GemLogin thật, fake mode chạy trên `_FakeLoginPage` tất định.
- **`POST /accounts`**: nạp TÀI KHOẢN THẬT (cookie mã hoá at-rest qua packages/crypto) → dòng `profiles`
  AVAILABLE để `POST /check` dùng ngay. Nhập cookie/credential vào từ giao diện, KHÔNG endpoint nào TRẢ ra (INV-12).
- **Dashboard** thêm panel **"Điều khiển Station"**: bấm nút Tạo profile / Mở-Tắt browser / Chạy login / Nạp
  tài khoản / Gửi check + xem phản hồi (ok/detail) realtime.

**E2E `scripts/e2e_control.py` — 9/9 PASS** (orchestrator + worker fake): list station · tạo profile
(ok+command_id+profile_id) · mở/tắt browser (ok) · login COOKIE→LOGGED_IN · login INFO YouTube→ok=false
"unsupported" (không đoán) · nạp tài khoản→AVAILABLE+has_cookie + **cookie lưu DB đã mã hoá (không plaintext)**
· GET profiles không lộ cookie/ciphertext (INV-12) · station rớt→REST trả 503 tức thì (không treo).

### 4.4. Gate + E2E cũ (Phase 0–5)

**Gate tĩnh** — tất cả xanh (cập nhật sau Phase 2 + GemLogin thật + bề mặt điều khiển):

- `pnpm build` 8/8 · `pnpm typecheck` 13/13 · `pnpm lint` 7/7.
- `pnpm test` (TS): shared 6 · config 9 · crypto 7 · contracts 8 (gồm parity zod↔pydantic) · **orchestrator 17**
  (claim/lease/rate-limit + recovery 4 + circuit 5, chạy Postgres/Redis THẬT).
- `pnpm test` (TS): shared 6 · config 9 · crypto 7 · contracts 8 · **orchestrator 17** (real Postgres/Redis).
- `pnpm test:golden` **39** (vote engine + golden **4 platform** TikTok/FB/X/YT: live→LIVE, dead_404/soft404→
  DEAD, login_wall→INCONCLUSIVE+CHALLENGED, captcha→INCONCLUSIVE+BLOCKED, missing_selector→INCONCLUSIVE).
- worker `ruff` ✓ · `mypy` strict **30 files** ✓ · `pytest` **74** (detector 4 platform + login module 14 +
  login.run executor 6 + hygiene/adapter/idempotency/cdp/concurrency).

**E2E** — tất cả PASS:

- **P1** (C/D/F): POST→PENDING→RUNNING→DONE; `check_logs` 1 dòng `LIVE|OK`; cache hit <500ms; `trace_id`
  xuyên suốt api→orchestrator→worker→check_logs.
- **P3** (3a/3b/4): flaky BLOCKED→LIVE (auto-switch, đổi profile); captcha luôn BLOCKED→DLQ (retry dừng ở max);
  pool cạn → job chờ (không sai) → phục hồi 1 profile → DONE.
- **P4** (2a/2b/3): kill worker → thu hồi job RUNNING (PENDING + clear cột dispatch + trả profile); orchestrator
  restart → startup sweep thu hồi qua cột dispatch; bounce orchestrator → worker tự reconnect + job hoàn tất.
- **P5** (1/2/3/4): chuỗi BLOCKED → API 503+retry_after (per-platform, FACEBOOK vẫn 202) → cooldown → 202;
  /docs + /docs/json khớp DTO; /metrics đủ 7 metric bắt buộc, `result_total{TIKTOK,LIVE}` khớp; dashboard
  snapshot+SSE 3 tỷ lệ TÁCH BIỆT, kill station → OFFLINE, không lộ cookie.

**Load test** (`LOADTEST_N=100` concurrent — kịch bản 70–100 người dùng, fake, pool=8, 2 phase): **100/100
hoàn tất cả hai phase, KHÔNG crash** (health 200 sau tải); browser đồng thời tối đa = **8 = pool** (INV-10);
**queue depth đỉnh 60–68 → rút về 0** (backpressure: tải vượt công suất thì XẾP HÀNG rồi tiêu hết); p95 POST
**268–294ms** (<500ms KPI); RAM đỉnh worker ~77MB; prefetch>pool churn 28 vs prefetch=pool **0** (INV-10:
prefetch phải = tổng max_concurrency để không requeue lãng phí). → **50 concurrent là sàn; 100 vẫn ổn định.**

**Bất biến chính xác (KPI 98%)**: `UrlStatus.DEAD` chỉ trả từ MỘT chỗ (`detectors/base.py` vote engine, nhánh
có phiếu dead); mọi exception/timeout/guard-fail → INCONCLUSIVE, KHÔNG bao giờ DEAD (INV-1). url_status luôn
tách profile_health (INV-3).

## 5. Hạn chế còn lại (đã biết, có chủ đích)

**Đã GIẢI QUYẾT trong đợt này** (trước đây là §5.1–§5.4, §5.8):
- ✅ **Phase 2**: đủ detector 4 platform (FB/X/YT + TikTok) + login module (cookie ×4, info TT&X, captcha/OTP,
  refresh cookie) — xem §4.1. Golden set mở rộng 39 ca.
- ✅ **RealGemLoginAdapter đã sửa + TEST THẬT** với GemLogin 5.0.8 trên máy — xem §4.2 (đường API đã đúng, đã
  attach DrissionPage vào browser thật, E2E 4 platform).
- ✅ **Dashboard stream bước chi tiết** (OPEN_BROWSER→LOGIN→DETECT→DONE) — đã có contract + worker phát + panel.
- ✅ **Proxy pool có script seed** (`seed-proxies.ts`, mã hoá `proxy_url_enc`, gán sticky) — cần proxy THẬT để
  bơm (đọc từ env `FASTCHECK_SEED_PROXIES`, không commit).

**Còn lại (có chủ đích / chờ điều kiện):**
1. **Chưa đo được chính xác 98% trên nền tảng THẬT** — cần cookie/tài khoản thật (guard chặn trước khi có login
   là ĐÚNG thiết kế). Golden fixtures là lưới an toàn hiện tại. Khi có cookie: seed vào `profiles` → chạy lại
   E2E thật (`docs/huong-dan-test-thuc-te.md`). Bảng tín hiệu detector là ĐIỂM KHỞI ĐẦU — health-check định kỳ.
2. **Login-by-info: GỌI TAY được, chưa AUTO-fallback trong luồng job**: đã có lệnh `login.run` (Swagger
   `POST /stations/{id}/login` method=INFO) để operator gọi station chạy login bằng username/password (captcha/
   OTP/TOTP) — xem §4.3. Còn lại: TỰ ĐỘNG chuyển sang info khi cookie chết giữa job cần gửi credential đã mã hoá
   kèm RUN — chưa bật vì chưa có tài khoản thật (tránh lockout). Module + đường gọi đã sẵn sàng.
3. **FakeGemLoginAdapter / FakePageSource** vẫn dùng cho test tất định (CI không có GemLogin). Đường thật
   (`RealGemLoginAdapter` + `DrissionPageSource`) đã hoạt động khi `GEMLOGIN_MODE=real`.
4. **Ghi log tập trung (ELK/Loki)** — **để sau** (đúng yêu cầu người dùng). Hiện có pino JSON + `/metrics`
   Prometheus. Lưu lịch sử check vào DB (`check_logs`, url_status ⊥ profile_health) đã đủ để tra cứu.
5. **CDP forwarding**: chính sách an toàn (mặc định local; bật cần token; thiếu token → fail-fast) + test;
   **tunnel WSS thật** thuộc hạ tầng triển khai (devops đưa lên server).
6. **Circuit/cache/rate-limit ở Redis** (`allkeys-lru`): mất Redis → chậm lại/bảo vệ ít hơn, KHÔNG sai (INV-5).
7. **Hạ tầng khi lên server (devops lo qua Docker)**: PgBouncer, cron partition `check_logs` theo tháng,
   Prometheus/Grafana, tunnel WSS. Không chặn chức năng — đóng gói Docker là được.

## 6. Checklist khi lên MÁY TRẠM THẬT (production)

### 6.1. Chuyển worker sang GemLogin thật (ĐÃ KIỂM CHỨNG với 5.0.8)

- Đặt **`GEMLOGIN_MODE=real`** trong `.env` của máy trạm (Windows, native — KHÔNG Docker).
- Đặt **`GEMLOGIN_API_URL=http://127.0.0.1:1010`** (mặc định GemLogin Electron).
- **`RealGemLoginAdapter` đã khớp API THẬT** (không cần chỉnh nữa cho 5.0.8): `GET /api/profiles`,
  `POST /api/profiles/create`, `POST /api/profiles/update/{id}`, `GET /api/profiles/start/{id}`
  (→ `data.remote_debugging_address`), `GET /api/profiles/close/{id}`, `GET /api/status`. DrissionPage attach
  qua `ChromiumOptions().set_address(addr)` — đã chạy thật. Bản FREE: KHÔNG xoá được profile + tối đa 5.
- Cài Python deps trên máy trạm: `uv sync` (đã gồm `DrissionPage`, `psutil`). Chrome/Chromium do GemLogin quản.
- Kiểm nhanh: `uv --directory apps/worker run python scripts/e2e_real_gemlogin.py` (mở browser thật 4 platform).

### 6.2. Điền credential / cookie / proxy

- **Cookie**: sinh khoá mã hoá AES-256-GCM → `COOKIE_ENC_KEY` (base64 32 byte) + `COOKIE_KEY_ID`. Xoay khoá
  qua `COOKIE_ENC_KEYS` (JSON keyId→base64). Cookie lưu **mã hoá** ở `profiles.cookie_ciphertext` (INV-12);
  orchestrator giải mã (`packages/crypto`) rồi gửi xuống worker qua WSS. **Không commit cookie/.env.**
  - Seed profile + cookie: theo mẫu `apps/orchestrator/src/scripts/seed-tiktok-profile.ts`.
- **Proxy**: seed bảng `proxies` (residential/mobile sticky, khớp geo — INV-7); `proxy_url_enc` mã hoá
  credential proxy. Gán `profiles.proxy_id`. Xoay proxy chỉ ở tầng "cấp IP mới cho phiên sau".
- **Login-by-info** (khi có Phase 2): điền `account_label` + credential an toàn (không lưu mật khẩu thô).

### 6.3. Bảo mật kênh

- **WSS + token** thật cho station↔orchestrator: đặt `WS_AUTH_TOKEN` là chuỗi ngẫu nhiên dài; chạy
  orchestrator sau TLS (reverse proxy/tunnel). Worker đặt `ORCHESTRATOR_WS_URL=wss://...`.
- **CDP forward**: giữ mặc định `CDP_FORWARD_ENABLED=false` (chạy login local). Chỉ bật khi cần server điều
  khiển trực tiếp, kèm `CDP_FORWARD_TOKEN` + giữ trong mạng nội bộ/tunnel (INV-12).

### 6.4. Năng lực & backpressure (INV-10)

- Đặt **`WORKER_MAX_CONCURRENCY`** theo RAM máy: `≈ (RAM_khả_dụng − RAM_OS)/~500MB` mỗi Chromium (16GB →
  ~18–20). Đặt **`ORCHESTRATOR_PREFETCH` = tổng max_concurrency** các station (bằng nhau — tránh churn).
- Tổng `max_concurrency` các station ONLINE ≥ tải mục tiêu (≥50). Mở rộng ngang = thêm máy trạm (không nhồi tab).
- `BROWSER_RAM_LIMIT_MB` + `RESOURCE_MONITOR_INTERVAL_SECONDS`: giám sát RAM/PID, vượt ngưỡng → kill cây + giải phóng.

### 6.5. Vận hành & quan sát

- **Circuit breaker**: chỉnh `CIRCUIT_WINDOW_SECONDS/MIN_SAMPLES/BLOCK_THRESHOLD/COOLDOWN_SECONDS` theo thực tế
  ban. Alert khi circuit mở (log `alert:true` + gauge `fastcheck_circuit_open`).
- **Prometheus**: scrape `GET /metrics` của api (`:3001`) + orchestrator (`:3002`). Metric bắt buộc đã phơi:
  result LIVE/DEAD/INCONCLUSIVE theo platform, BLOCKED, p95 latency, queue depth, profiles theo status, proxy
  fail_count, RAM/CPU worker, circuit open. Dựng Grafana + alert (INCONCLUSIVE/BLOCKED tăng đột biến, RAM worker
  vượt ngưỡng, pool thấp, DLQ có job, circuit mở).
- **Dashboard**: `pnpm --filter @fastcheck/dashboard build` → host tĩnh; đặt `VITE_ORCH_URL` trỏ orchestrator
  (hoặc reverse-proxy `/dashboard` + `/metrics`).
- **DB**: gắn PgBouncer (transaction mode); thêm cron tạo partition `check_logs` theo tháng; backup định kỳ.
- **Golden set**: chạy `pnpm test:golden` định kỳ (health-check detector) — nền tảng đổi cơ chế thì golden vỡ
  TRƯỚC khi phá KPI 98%.

### 6.6. Pháp lý & phạm vi (spec §7)

Giới hạn đúng mục đích được duyệt: **kiểm tra trạng thái link**. Không mở rộng sang thu thập dữ liệu người
dùng. Tự động hoá + bypass anti-bot có thể vi phạm ToS — thống nhất phạm vi với quản lý/pháp chế.

---

*Cập nhật: 2026-07-18 (Phase 2 đủ 4 platform + login module + GemLogin THẬT + load test 100 concurrent +
**bề mặt điều khiển Station Management** qua Swagger/Dashboard + login.run + accounts). Hướng dẫn chạy & test
thủ công (Swagger/Dashboard/GemLogin thật): `docs/huong-dan-test-thuc-te.md`. Chi tiết từng phase: `PROGRESS.md`.*
