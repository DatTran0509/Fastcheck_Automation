# Hướng dẫn chạy app + test THỰC TẾ mọi tính năng (kèm hướng dẫn GemLogin)

> Mục tiêu: bạn tự tay chạy toàn hệ FastCheck trên máy local + dùng **GemLogin thật** để test từng tính năng
> trong file Excel yêu cầu (Hạng mục 1 & 2). Doc này ánh xạ **mỗi yêu cầu Excel → cách bấm/gõ → kết quả kỳ vọng**.
>
> Ký hiệu: 💻 = chạy trên máy trạm (Windows, native, có GemLogin). 🐧 = api/orchestrator (Linux/Docker khi lên
> server; local thì chạy Node trực tiếp). Lệnh chạy ở thư mục gốc repo trừ khi ghi khác.

---

## 0. Bức tranh tổng thể (đọc 1 phút)

```
[Bạn] --POST /check--> API (Fastify :3001) --RabbitMQ--> Orchestrator (NestJS :3002)
                                                              │  claim profile + chọn station
                                                              │  WS (WSS+token)
                                                              ▼
                                              Worker 💻 (Python, native Windows)
                                              inject cookie → GemLogin mở browser →
                                              DrissionPage attach CDP → login/detector →
                                              trả url_status + profile_health
                                                              │
                              Postgres (nguồn sự thật) ◄───────┘  Redis (cache/lock)  Dashboard (React :5173)
```

3 tiến trình phải chạy để test end-to-end: **API**, **Orchestrator**, **Worker**. Cộng hạ tầng Docker
(Postgres/Redis/RabbitMQ) và (tùy chọn) **Dashboard**. Worker chạy chung máy với **GemLogin đang mở**.

---

## 1. Chuẩn bị môi trường (1 lần)

### 1.1. Hạ tầng + build

```bash
docker compose up -d          # Postgres + Redis + RabbitMQ (chờ 3 container "healthy")
pnpm install
pnpm build                    # build TS (api/orchestrator/packages/dashboard)
pnpm db:migrate               # tạo bảng
uv --directory apps/worker sync   # cài Python deps (DrissionPage, psutil, websockets...)
```

### 1.2. File `.env` (thư mục gốc)

Copy từ `.env.example` rồi điền. Các biến quan trọng để test THẬT:

```ini
# --- hạ tầng (khớp docker compose) ---
DATABASE_URL=postgres://fastcheck:fastcheck@127.0.0.1:5432/fastcheck
REDIS_URL=redis://127.0.0.1:6379
RABBITMQ_URL=amqp://127.0.0.1:5672

# --- mã hoá cookie (BẮT BUỘC) — sinh khoá 32 byte base64 ---
COOKIE_ENC_KEY=<base64 32 byte>     # ví dụ sinh: openssl rand -base64 32
COOKIE_KEY_ID=k1

# --- WS station <-> orchestrator ---
WS_AUTH_TOKEN=<chuỗi ngẫu nhiên dài>
ORCHESTRATOR_WS_URL=ws://127.0.0.1:3002

# --- station / worker ---
STATION_ID=<uuid>                   # sinh: uuidgen (hoặc bất kỳ UUID v4)
STATION_NAME=may-tram-01
WORKER_MAX_CONCURRENCY=5            # BẢN FREE GemLogin tối đa 5 profile → đặt 5
ORCHESTRATOR_PREFETCH=5            # = tổng max_concurrency các station (INV-10, tránh churn)

# --- GEMLOGIN THẬT ---
GEMLOGIN_MODE=real                 # 'fake' để test không cần GemLogin; 'real' để dùng GemLogin
GEMLOGIN_API_URL=http://127.0.0.1:1010
```

> 💡 `WORKER_MAX_CONCURRENCY` = số browser mở đồng thời trên 1 máy. Bản GemLogin Free = 5 profile → để **5**.
> Muốn chịu tải cao hơn 5 check thật song song: thêm **máy trạm** (mỗi máy 1 station), KHÔNG nhồi thêm tab.

---

## 2. Dùng GemLogin (bản 5.0.8 Free bạn đã cài)

GemLogin là "antidetect browser": mỗi **profile** = 1 vân tay trình duyệt + proxy riêng. Hệ FastCheck **điều
khiển GemLogin qua API local `http://127.0.0.1:1010`** (mở/tắt browser, CRUD profile). **Giữ app GemLogin luôn
mở** khi test — tắt app là mất API.

