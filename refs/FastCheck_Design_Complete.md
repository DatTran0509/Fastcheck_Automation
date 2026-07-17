# FastCheck Automation — Tài liệu thiết kế hoàn chỉnh

Dịch vụ kiểm tra trạng thái **Live/Dead** của link social (TikTok, Facebook, X, YouTube) ở quy mô lớn, có auto-login bằng cookie/info, auto-switch profile khi bị chặn, chạy multi-profile song song và điều phối qua Station Management.

> Đây là bản gộp: hợp nhất tài liệu thiết kế hệ thống, thiết kế chi tiết Station Management (Hạng mục 2), cấu trúc source, và đặc tả từng tính năng (mô tả · yêu cầu · hướng dẫn · vị trí code · nghiệm thu) thành một tài liệu duy nhất cho toàn dự án.

## Mục lục
1. Mục tiêu & triết lý
2. Kiến trúc tổng thể
3. Mô hình dữ liệu (PostgreSQL)
4. Ba vấn đề cốt lõi
5. Cấu trúc source & tổ chức thư mục
6. Đặc tả từng tính năng (feature cards)
7. Vòng đời một job (trace_id)
8. Thiết kế vận hành công nghệ nền tảng
9. Rủi ro & vận hành
10. Bổ sung kỹ thuật chống "hỏng âm thầm"
11. Lộ trình & luật bất biến
12. Phụ lục: ánh xạ yêu cầu Excel

---

## 1. Mục tiêu & triết lý

**KPI định hình mọi quyết định kỹ thuật:**
- Độ chính xác phân loại LIVE/DEAD **≥ 98%**.
- API trả **< 500ms** khi cache hit; check thật **< 3 phút**/mục tiêu.
- Chịu tải **≥ 50 request đồng thời** không crash.

**Triết lý bao trùm:** *một lỗi được báo ra luôn tốt hơn một lỗi âm thầm.* Hệ thống thà trả `INCONCLUSIVE` hoặc từ chối còn hơn trả sai mà không ai biết. Toàn bộ thiết kế xoay quanh việc biến "hỏng âm thầm" thành "hỏng có báo động".

---

## 2. Kiến trúc tổng thể

Hệ thống chia 3 vùng: **vùng tiếp nhận** (stateless, mở rộng ngang), **vùng điều phối & dữ liệu** (nguồn sự thật), và **vùng thực thi** (các máy trạm chạy browser).

| Tầng | Công nghệ | Vai trò | Vấn đề nó giải quyết |
|---|---|---|---|
| FastCheck API | Node.js + Fastify | Nhận `POST /check`, validate, chuẩn hoá URL, tra cache, dedupe, đẩy job | Trả `< 500ms` khi cache hit; API mỏng nên chịu burst tốt |
| Cache / Lock | Redis (ioredis) | Cache kết quả theo `url_hash`, khoá chống stampede, rate-limit theo platform | Không check lại URL vừa check; chống 100 job trùng; chống bắn quá nhanh |
| Message Queue | RabbitMQ (amqplib) | Hàng đợi job, retry có backoff, Dead Letter Queue | Tách đồng bộ, hấp thụ tải đỉnh, không mất job |
| Orchestrator | NestJS + WebSocket Gateway | Điều phối job xuống station theo slot, quản lý pool profile, auto-switch | Cân tải, chống cấp quá năng lực máy, xử lý block |
| Data store | PostgreSQL | `profiles`, `check_jobs`, `check_logs`, `stations`, `proxies` | Nguồn sự thật vòng đời job & pool; claim profile an toàn |
| Observability | pino → Prometheus/Grafana (ELK khi cần) | Log theo `trace_id`, metric, cảnh báo | Truy vết 1 job xuyên hệ thống; phát hiện detector vỡ sớm |
| Client App (máy trạm) | Python (WS client, websockets) | Nghe lệnh, inject cookie, mở/tắt browser, quản lý PID, forward CDP | Chạy script sát browser (nhanh, ổn định); chống treo |
| Antidetect browser | GemLogin | Mỗi profile 1 vân tay + 1 proxy, đa luồng độc lập | Chống trùng vân tay → chống ban hàng loạt |
| Automation | DrissionPage (attach CDP endpoint GemLogin) | Điều khiển DOM, đọc tín hiệu, phân loại LIVE/DEAD/INCONCLUSIVE | Xác định trạng thái chính xác `≥ 98%` |

**Nguyên tắc kiến trúc:**
- API **stateless** → nhân bản nhiều instance sau load balancer.
- **RabbitMQ chỉ là kênh vận chuyển**, không phải database. Trạng thái vòng đời job nằm ở bảng `check_jobs`.
- Server gửi **lệnh cấp cao** (`RUN script X với cookie Y`) cho máy trạm; máy trạm tự chạy DrissionPage *local*. Forward CDP có nhưng phải qua WSS+token, không để trần ra internet.
- Mọi thao tác đi kèm `trace_id` từ đầu tới cuối để truy vết.

