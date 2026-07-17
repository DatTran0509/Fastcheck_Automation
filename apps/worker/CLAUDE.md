# CLAUDE.md — apps/worker (Client App / máy trạm)

Vùng thực thi. **Python 3.12 + DrissionPage** (ADR-0006), chạy **native trên Windows** cùng GemLogin (KHÔNG Docker, KHÔNG Node). WS client nghe lệnh, điều khiển browser thật, phân loại, trả kết quả. Đây là nơi hai skill quan trọng nhất áp dụng: `platform-detector` và `worker-process-hygiene`.

## Trách nhiệm
- **WS client** (`websockets`): kết nối orchestrator, đăng ký (`station_id, mac, agent_version, max_concurrency`), heartbeat ~10s, auto-reconnect (exponential backoff), lưu `command_id` đã xử lý (idempotent, INV-14).
- **CRUD profile GemLogin** (gọi API GemLogin local), đồng bộ danh sách profile về server.
- **Mở/tắt browser**: inject cookie (**trước** khi điều hướng) → GemLogin mở browser (vân tay + proxy sticky) → **DrissionPage attach vào CDP endpoint** của GemLogin.
- **Login per platform**: interface `login(page, credential) -> LoginResult`, mỗi platform một bản. Kịch bản lưu **phía client**. Không copy-paste 4 script rời.
- **Detector per platform**: guard đăng nhập → vote LIVE/DEAD/INCONCLUSIVE (→ skill `platform-detector`).
- **Process hygiene**: process pool, timeout cứng, kill cây tiến trình bằng `taskkill /T /F`, giám sát RAM/PID bằng `psutil` (→ skill `worker-process-hygiene`).

## Luật cục bộ (đọc kỹ)
- **Guard đăng nhập trước khi đọc target** (INV-2) — chốt chặn hỏng âm thầm quan trọng nhất.
- **INCONCLUSIVE ≠ DEAD** (INV-1). Không khớp chắc chắn → INCONCLUSIVE.
- **1 job = 1 profile = 1 browser = 1 process = 1 vân tay = 1 proxy** (INV-6). Không nhiều target trong một context, không clone, không dùng chung state/global.
- **Sticky proxy, không xoay giữa phiên** (INV-7).
- **process pool: pool size = max_concurrency = prefetch** (INV-10). KHÔNG dùng thread điều khiển browser.
- **Kill cây tiến trình** (INV-9): Windows `taskkill /PID <pid> /T /F` hoặc Job Object; theo dõi bằng `psutil`.
- **Không log cookie/credential** (INV-12). Worker KHÔNG tự giải mã cookie — orchestrator giải mã (`packages/crypto`) rồi gửi xuống qua WSS (ADR-0006).
- Login bằng info chỉ TikTok & X khi cookie chết; FB & YT chỉ cookie. Sau phiên thành công gửi cookie mới về orchestrator để mã hoá & refresh session.

## Ranh giới ngôn ngữ (ADR-0006)
- Worker là Python; KHÔNG import `packages/*` (TS). Contract WS mirror bằng **pydantic**, khớp `packages/contracts` (zod là nguồn sự thật).
- deps quản lý bằng **uv**; format **black**, lint **ruff**, type-check **mypy** (xem `.claude/rules/coding-conventions.md`).
- Tham gia Turbo/`pnpm dev` qua **wrapper mỏng** `package.json` gọi `uv run` (để `pnpm dev` chạy được cả 3 app). Golden set chạy `uv run pytest`.

## Nhớ trước khi commit
- Sửa detector → `pnpm test:golden` (→ `uv run pytest`).
- Sửa concurrency/process → nghĩ tới rò RAM và tiến trình con sót.

Skills: `platform-detector`, `worker-process-hygiene`.
Chi tiết: `docs/adr/0006-drissionpage-python-worker.md`, `docs/station-management-design.md`, `docs/anti-patterns.md`, spec `§4.4`, `§4.5`, `§4.8`, `§6.5`, `§8`.