### 2.1. Bật API local (kiểm tra 1 lần)

API đã bật sẵn nếu lệnh sau trả JSON:

```bash
curl http://127.0.0.1:1010/api/status
# {"success":true,"type":"electron","port":1010,...,"activeBrowsers":0}
```

Nếu không có: vào GemLogin → **Settings/Cài đặt** → bật **Local API / Automation API** (cổng 1010).

### 2.2. Tạo profile

**Cách A — để hệ tự tạo** (khuyên dùng khi test Station Management): xem §5.3, server gửi lệnh `profile.create`.

**Cách B — tạo tay trên UI** (khi muốn đăng nhập sẵn để đo chính xác):

1. Bấm **`+ Add profile`** (góc trên trái).
2. Đặt tên (ví dụ `tiktok-01`), chọn hệ điều hành/vân tay mặc định.
3. **Proxy**: mục Proxy trong profile — dán proxy dạng `type://user:pass@host:port` (residential/mobile khớp
   geo tài khoản — INV-7). Chưa có proxy vẫn chạy được (IP máy bạn), nhưng dễ bị nền tảng chặn hơn.
4. Lưu. Profile hiện ở tab **Profile** với một **PROFILE ID** (số, ví dụ `1`, `2`...). Ghi nhớ id này.

> ⚠️ Bản **Free**: tối đa **5 profile** và **KHÔNG xoá được** (API trả "The free version does not work this
> feature" — hệ sẽ báo lỗi rõ ràng, không im lặng). Muốn xoá/nhiều hơn 5 → nâng cấp bản trả phí.

### 2.3. Proxy (tab **IPv6 Proxy** / mua ngoài)

- Bạn có thể mua proxy (quảng cáo TunProxy trong app) hoặc dùng proxy sẵn có.
- Gán proxy cho profile ở bước 2.2.3, hoặc seed vào hệ bằng `seed-proxies.ts` (§3.2) để hệ quản lý xoay proxy.
- **1 profile = 1 proxy sticky** (INV-7) — không xoay proxy giữa phiên.

### 2.4. Đăng nhập sẵn để test chính xác (QUAN TRỌNG)

Để đo đúng LIVE/DEAD, profile phải **đã đăng nhập** nền tảng (guard login mới pass — INV-2). Có 2 cách:

- **Cách A (đơn giản nhất)**: trong GemLogin, mở browser của profile (bấm nút mở/▶), **tự tay đăng nhập** vào
  TikTok/Facebook/X/YouTube một lần. GemLogin **lưu session vào profile** → lần sau hệ mở lại là đã đăng nhập,
  guard pass, detector phân loại được LIVE/DEAD. **Không cần export cookie.**
- **Cách B (qua pipeline cookie)**: export cookie → seed vào DB (xem §6). Dùng khi muốn test đúng luồng inject
  cookie + refresh.

---

## 3. Khởi động hệ (mỗi lần test)

Mở 3–4 terminal (hoặc dùng `pnpm dev` chạy chung api+orchestrator):

```bash
# T1 — API + Orchestrator (chung)
pnpm dev                       # chạy api (:3001) + orchestrator (:3002)  [🐧]

# T2 — Worker (máy trạm, GemLogin đang mở)   [💻]
pnpm dev:worker                # = uv --directory apps/worker run python -m fastcheck_worker

# T3 — Dashboard (tùy chọn)   [🐧]
pnpm --filter @fastcheck/dashboard dev     # http://localhost:5173
```

Kiểm tra sống + bề mặt điều khiển:

```bash
curl http://127.0.0.1:3001/health      # api
curl http://127.0.0.1:3002/health      # orchestrator → phải thấy station của bạn ONLINE
```

- **Swagger API (check)**   : `http://127.0.0.1:3001/docs`  — POST /check, GET /check/{trace_id}
- **Swagger Điều khiển station** : `http://127.0.0.1:3002/docs`  — stations, profile CRUD, browser, login, accounts
- **Dashboard (xem + BẤM NÚT điều khiển)** : `http://localhost:5173`

### 3.1. Seed profile + cookie (để có profile trong pool)

```bash
node apps/orchestrator/dist/scripts/seed-tiktok-profile.js    # seed 1 profile TikTok (cookie mã hoá)
```

> Với GemLogin thật: gán `gemlogin_profile_id` của profile GemLogin vào profile DB (hoặc để Station tự đồng bộ
> qua `profile_sync` — §5.2). Muốn seed nhanh cho platform khác, nhân bản script này đổi `Platform`.

### 3.2. Seed proxy (tùy chọn)

```bash
# đọc proxy từ biến môi trường (KHÔNG commit proxy thật)
FASTCHECK_SEED_PROXIES='[{"url":"http://user:pass@host:port","type":"RESIDENTIAL","region":"VN"}]' \
  node apps/orchestrator/dist/scripts/seed-proxies.js
```

---

## 4. Test Hạng mục 1 — Dịch vụ FastCheck (40%)

Mỗi mục: **cách làm** → **kết quả kỳ vọng**.

### 4.1. API kiểm tra trạng thái + response < 500ms khi cache

```bash
# Gửi 1 link cần check (thay URL thật)
curl -X POST http://127.0.0.1:3001/check -H 'Content-Type: application/json' \
  -d '{"url":"https://www.tiktok.com/@tiktok/video/123"}'
# → 202 + { "trace_id": "...", ... }

# Tra kết quả theo trace_id
curl http://127.0.0.1:3001/check/<trace_id>
# → trạng thái job + result (LIVE/DEAD/INCONCLUSIVE) khi xong

# Gửi LẠI cùng URL → cache hit
curl -X POST http://127.0.0.1:3001/check -H 'Content-Type: application/json' -d '{"url":"...tiktok..."}'
# → phản hồi < 500ms, "cached": true, KHÔNG tạo job mới
```

**Kỳ vọng**: lần đầu 202 + xử lý thật < 3 phút; lần sau cache **< 500ms**. Đo được ở log/`/metrics`.

### 4.2. Detector 4 platform — LIVE / DEAD / INCONCLUSIVE

- **Test nhanh không cần login/thật (golden set)** — chứng minh logic phân loại đúng:

  ```bash
  pnpm test:golden      # 39 ca: TikTok/Facebook/X/YouTube × (live/dead/soft404/login_wall/captcha/missing)
  ```

  **Kỳ vọng**: 39 PASS. Đây là "lưới an toàn 98%" — nền tảng đổi cơ chế thì test đỏ TRƯỚC khi KPI vỡ.
- **Test THẬT trên browser GemLogin** (chưa login → INCONCLUSIVE là đúng):

  ```bash
  uv --directory apps/worker run python scripts/e2e_real_gemlogin.py
  ```

  **Kỳ vọng**: mở 4 browser thật (mỗi platform 1), attach DrissionPage, điều hướng, detect; chưa đăng nhập →
  **INCONCLUSIVE + CHALLENGED** (TUYỆT ĐỐI không DEAD/LIVE — INV-1/2); browser đóng sạch (activeBrowsers=0).
- **Đo chính xác LIVE/DEAD thật**: đăng nhập sẵn (§2.4 cách A) rồi gửi link qua API (§4.1). Link còn sống →
  LIVE; link đã xoá/404 → DEAD; bị captcha/cookie chết → INCONCLUSIVE (không đoán bừa).

Loại target đã phủ: **FB** post/profile/group/page · **X** post/profile · **TikTok** post/profile · **YT** video/channel.

### 4.3. Đăng nhập bằng cookie (cả 4) & bằng info (TikTok, X)

```bash
uv --directory apps/worker run pytest tests/test_login.py -q     # 14 ca
```

**Kỳ vọng**: cookie-login cho cả 4 platform; **info-login TikTok & X** (gõ mô phỏng người, captcha→BLOCKED,
OTP→tự sinh mã TOTP hoặc báo cần OTP, sai mật khẩu→BAD_CREDENTIAL); FB/YT yêu cầu info → báo lỗi (đúng phạm vi).
**Test info-login thật**: cần tài khoản thật + bật gửi credential xuống worker (xem `TEST_REPORT §5.2`); mặc
định tắt để tránh khoá tài khoản.

### 4.4. Auto-switch profile khi block/dead

Kịch bản: một profile bị BLOCKED → hệ tự đổi profile khác + re-queue (có backoff), vượt max_retries → DLQ.

```bash
python scripts/e2e_phase3.py      # (fake) mô phỏng flaky BLOCKED→LIVE + captcha luôn BLOCKED→DLQ + pool cạn
```

**Kỳ vọng**: job flaky cuối cùng DONE=LIVE với profile khác; job luôn-captcha dừng ở max_retries vào DLQ + ALERT.
Thật: đăng nhập 2 profile TikTok, cho 1 profile dính captcha → quan sát job chuyển sang profile còn lại.

### 4.5. Multi-profile song song

Đặt `WORKER_MAX_CONCURRENCY=5` + có ≥5 profile AVAILABLE → gửi nhiều link cùng lúc.
**Kỳ vọng**: tối đa 5 browser mở đồng thời (đúng cap), các job còn lại xếp hàng rồi chạy dần.

### 4.6. Chịu tải 50 → 100 concurrent, không crash

```bash
LOADTEST_N=100 uv --directory apps/worker run python scripts/e2e_phase4_loadtest.py
```

**Kỳ vọng**: 100/100 hoàn tất, health 200 sau tải; browser đồng thời ≤ pool; queue depth tăng rồi rút về 0;
p95 POST < 500ms. (Đo THẬT bằng fake worker để cô lập tầng API/queue — worker thật bị chặn ở cap 5 profile Free.)

### 4.7. Lưu lịch sử vào DB

```bash
docker exec -it fastcheck-postgres psql -U fastcheck -c \
  "SELECT trace_id, url_status, profile_health, block_reason, response_time_ms FROM check_logs ORDER BY checked_at DESC LIMIT 10;"
```

**Kỳ vọng**: mỗi check có 1 dòng, **url_status TÁCH BIỆT profile_health** (INV-3). (Ghi log tập trung ELK/Loki
để sau — theo yêu cầu; hiện có pino JSON + `/metrics`.)

---

## 5. Test Hạng mục 2 — Station Management (40%) — TƯƠNG TÁC THẬT

> Mục 2 vận hành được BẰNG TAY qua **2 bề mặt**: **Swagger** `http://127.0.0.1:3002/docs` (khuyên dùng để test
> từng lệnh) và **Dashboard** `http://localhost:5173` → panel **"Điều khiển Station"** (bấm nút). Cả hai gọi
> cùng REST orchestrator. Mỗi lệnh gửi xuống Client qua WSS rồi **chờ `command_ack`** rồi mới trả HTTP (INV-14).
>
> Tự động kiểm toàn bộ mục 2 một phát: `python scripts/e2e_control.py` (9 kịch bản, dùng worker fake).

### 5.0. Bề mặt điều khiển có gì (endpoint)

| Việc (Excel)                    | REST (orchestrator :3002)                            | Trên Dashboard                |
| -------------------------------- | ---------------------------------------------------- | ------------------------------ |
| Xem station đang connect        | `GET /stations`                                    | bảng "Station"                |
| Xem danh sách profile / station | `GET /stations/{id}/profiles`                      | nút**Xem profile**      |
| Thêm profile GemLogin           | `POST /stations/{id}/profiles`                     | nút**Tạo profile**     |
| Sửa / xoá profile              | `PATCH`/`DELETE /stations/{id}/profiles/{gemId}` | (Swagger)                      |
| Mở / tắt browser               | `POST /stations/{id}/browser/open`\|`close`      | nút**Mở/Tắt browser** |
| Gọi kịch bản login            | `POST /stations/{id}/login`                        | nút**Chạy login**      |
| Nạp tài khoản thật vào pool | `POST /accounts`                                   | nút**Nạp tài khoản** |
| Gửi check (mục 1)              | `POST /check` (API :3001)                          | ô**Gửi check**         |

### 5.1. Client kết nối server (WebSocket) + đăng ký

Bật `pnpm dev` + `pnpm dev:worker` → log orchestrator: `station đã đăng ký (ONLINE)`. Kiểm:
`GET /stations` (Swagger) → thấy station của bạn `ONLINE` + `max_concurrency`. Tắt worker → sau vài giây
station **OFFLINE** + thu hồi job đang chạy (INV-15). Đây là "mở station management" (App Connect).

### 5.2. Server quản lý danh sách + đồng bộ profile GemLogin

Worker tự gọi API GemLogin lấy danh sách profile → đẩy lên server (`profile_sync`) khi đăng ký + định kỳ.
Kiểm: `GET /stations/{id}/profiles` (Swagger) hoặc nút **Xem profile** → thấy profile GemLogin của máy
(gắn `assigned_station_id`, kèm `has_cookie` — **KHÔNG trả cookie**, INV-12).

### 5.3. Server GỬI lệnh thêm/sửa/xoá profile GemLogin

Trên Swagger, `POST /stations/{id}/profiles` body `{"platform":"TIKTOK","account_label":"acc1"}` → **Execute**.
**Kỳ vọng**: trả `{ok:true, profile_id:"..."}` và **profile mới hiện trong GemLogin UI** (tab Profile). Sửa =
`PATCH .../{gemId}`. Xoá = `DELETE .../{gemId}` → **bản Free trả `ok:false` + "The free version..."** (báo lỗi
rõ ràng, không nuốt). Dashboard: nút **Tạo profile**.

### 5.4. Server GỬI lệnh mở/tắt browser + forward CDP

`POST /stations/{id}/browser/open` body `{"gemlogin_profile_id":"1"}` → **Execute**. **Kỳ vọng**: `ok:true` +
`detail` chứa `pid=...` + trạng thái CDP; **GemLogin mở browser thật** (`activeBrowsers` tăng — kiểm
`curl http://127.0.0.1:1010/api/status`). `POST .../browser/close` → đóng, `activeBrowsers` về 0.
Nếu truyền `profile_id` (uuid có cookie đã lưu) → cookie được inject TRƯỚC điều hướng (INV-2). Dashboard:
nút **Mở/Tắt browser**. **Forward CDP** (INV-12): mặc định `CDP_FORWARD_ENABLED=false`; bật cần
`CDP_FORWARD_TOKEN`, thiếu token → fail-fast.

### 5.5. Server GỌI Client chạy KỊCH BẢN ĐĂNG NHẬP (kịch bản lưu phía client)

`POST /stations/{id}/login` — đây là chỗ nhập **TÀI KHOẢN THẬT** để test:

- **Cookie (cả 4 platform)**: `{"gemlogin_profile_id":"1","platform":"TIKTOK","method":"COOKIE","cookie":"<JSON cookie thật>"}`.
- **Info (TikTok & X)**: `{"gemlogin_profile_id":"1","platform":"TIKTOK","method":"INFO","username":"...","password":"...","otp_secret":"<TOTP base32 nếu có 2FA>"}`.

**Kỳ vọng**: `ok:true` + `detail:"LOGGED_IN"` khi đăng nhập thành công (Client mở browser GemLogin → chạy
script → thu cookie mới → orchestrator mã hoá & refresh). Cookie chết → `ok:false detail:"COOKIE_DEAD..."`;
captcha → `BLOCKED`; cần OTP không secret → `OTP_REQUIRED`; **FB/YT + info → `ok:false "login_unsupported..."`**
(đúng phạm vi Excel). KHÔNG log credential (INV-12). Dashboard: điền cookie/user/pass + nút **Chạy login**.

### 5.6. Nạp TÀI KHOẢN THẬT vào pool rồi CHECK (mục 1 ↔ mục 2)

Cách nhanh nhất để "test bằng tài khoản thật và trả kết quả":

1. `POST /accounts` body `{"platform":"TIKTOK","gemlogin_profile_id":"1","station_id":"<id>","cookie":"<cookie thật>"}`
   → tạo dòng `profiles` **AVAILABLE**, cookie **mã hoá at-rest** (`has_cookie:true`). Dashboard: nút **Nạp tài khoản**.
2. `POST /check` (API :3001) link cần kiểm → dispatch chọn đúng profile này → Client mở browser GemLogin, inject
   cookie, guard pass → trả **LIVE / DEAD / INCONCLUSIVE**. Xem kết quả ở `GET /check/{trace_id}` hoặc Dashboard.

> Hoặc đơn giản hơn (không cần export cookie): đăng nhập TAY trong browser GemLogin một lần (§2.4 cách A) rồi
> `POST /accounts` **không kèm cookie** (chỉ gắn `gemlogin_profile_id` + platform) → `POST /check`. Session đã
> lưu trong profile GemLogin nên guard vẫn pass.

### 5.7. Dashboard theo dõi + stream tiến trình

Mở `http://localhost:5173`: panel **Điều khiển Station** (bấm nút mục 5.3–5.6), bảng **Station**
(status/load/RAM/CPU), **Tỷ lệ LIVE/DEAD/INCONCLUSIVE** (3 màu TÁCH BIỆT), **Pool**, **Circuit breaker**,
**Job gần đây** theo trace_id, **Cảnh báo**, và panel **"Tiến trình job đang chạy (stream)"**
(OPEN_BROWSER → LOGIN → DETECT → DONE realtime qua SSE khi chạy real mode).

---

## 6. Nạp cookie THẬT để đo chính xác 98% (khi có tài khoản)

1. Đăng nhập nền tảng trong 1 profile GemLogin (§2.4 cách A) → export cookie (extension EditThisCookie / DevTools)
   ra JSON `[{"name","value","domain","path",...}]`.
2. Seed vào DB (cookie **mã hoá AES-GCM** qua `packages/crypto`) — sửa `seed-tiktok-profile.ts` thay cookie giả
   bằng cookie thật, hoặc viết script tương tự cho platform khác. Đặt `gemlogin_profile_id` khớp profile GemLogin.
3. Gửi link qua API (§4.1). Guard pass → detector cho **LIVE/DEAD thật**.
4. Sau phiên OK, worker tự gửi **cookie mới** về orchestrator để mã hoá & refresh (`cookie_refresh`) — session
   được làm mới tự động (spec §4.4).
5. Đối chiếu bộ link đã biết trước (sống/chết) để tính tỉ lệ đúng ≥ 98%.

---

## 7. Khi lên server (bàn giao devops)

- api + orchestrator đóng **Docker** (Linux). Worker chạy **native Windows** cùng GemLogin (KHÔNG Docker).
- devops lo: reverse proxy TLS (`wss://` cho WS), PgBouncer, Prometheus/Grafana scrape `/metrics`, cron tạo
  partition `check_logs` theo tháng, tunnel WSS nếu bật forward CDP. Xem `TEST_REPORT.md §6`.

---

## 8. Bảng ánh xạ nhanh: yêu cầu Excel → cách test

| Yêu cầu Excel                               | Cách test     | Lệnh / thao tác                                     |
| --------------------------------------------- | -------------- | ----------------------------------------------------- |
| Login TT & X (cookie + info)                  | pytest + thật | `pytest tests/test_login.py`; §2.4 + §4.3         |
| Login FB & YT (cookie)                        | pytest + thật | như trên (cookie ×4)                               |
| Detector FB/X/TT/YT sống-chết               | golden + thật | `pnpm test:golden`; `e2e_real_gemlogin.py`; §4.2 |
| Import/inject cookie multi-profile            | thật          | §2.4, §3.1, §6                                     |
| Auto-switch khi block/dead                    | e2e            | `python scripts/e2e_phase3.py`; §4.4               |
| Multi-profile song song                       | thật          | `WORKER_MAX_CONCURRENCY=5` + nhiều link; §4.5     |
| API + response <500ms cache                   | curl           | §4.1                                                 |
| < 3 phút/target · ≥98% chính xác         | thật          | §4.1 (đo thời gian) + §6 (bộ link biết trước) |
| ≥50 (70–100) concurrent không crash        | load test      | `LOADTEST_N=100 ... e2e_phase4_loadtest.py`; §4.6  |
| Lưu lịch sử DB                             | psql           | §4.7                                                 |
| Client WS + đăng ký station                | Swagger/UI     | §5.1 (`GET /stations`)                             |
| Server quản station + đồng bộ profile     | Swagger/UI     | §5.2 (`GET /stations/{id}/profiles`)               |
| Client CRUD profile GemLogin                  | Swagger/UI     | §5.3 (`POST/PATCH/DELETE .../profiles`)            |
| Client mở/tắt browser + forward CDP         | Swagger/UI     | §5.4 (`POST .../browser/open\|close`)               |
| Client chạy script login (tài khoản thật) | Swagger/UI     | §5.5 (`POST .../login`)                            |
| Nạp tài khoản thật + check → LIVE/DEAD   | Swagger/UI     | §5.6 (`POST /accounts` → `POST /check`)         |
| Dashboard theo dõi + BẤM NÚT điều khiển | UI             | §5.7 (`:5173` panel Điều khiển Station)         |
| Tự động kiểm toàn bộ mục 2             | e2e            | `python scripts/e2e_control.py` (9 kịch bản)      |

---

*Tài liệu này đi kèm `TEST_REPORT.md` (kết quả đo) và `PROGRESS.md` (nhật ký dựng). Cập nhật khi thêm tính năng.*