```
Client ──POST /check──> FastCheck API (stateless) ──> Redis (cache/lock/rate-limit/registry)
                              │ push job
                              ▼
                        RabbitMQ (job.pending / job.retry / job.dlq)
                              │ consume (manual ack)
                              ▼
                        Orchestrator (NestJS + WS) ──> PostgreSQL (nguồn sự thật)
                              │ WS RUN {url, cookie, command_id}   └─> Observability
                              ▼
     Worker node ── 1 profile · 1 vân tay · 1 proxy · 1 context ──┐
     Python worker → GemLogin (antidetect) → DrissionPage (attach CDP)
                              ▼
     Target: TikTok · Facebook · X · YouTube (anti-bot)
```

---

## 3. Mô hình dữ liệu (PostgreSQL)

### 3.1. `stations` — máy trạm
```
id              UUID PK
name            VARCHAR(100)
mac_address     VARCHAR(255)
ip_address      INET
status          ENUM(ONLINE, OFFLINE, DRAINING)   -- DRAINING = đang gỡ, không nhận job mới
max_concurrency INT        -- số browser tối đa máy chạy nổi (tính theo RAM)
current_load    INT        -- job đang chạy
agent_version   VARCHAR(50)
last_ping_at    TIMESTAMPTZ
```

### 3.2. `proxies` — tách riêng khỏi profile
```
id            UUID PK
proxy_url_enc BYTEA        -- credential mã hoá
type          ENUM(RESIDENTIAL, MOBILE, DATACENTER)
region        VARCHAR(50)  -- để khớp timezone/locale khi mở browser
status        ENUM(ACTIVE, BANNED, COOLDOWN)
fail_count    INT
```

### 3.3. `profiles` — pool tài khoản/cookie
```
id                  UUID PK
platform            ENUM(TIKTOK, FACEBOOK, TWITTER, YOUTUBE)
account_label       VARCHAR(100)     -- nhãn nội bộ; KHÔNG lưu mật khẩu thô
cookie_ciphertext   BYTEA            -- cookie mã hoá AES-GCM
cookie_key_id       VARCHAR(50)      -- id khoá (hỗ trợ xoay khoá)
proxy_id            UUID FK -> proxies
assigned_station_id UUID FK -> stations
status              ENUM(AVAILABLE, IN_USE, COOLDOWN, DEAD, BLOCKED)
health_score        SMALLINT         -- 0-100, giảm khi gặp challenge
lease_expires_at    TIMESTAMPTZ      -- hạn "thuê" profile; quá hạn tự trả về pool
cooldown_until      TIMESTAMPTZ      -- nghỉ tạm khi bị nghi ngờ
consecutive_fails   SMALLINT         -- fail liên tiếp -> chuyển DEAD
last_used_at        TIMESTAMPTZ
```
Index claim: `(platform, status, cooldown_until) WHERE status = 'AVAILABLE'`.

### 3.4. `check_jobs` — nguồn sự thật vòng đời job
```
id                  UUID PK
trace_id            UUID
target_url          TEXT
url_hash            VARCHAR(64)   -- sha256(URL đã chuẩn hoá); key cache + dedupe
platform            ENUM(...)
status              ENUM(PENDING, RUNNING, DONE, FAILED, DEAD_LETTER)
retry_count         SMALLINT
result              ENUM(LIVE, DEAD, INCONCLUSIVE) NULL
assigned_station_id UUID NULL FK -> stations   -- set khi RUNNING; thu hồi khi station chết
assigned_profile_id UUID NULL FK -> profiles   -- profile đang chạy job
dispatched_at       TIMESTAMPTZ NULL           -- lúc cấp job xuống station
created_at          TIMESTAMPTZ
finished_at         TIMESTAMPTZ
```
Chống job trùng: `UNIQUE(url_hash) WHERE status IN ('PENDING','RUNNING')`. Index `(status, assigned_station_id)` để thu hồi nhanh job của station chết.

> Ba cột `assigned_station_id/assigned_profile_id/dispatched_at` là bổ sung so với ERD ban đầu, để station chết còn biết *job nào đang ở đâu* mà thu hồi (mục 6.8, luật INV-15).

### 3.5. `check_logs` — lịch sử (append-only, partition theo tháng)
```
id                BIGINT identity
trace_id          UUID
job_id            UUID FK -> check_jobs
profile_id        UUID FK -> profiles
target_url        TEXT
url_status        ENUM(LIVE, DEAD, INCONCLUSIVE)           -- kết quả TARGET
profile_health    ENUM(OK, CHALLENGED, BLOCKED, THROTTLED) -- sức khoẻ PROFILE lúc check
block_reason      TEXT NULL
response_time_ms  INT
checked_at        TIMESTAMPTZ
```
`PARTITION BY RANGE (checked_at)`; index `(profile_id, checked_at)`, `(trace_id)`.

> **Tách `url_status` khỏi `profile_health` là điểm cốt tử.** Nó cho phép phân biệt "link chết thật" với "profile mình bị chặn nên tưởng link chết" — nền tảng của độ chính xác 98%. `check_jobs` giữ trạng thái hiện tại; `check_logs` giữ mọi lần thử (một job switch profile 3 lần → 1 dòng job, 3 dòng log).

### 3.6. Claim profile an toàn (atomic, song song)
```sql
UPDATE profiles
SET status = 'IN_USE',
    lease_expires_at = now() + interval '5 minutes',
    assigned_station_id = :station_id
WHERE id = (
  SELECT id FROM profiles
  WHERE platform = :platform
    AND status = 'AVAILABLE'
    AND (cooldown_until IS NULL OR cooldown_until < now())
  ORDER BY health_score DESC, last_used_at ASC
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
RETURNING *;
```
`SKIP LOCKED` cho 50 worker cùng lấy profile mà không dẫm chân nhau; `lease_expires_at` chống kẹt `IN_USE` khi worker treo (cron dọn định kỳ).

---

## 4. Ba vấn đề cốt lõi

### 4.1. Bypass nền tảng mà không bị ban
Ban đến từ việc nền tảng **tương quan nhiều tín hiệu** để kết luận "đây là bot". Bốn lớp tín hiệu cần xử lý:

**a) Vân tay thiết bị — nhất quán và duy nhất.** GemLogin lo: mỗi profile một bộ vân tay cố định (UA, độ phân giải, Canvas/WebGL/AudioContext hash, font, số nhân CPU, RAM ảo, timezone, ngôn ngữ). *Nhất quán theo thời gian* (một profile giữ cùng vân tay), *duy nhất giữa các profile*, và *khớp nội bộ* (timezone/locale khớp vùng IP proxy).

**b) Mạng — proxy sạch, khớp địa lý.** Ưu tiên residential/mobile, mỗi profile một IP riêng, không tái dùng IP cho nhiều profile song song. Lưu ý TLS/JA3: đi qua browser thật thì JA3 giống người dùng thật — lý do dùng browser thay vì HTTP client.

**c) Hành vi — giống người.** Gõ phím trễ ngẫu nhiên, di chuột cong, cuộn trước khi thao tác; jitter giữa request; xử lý captcha/OTP khi login info; warm-up profile mới trước khi dùng.

**d) Nhịp độ & tuổi thọ.** Rate-limit theo platform và theo profile (token bucket Redis); gặp challenge thì `COOLDOWN` thay vì giết ngay, giảm `health_score`, chỉ `DEAD` khi `consecutive_fails` vượt ngưỡng; xoay vòng cả pool.

> Không có bypass "vĩnh viễn". Mục tiêu là giảm tỷ lệ ban xuống mức chấp nhận được và phát hiện nhanh khi bị siết (INCONCLUSIVE/BLOCKED tăng đột biến → cảnh báo).

### 4.2. Chia RAM/CPU & chống trùng vân tay
**Chống trùng vân tay (nguyên nhân số 1 gây ban hàng loạt):** mỗi job = 1 profile = 1 browser context/instance = 1 vân tay = 1 proxy riêng. Tuyệt đối không mở nhiều target trong một context, không clone profile chạy song song, không dùng lại profile cho nhiều job cùng lúc. "Đa luồng độc lập" = N browser độc lập, không phải N tab.

**Chia RAM/CPU:** mỗi Chromium ~300–600MB. `max_concurrency ≈ (RAM_khả_dụng − RAM_OS_và_app) / RAM_mỗi_browser` (16GB, chừa 4GB, 500MB/browser → ~24, đặt an toàn 18–20). Giảm tải: `route.abort()` resource không cần, tắt GPU, `--disable-dev-shm-usage`. Đóng browser sau job; stagger khi mở (lệch 200–500ms); giám sát PID+RAM, vượt ngưỡng → kill.

**Kết luận:** đạt "50 concurrent" bằng cách phân bổ trên nhiều máy trạm, không nhồi tab vào ít máy.

### 4.3. Chịu tải ≥50 đồng thời
"50 concurrent ở API" (dễ — async, nhận→đẩy queue→trả 202) ≠ "50 check thật song song" (khó — 50 browser). Đảm bảo bằng: tổng `max_concurrency` các station ONLINE ≥ 50; backpressure qua queue (tải vượt → job xếp hàng, không sập); Orchestrator chỉ cấp job cho station còn slot; mở rộng ngang bằng thêm máy; **load test bắt buộc** (k6/Locust) — con số chỉ đáng tin khi đã đo. Chống SPOF: tách state Orchestrator xuống Redis/PG.

---

## 5. Cấu trúc source & tổ chức thư mục

Monorepo **pnpm workspaces + Turborepo**. Ranh giới thư mục phản chiếu ranh giới ba vùng kiến trúc.

```
fastcheck/
├── CLAUDE.md                       # context gốc cho Claude Code
├── README.md
├── package.json                    # workspace root
├── pnpm-workspace.yaml
├── turbo.json                      # pipeline build/test/lint
├── tsconfig.base.json
├── docker-compose.yml              # Postgres + Redis + RabbitMQ (local dev)
├── .env.example
├── refs/                           # tài liệu nguồn: file này + yêu cầu Excel
├── docs/                           # tài liệu tham chiếu (kit)
│   └── (invariants, architecture, data-model, ...)
├── .claude/                        # rules + skills cho Claude Code
│
├── packages/                       # code dùng chung, không tự chạy
│   ├── shared/     # enum, hằng số, URL normalizer, url_hash, trace util, logger (pino)
│   ├── contracts/  # ★ nguồn sự thật shape dữ liệu giữa service: DTO API, payload queue, WS message (zod)
│   ├── config/     # ★ schema env validate bằng zod, fail-fast khi thiếu biến
│   ├── crypto/     # ★ AES-256-GCM cookie enc/dec + xoay khoá — MỘT nơi duy nhất
│   └── db/         # migrations + repositories (Kysely/Drizzle + node-pg-migrate; KHÔNG Prisma)
│       ├── migrations/
│       └── src/repositories/{profile,job,log,station,proxy}.repo.ts
│
└── apps/                           # service tự chạy được
    ├── api/            # FastCheck API (Fastify) — vùng tiếp nhận (Linux/Docker)
    │   └── src/{routes,plugins,services}/
    │       └── services/{normalize,cache,dedupe,ratelimit,circuit-breaker}.ts
    ├── orchestrator/   # NestJS + WS Gateway — vùng điều phối (Linux/Docker)
    │   └── src/{consumer,dispatch,profile-pool,station-registry,ws}/
    ├── worker/         # Client App (máy trạm) — vùng thực thi (native Windows + GemLogin)
    │   └── src/
    │       ├── ws-client/     # kết nối, đăng ký, heartbeat, reconnect, idempotency
    │       ├── browser/       # DrissionPage adapter (real|fake), cookie inject, attach CDP GemLogin, forward CDP
    │       ├── detectors/     # base (guard + vote engine) + tiktok/ facebook/ twitter/ youtube/
    │       ├── login/         # login(context, credential)->LoginResult per platform (lưu client-side)
    │       ├── concurrency/   # process pool, stagger
    │       └── process/       # PID/RAM monitor, kill process-tree theo HĐH, reap
    └── dashboard/      # React — theo dõi station/job/tỷ lệ (điểm cộng)
```

**Nguyên tắc ranh giới:**
- `packages/contracts` là **hợp đồng giữa các service** (zod). Đổi payload → sửa ở đây, TypeScript báo mọi nơi bị ảnh hưởng. Chống lệch schema âm thầm.
- `packages/shared` chứa logic thuần (normalizer, enum) — API và detector dùng chung để `url_hash` nhất quán.
- `packages/db` là nơi **duy nhất** chạm SQL; câu claim atomic sống ở `profile.repo.ts`.
- `packages/crypto` là nơi **duy nhất** mã hoá cookie; `packages/config` validate env fail-fast.
- Detector & login **per platform, không copy-paste**: dùng chung base (guard + vote engine), mỗi platform chỉ khác dữ liệu tín hiệu.

**Runtime khác nhau:** `api`+`orchestrator` chạy Linux/Docker; `worker` chạy **native Windows** cùng GemLogin (không Docker); `dashboard` là web tĩnh. Điều này ảnh hưởng cách dọn tiến trình (mục 6.8) và đóng gói.

---

## 6. Đặc tả từng tính năng (feature cards)

> Mỗi thẻ theo cấu trúc: **Mô tả** · **Yêu cầu** · **Hướng dẫn triển khai** · **Vị trí source** · **Nghiệm thu**. Đây là phần "hướng dẫn yêu cầu của tính năng" để lập trình theo.

### 6.1. FastCheck API
- **Mô tả:** cổng nhận yêu cầu check, trả nhanh, đẩy việc nặng xuống queue.
- **Yêu cầu:** `POST /check {url}` trả `202 + trace_id`; cache hit `< 500ms`; dedupe URL đang xử lý; rate-limit theo client; `GET /check/{trace_id}` poll trạng thái.
- **Hướng dẫn:** validate → **chuẩn hoá URL** (bỏ `utm_*`/`fbclid`, lowercase host, gỡ fragment, chuẩn path) → `url_hash = sha256` → tra Redis → hit trả / miss: lock stampede → upsert `check_jobs` `ON CONFLICT (url_hash) DO NOTHING` → push RabbitMQ → trả `trace_id`. Nhận diện platform + loại target từ URL bằng regex/parser.
- **Vị trí:** `apps/api/src/routes`, `apps/api/src/services/{normalize,cache,dedupe,ratelimit}`; DTO ở `packages/contracts`.
- **Nghiệm thu:** hai URL khác tracking param nhưng cùng nội dung → cùng `url_hash`; POST lại khi PENDING không tạo job thứ hai; cache hit đo được `< 500ms`.

### 6.2. Cache & dedupe (Redis)
- **Mô tả:** không check lại URL vừa check; chống 100 job trùng khi cache hết hạn.
- **Yêu cầu:** cache theo `url_hash` có TTL; không cache INCONCLUSIVE; chống stampede.
- **Hướng dẫn:** `fastcheck:result:{url_hash}` = `{status, checked_at}`, TTL ~15m (LIVE ngắn hơn DEAD). Miss → `SET lock:{url_hash} NX EX 10`, chỉ request giữ lock tạo job. Rate-limit `rl:{platform}:{profile}` token bucket (Lua atomic).
- **Vị trí:** `apps/api/src/services/{cache,dedupe}`, ioredis.
- **Nghiệm thu:** 100 request cùng URL lúc cache miss → chỉ 1 job tạo ra; INCONCLUSIVE không vào cache.

### 6.3. Hàng đợi & retry (RabbitMQ)
- **Mô tả:** tách đồng bộ, hấp thụ tải đỉnh, không mất job.
- **Yêu cầu:** manual ack; retry có backoff; DLQ + alert khi vượt `max_retries`; queue không lưu trạng thái.
- **Hướng dẫn:** exchange `fastcheck.direct` → `job.pending`; nhánh `job.retry` (TTL tăng dần + dead-letter) và `job.dlq`. Chỉ ack khi job xong. Trạng thái vòng đời luôn ở `check_jobs`.
- **Vị trí:** `apps/orchestrator/src/consumer`, amqplib.
- **Nghiệm thu:** kill worker giữa chừng → job tự requeue; job lỗi quá ngưỡng → vào DLQ + có alert.

### 6.4. Auto-login & duy trì session
- **Mô tả:** đưa profile vào trạng thái đã đăng nhập để check.
- **Yêu cầu:** login-by-cookie cho cả 4 platform; login-by-info cho TikTok & X khi cookie chết; refresh cookie sau phiên; kịch bản lưu **phía client**.
- **Hướng dẫn:** inject cookie **trước** khi điều hướng → xác minh đăng nhập (guard). Login info: gõ chậm mô phỏng người, xử lý captcha/OTP, không thử sai nhiều lần. Sau phiên thành công, mã hoá cookie mới (`packages/crypto`) và cập nhật `profiles`. Interface chung `login(context, credential) -> LoginResult`, mỗi platform một implementation. Phân biệt "cookie hết hạn" (→ login lại) khác "acc bị khoá" (→ DEAD).
- **Vị trí:** `apps/worker/src/login/*`.
- **Nghiệm thu:** cookie hợp lệ → guard pass; cookie chết → INCONCLUSIVE + mark profile (không DEAD); refresh cookie giải mã lại được.

### 6.5. Detector Live/Dead theo nền tảng ★ quyết định KPI 98%
- **Mô tả:** phân loại trạng thái target thành LIVE/DEAD/INCONCLUSIVE.
- **Yêu cầu:** **ba nhánh** (không mặc định DEAD); guard đăng nhập trước khi đọc target; vote đa tín hiệu; golden set.
- **Hướng dẫn:** mỗi platform một detector kế thừa `detectors/base` (dùng chung guard + vote engine). Kết luận = vote(HTTP status + DOM element + URL cuối). Bắt soft-404 bằng nội dung (không chỉ status). `wait_for_selector` cụ thể, timeout hợp lý (< 3 phút). Selector bền (role/aria/testid) + fallback. "Không thấy tín hiệu" → INCONCLUSIVE. Bắt exception/timeout → INCONCLUSIVE, không DEAD.
- **Vị trí:** `apps/worker/src/detectors/{base,tiktok,facebook,twitter,youtube}`; golden set ở `apps/worker/test/fixtures`.
- **Nghiệm thu:** golden set xanh; login_wall → INCONCLUSIVE+CHALLENGED; missing_selector → INCONCLUSIVE; soft404 → DEAD. Grep xác nhận không nhánh nào trả DEAD từ catch/timeout/else.

### 6.6. Auto-switch profile ★ Hạng mục 1
- **Mô tả:** khi profile đang check bị block/dead, tự đổi sang profile dự phòng và check lại.
- **Yêu cầu:** phát hiện qua `profile_health` (không nhầm với link chết); switch tự động + hỗ trợ thủ công; có van an toàn chống switch vô hạn.
- **Hướng dẫn:** worker báo `BLOCKED`/timeout → Orchestrator hạ cấp profile cũ (`COOLDOWN`/`DEAD` theo `consecutive_fails`, xoay proxy nếu nghi proxy) → claim profile mới cùng platform (mục 3.6) → re-queue `retry_count+1` (backoff). Vượt `max_retries` → DLQ + alert. Cảnh báo khi pool thấp; circuit breaker khi block diện rộng.
- **Vị trí:** `apps/orchestrator/src/profile-pool`.
- **Nghiệm thu:** ép profile BLOCKED → job chuyển profile khác và hoàn tất; pool cạn không switch vô hạn; vượt max → DLQ.

### 6.7. Multi-profile song song
- **Mô tả:** chạy nhiều profile độc lập song song để tăng thông lượng.
- **Yêu cầu:** không trùng vân tay; không vượt năng lực máy.
- **Hướng dẫn:** Orchestrator cấp job theo slot (`current_load < max_concurrency`); mỗi profile 1 proxy sticky; process pool = `max_concurrency` = prefetch; stagger mở browser; jitter giữa request; mỗi job một context riêng.
- **Vị trí:** `apps/worker/src/concurrency`, `apps/orchestrator/src/dispatch`.
- **Nghiệm thu:** số browser đồng thời ≤ tổng slot; mỗi job context độc lập.

### 6.8. Station Management ★ Hạng mục 2 (thiết kế chi tiết)

**Ranh giới:** Server = *ra lệnh & theo dõi* (không tự chạy browser); Client = *thực thi tại máy* (điều khiển GemLogin+DrissionPage cục bộ, giữ kịch bản login, báo trạng thái/tiến trình về).

**a) Kết nối & đăng ký (WebSocket realtime).** WSS+token, kết nối bền hai chiều. Client mở WS → gửi `register {station_id, name, mac, ip, agent_version, max_concurrency}` (**đăng ký = mở station management**) → Server ghi `stations`, `ONLINE`. Heartbeat ~10s (cập nhật `last_ping_at`, `current_load`). Mất kết nối → Client auto-reconnect (exponential backoff) + register lại.

**b) Registry phía Server.** Ai online, tải bao nhiêu, agent version nào. Nguồn bền ở `stations` (PG), realtime nhanh ở Redis. Dùng để cấp job đúng máy còn slot, hiển thị dashboard, phát hiện máy chết.

**c) Đồng bộ profile GemLogin (Station → Server).** Client hỏi API local GemLogin lấy danh sách profile → đẩy lên Server (lúc register + định kỳ + sau mỗi CRUD) → Server cập nhật `profiles` (gắn `assigned_station_id`). Server luôn biết profile nào ở máy nào.

**d) Giao thức lệnh (Server → Client), idempotent + `command_id`:**

| Lệnh | Client làm | Kết quả |
|---|---|---|
| `profile.create/update/delete` | gọi API GemLogin CRUD profile | id/ok/lỗi + đồng bộ lại |
| `browser.open` | inject cookie → mở GemLogin → lấy CDP endpoint | trạng thái + (tuỳ chọn) kênh CDP forward |
| `browser.close` | đóng browser, kill cây tiến trình | ok |
| `script.run` | chạy kịch bản login + detector, stream tiến trình | url_status + profile_health |

Client lưu `command_id` đã xử lý; nhận trùng → bỏ qua (mạng chập chờn gửi lại "mở browser" 2 lần chỉ mở 1).

**e) Mở browser + forward CDP.** Thứ tự bắt buộc: (1) **inject cookie đã giải mã trước khi điều hướng** (yêu cầu tối thiểu) → (2) mở GemLogin (vân tay + proxy sticky) → (3) lấy CDP endpoint, DrissionPage attach CDP → (4) nếu cần thì **forward kênh CDP/WebSocket** về Server. Forward phải qua **WSS + token**, ưu tiên mạng nội bộ/tunnel; mặc định chạy kịch bản login local rồi trả kết quả.

**f) Kịch bản login (phía Client).** Lưu tại `apps/worker/src/login`. Server chỉ **gọi** ("station, chạy script login platform X cho job này"); Client tự chạy + stream tiến trình. Login-by-cookie cho cả 4; login-by-info cho TT & X.

**g) Phát hiện máy chết & thu hồi job.** Quá ngưỡng không heartbeat → `OFFLINE` → tìm mọi `check_jobs` RUNNING theo `assigned_station_id` → re-queue + trả profile + clear cột dispatch. Không chỉ dựa registry RAM: Server restart vẫn thu hồi được nhờ cột dispatch.

**h) Process hygiene.** Timeout cứng ≤2 phút; kill **cây tiến trình** theo HĐH (Windows: `taskkill /T /F` hoặc Job Object; Linux: process group + SIGTERM→SIGKILL); giám sát RAM/PID.

**Vị trí:** `apps/orchestrator/src/{ws,station-registry,dispatch}`, `apps/worker/src/{ws-client,browser,login,process}`.
**Nghiệm thu:** xem bảng ánh xạ ở Phụ lục (mục 12).

### 6.9. Dashboard (điểm cộng)
- **Mô tả:** web theo dõi hệ thống realtime.
- **Yêu cầu:** hiển thị trạng thái/load station, tiến trình job theo `trace_id`, tỷ lệ LIVE/DEAD/**INCONCLUSIVE** (đủ 3, không gộp), sức khoẻ pool, cảnh báo block; realtime WS/SSE; không lộ cookie.
- **Vị trí:** `apps/dashboard`, type từ `packages/contracts`.
- **Nghiệm thu:** chạy vài job → dashboard cập nhật realtime; tắt station → phản ánh OFFLINE.

### 6.10. Logging & lịch sử
- **Yêu cầu:** mọi bước log kèm `trace_id`; **không log cookie/credential**; kết quả cuối vào `check_logs`.
- **Hướng dẫn:** pino JSON; `trace_id` tra một job xuyên API→queue→worker→profile→kết quả.
- **Nghiệm thu:** một request → cùng `trace_id` xuất hiện ở log 3 app + `check_logs`.

### 6.11. Tài liệu API
- **Yêu cầu:** OpenAPI/Swagger cho `POST /check`, `GET /check/{trace_id}`: schema, mã lỗi, ví dụ, rate limit, ngữ nghĩa LIVE/DEAD/INCONCLUSIVE, SLA (`<500ms` cache, `<3 phút` check).
- **Hướng dẫn:** sinh từ zod (`fastify-type-provider-zod`/tương đương), phục vụ ở `/docs`.
- **Nghiệm thu:** đổi một field trong contracts → docs đổi theo.

---

## 7. Vòng đời một job (truy vết theo trace_id)

1. Client `POST /check {url}` → API normalize → `url_hash`.
2. Redis hit → trả ngay (`< 500ms`). Miss → tạo `check_jobs` (dedupe) → push RabbitMQ → trả `trace_id`.
3. Orchestrator consume job → claim profile khoẻ (`SKIP LOCKED`) → chọn station còn slot → set RUNNING + cột dispatch → WS `RUN {url, cookie, command_id}`.
4. Client App inject cookie → mở GemLogin (vân tay + proxy riêng) → DrissionPage attach CDP → **guard đăng nhập** → thao tác DOM.
5. Detector vote → `LIVE`/`DEAD`/`INCONCLUSIVE` + xác định `profile_health`.
6. Kết quả về Orchestrator: ghi `check_logs` (url_status + profile_health riêng), cập nhật `check_jobs`, set cache; trả profile về pool (hoặc `COOLDOWN`); ack.
7. `INCONCLUSIVE`/`BLOCKED` → auto-switch, re-queue; hết retry → DLQ + alert.

---

## 8. Thiết kế vận hành công nghệ nền tảng

### 8.1. Redis — trí nhớ ngắn hạn (không phải nguồn sự thật)
| Nhóm key | Mẫu key | Cơ chế | Giải quyết |
|---|---|---|---|
| Cache kết quả | `fastcheck:result:{url_hash}` | `SET EX 900` | Trả `<500ms` |
| Lock stampede | `lock:{url_hash}` | `SET NX EX 10` | Chỉ 1 job khi 100 request cùng URL |
| Rate-limit | `rl:{platform}:{profile}` | token bucket | Giới hạn check/profile/giờ |
| Registry | `station:{id}` | HSET | Điều phối không cần query DB |

TTL LIVE < TTL DEAD; không cache INCONCLUSIVE; `maxmemory-policy allkeys-lru` (mất cache → chậm, không sai); rate-limit Lua atomic.

### 8.2. RabbitMQ — hàng đợi, retry, backpressure
Topology: `fastcheck.direct` → `job.pending`; `job.retry` (TTL+DLX); `job.dlq`. Bốn cơ chế: **manual ack**, **prefetch** (backpressure — nền tảng của "50 concurrent không crash"), **retry backoff**, **DLQ**. Bất biến: queue chỉ vận chuyển; trạng thái ở `check_jobs`; mất queue thì nạp lại PENDING từ DB.

### 8.3. WebSocket — kênh điều khiển Orchestrator ↔ Client App
Đăng ký, heartbeat ~10s (quá ngưỡng → OFFLINE + thu hồi job), auto-reconnect backoff, lệnh idempotent + `command_id`, WSS+token, không forward CDP thô ra internet.

### 8.4. PostgreSQL — nguồn sự thật & pool
Claim `FOR UPDATE SKIP LOCKED`; lease + cron dọn mỗi phút; partition `check_logs` theo tháng; PgBouncer giới hạn kết nối. Dùng **Kysely/Drizzle**, không Prisma (Prisma vướng SKIP LOCKED + PgBouncer).

### 8.5. GemLogin + DrissionPage + CDP — chuỗi thực thi
GemLogin tạo mỗi profile một context độc lập (vân tay cố định + proxy riêng) và mở CDP endpoint. DrissionPage attach vào địa chỉ CDP để "mượn" browser đã nguỵ trang → thừa hưởng vân tay + JA3. Mỗi job một context, đóng sau khi xong. Kịch bản login phía client. Ở dev/test dùng adapter fake (DrissionPage mở Chromium thường + fixture) để chạy end-to-end không cần GemLogin thật.

---

## 9. Rủi ro & vận hành
- **Nền tảng đổi cơ chế** → detector sẽ vỡ: tách detector từng platform, health-check định kỳ, golden set, alert khi INCONCLUSIVE/BLOCKED tăng.
- **ToS & pháp lý:** giới hạn đúng mục đích được duyệt (kiểm tra trạng thái link), không thu thập dữ liệu người dùng; thống nhất phạm vi với quản lý/pháp chế.
- **Chi phí tài nguyên:** acc + residential proxy đắt và hao mòn → COOLDOWN + health_score kéo dài tuổi thọ pool.
- **Bảo mật cookie:** mã hoá at-rest, không log, xoay khoá, phân quyền chặt.
- **SPOF Orchestrator:** state ra Redis/PG để chạy đa instance; station tự reconnect.

---

## 10. Bổ sung kỹ thuật chống "hỏng âm thầm"

**10.1. Proxy sticky theo profile.** Một profile bind một IP cố định xuyên vòng đời; **không xoay IP giữa phiên** (đổi IP khi đang login = tín hiệu chiếm tài khoản). Rotating chỉ ở tầng cấp IP mới. Khớp geo. Theo dõi `fail_count` theo proxy + cảnh báo khi proxy fail bất thường (proxy chết biểu hiện là INCONCLUSIVE hàng loạt).

**10.2. Cookie injection + guard.** `addCookies` **trước** khi điều hướng; cookie đủ trường (sai domain/path → bỏ qua trong im lặng). **Guard xác minh đăng nhập là chốt chặn hỏng âm thầm quan trọng nhất**: chưa login → INCONCLUSIVE + mark profile, tuyệt đối không đọc target rồi báo DEAD.

**10.3. Giới hạn luồng ở Worker.** Worker Python dùng **process pool** (1 browser = 1 process), **không dùng thread điều khiển browser**. `N` = `max_concurrency` = prefetch RabbitMQ (backpressure nhất quán).

**10.4. Dọn tiến trình treo.** Timeout cứng ≤2 phút. Kill **cây tiến trình** (sót con = rò RAM âm thầm). Windows: `taskkill /T /F`/Job Object; Linux: process group + SIGTERM→SIGKILL, zombie phải reap (tini/--init). Giám sát RAM tổng + cảnh báo.

**10.5. Bảng tín hiệu + kỷ luật đọc kết quả.**

| Nền tảng | LIVE | DEAD | BLOCKED/CHALLENGE |
|---|---|---|---|
| TikTok | video render | HTTP 404, "unavailable" | turnstile, captcha, redirect login |
| Facebook | tên profile/page, post | "content isn't available" | checkpoint, xác minh SĐT |
| X | tweet render | "post doesn't exist/deleted" | login wall, captcha |
| YouTube | player + tiêu đề | "video unavailable", "terminated" | consent/verify bất thường |

Bảng là điểm khởi đầu, health-check định kỳ. Selector hardcode = hỏng âm thầm số 1. "Không thấy tín hiệu" ≠ DEAD → INCONCLUSIVE. Vote đa tín hiệu. Golden set chạy định kỳ.

**10.6. Circuit Breaker.** Tỷ lệ BLOCKED/lỗi theo platform vượt ngưỡng trong cửa sổ trượt → mở circuit (API `503` + `retry_after`), bảo vệ pool. Half-open thăm dò rồi đóng/mở tiếp. Khác DLQ (job lẻ); circuit breaker chặn thiệt hại diện rộng.

**10.7. Reverse API — CHƯA dùng làm đường chính.** Nhanh/nhẹ nhưng chữ ký chống bot (`X-Bogus`/`msToken`) đổi liên tục, vỡ thì hỏng trong im lặng và không có DOM fallback. Giữ browser làm đường chính; reverse API chỉ thử nghiệm làm đường nhanh phụ, luôn có browser fallback.

---

## 11. Lộ trình & luật bất biến

**Lộ trình** (chi tiết `docs/roadmap.md`): Phase 0 khung → Phase 1 một đường sống + kỷ luật chính xác → Phase 2 đủ 4 platform + login → Phase 3 pool + auto-switch → Phase 4 Station Management + chịu tải → Phase 5 dashboard + hoàn thiện. Ưu tiên chứng minh sớm rủi ro cao (chính xác 98%, backpressure).

**15 luật bất biến** (chi tiết `docs/invariants.md`): INCONCLUSIVE≠DEAD; guard đăng nhập bắt buộc; tách url_status/profile_health; queue chỉ vận chuyển; Postgres nguồn sự thật; 1 job=1 profile=1 vân tay=1 proxy=1 context; sticky proxy; selector bền + vote; kill cây tiến trình; backpressure nhất quán; claim atomic; cookie mã hoá + không log + CDP không để trần; chuẩn hoá URL trước hash; lệnh idempotent; station chết thu hồi job.

---

## 12. Phụ lục — Ánh xạ yêu cầu Excel (Hạng mục 2) → thiết kế → nghiệm thu

| Yêu cầu Excel | Thiết kế | Nghiệm thu |
|---|---|---|
| Client App realtime (WS) nhận lệnh | 6.8a | Client nhận được lệnh Server đẩy xuống |
| Server quản lý danh sách + trạng thái station | 6.8b | Liệt kê đúng station ONLINE; máy tắt → OFFLINE |
| Đồng bộ & quản lý profile GemLogin từ station | 6.8c | Danh sách profile khớp `profiles` sau đồng bộ |
| Client CRUD profile GemLogin | 6.8d | create/update/delete → profile GemLogin đổi tương ứng |
| Client mở/tắt browser + forward CDP | 6.8d,e | `browser.open` mở đúng profile; CDP forward qua WSS+token |
| Module chạy kịch bản login (FB/YT/TT/X) | 6.8f | Server gọi → Client chạy script → đạt trạng thái đăng nhập |
| Đăng ký với server = mở station management | 6.8a | Sau register, máy xuất hiện & điều khiển được |
| **Tối thiểu:** import+inject cookie trước khi mở & chạy | 6.8e | Cookie addCookies trước điều hướng; guard pass |
| Server: quản lý station connect | 6.8b | Như trên |
| Server: quản lý profile trên station | 6.8c | Như trên |
| Server: gửi lệnh điều khiển browser | 6.8d | Lệnh tới đúng máy, idempotent |
| Server: gọi station chạy script login | 6.8f | Như trên |
| Lệnh idempotent chống trùng | 6.8d | Cùng `command_id` 2 lần → 1 tác dụng |
| **Tốt hơn:** Dashboard theo dõi + stream tiến trình | 6.9 | Dashboard realtime, stream bước đang chạy |
