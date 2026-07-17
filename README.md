# FastCheck — Claude Context Kit

Bộ tài liệu ngữ cảnh để Claude Code (và bạn) làm việc trên dự án **FastCheck Automation** — dịch vụ kiểm tra trạng thái LIVE/DEAD/INCONCLUSIVE của link social ở quy mô lớn.

Kit này **không chứa code chạy được** — nó là "bộ não ngữ cảnh": kiến trúc, luật bất biến, từ vựng miền, quy ước, skills. Mục tiêu là để Claude hiểu đúng ý và tránh các bẫy "hỏng âm thầm" mà spec đã cảnh báo, thay vì đoán mò rồi viết sai một cách trông-như-đúng.

## Nội dung

```
CLAUDE.md                  ← Claude Code tự nạp file này mỗi phiên (điểm vào)
docs/
  invariants.md            ← ★ luật bất biến, đọc trước tiên
  glossary.md              ← từ vựng miền (INCONCLUSIVE ≠ DEAD, lease, sticky proxy…)
  architecture.md          ← tổng thể 3 vùng, vì sao từng công nghệ
  tech-stack.md            ← công cụ cụ thể (Kysely, pino, Vitest, zod…) + lý do
  data-model.md            ← schema Postgres, enum, câu claim atomic, cột dispatch
  job-lifecycle.md         ← vòng đời một job theo trace_id
  station-management-design.md ← thiết kế chi tiết Hạng mục 2, ánh xạ yêu cầu Excel
  roadmap.md               ← lộ trình theo giai đoạn, ánh xạ trọng số chấm điểm
  anti-patterns.md         ← danh mục "hỏng âm thầm" và cách né
  project-structure.md     ← cấu trúc source đề xuất (monorepo)
  adr/                     ← 5 quyết định kiến trúc + lý do
.claude/
  rules/                   ← quy ước code, error handling, security
  skills/                  ← platform-detector, profile-lifecycle, worker-process-hygiene
apps/*/CLAUDE.md           ← context cục bộ mỗi service (api, orchestrator, worker, dashboard)
```

## Cách gắn vào dự án

1. **Copy nguyên cây này vào gốc repo** của bạn (giữ đúng vị trí thư mục — `CLAUDE.md` ở gốc, `.claude/` ở gốc, `docs/` ở gốc, các `apps/*/CLAUDE.md` vào đúng app tương ứng).
2. Claude Code tự nạp `CLAUDE.md` gốc và `apps/*/CLAUDE.md` khi bạn làm trong thư mục con. Không cần cấu hình thêm.
3. **Skills**: các folder trong `.claude/skills/` sẽ xuất hiện trong danh sách skill của Claude và tự kích hoạt khi mô tả khớp ngữ cảnh (ví dụ bạn nói "làm detector" → skill `platform-detector`). Bạn cũng có thể gọi tên trực tiếp.
4. **Rules**: file trong `.claude/rules/` là quy ước tham chiếu. Để Claude luôn áp dụng, có thể thêm dòng `@.claude/rules/coding-conventions.md` (và các file khác) vào `CLAUDE.md` gốc — Claude Code sẽ import nội dung. Mặc định `CLAUDE.md` đã trỏ tới chúng bằng đường dẫn.

## Vì sao kit này giúp "vibe coding" không lệch ý

Spec FastCheck rất giàu các cảnh báo kiểu "đây là cái bẫy mà một cách làm ngây thơ sẽ rơi vào" (mặc định DEAD, bỏ guard đăng nhập, selector giòn, kill sót tiến trình con…). Đó chính xác là những thứ một AI code nhanh mà thiếu ngữ cảnh hay làm sai. Kit này chuyển các cảnh báo đó thành **luật máy đọc được** (`invariants.md`) + **skill kích hoạt đúng lúc**, nên khi bạn ra lệnh ngắn gọn, Claude vẫn giữ được các ràng buộc cốt tử.

## Bảo trì
- Khi kiến trúc/quy ước đổi → cập nhật `CLAUDE.md` gốc và file docs liên quan.
- Khi có quyết định kiến trúc mới → thêm một ADR vào `docs/adr/`.
- Khi phát hiện một "silent failure" mới → thêm vào `docs/anti-patterns.md` và (nếu là luật) `docs/invariants.md`.

---

## Cách chạy (source Phase 0)

> Phần này được thêm khi dựng source thật bên cạnh kit. Phase 0 = bộ khung chạy được (chưa có nghiệp vụ detector/login). Lộ trình: `docs/roadmap.md`.

### Yêu cầu
- **Node.js LTS ≥ 20.10** (đã test trên 20.20).
- **pnpm 9** — bật qua Corepack (đi kèm Node): `corepack enable && corepack prepare pnpm@9.15.9 --activate`.
- **Docker + Docker Compose** — cho Postgres + Redis + RabbitMQ local.

### Chạy lần đầu
```bash
pnpm install                       # cài toàn workspace
cp .env.example .env               # rồi sinh COOKIE_ENC_KEY thật (xem chú thích trong file)
docker compose up -d               # Postgres + Redis + RabbitMQ
pnpm db:migrate                    # tạo 5 bảng + cột dispatch + index
pnpm dev                           # chạy api + orchestrator + worker (turbo)
```

### Kiểm tra nhanh
```bash
# Tạo một job (miss cache) → 202 + trace_id
curl -X POST http://localhost:3001/check -H "content-type: application/json" \
  -d '{"url":"https://www.tiktok.com/@scout2015/video/6718335390845095173"}'

# Poll trạng thái theo trace_id
curl http://localhost:3001/check/<trace_id>

# Health của orchestrator (thấy worker đã đăng ký trong registry)
curl http://localhost:3002/health
```

### Lệnh hay dùng
```bash
pnpm dev            # 3 app ở chế độ dev (tsx watch / nest --watch)
pnpm db:migrate     # chạy migration (node-pg-migrate)
pnpm test           # unit test (Vitest): normalizer, crypto round-trip, config fail-fast
pnpm test:golden    # placeholder ở Phase 0 (golden set thật ở Phase 1)
pnpm lint           # ESLint
pnpm typecheck      # tsc --noEmit toàn repo
```

> Sinh COOKIE_ENC_KEY: `node -e "console.log(require('crypto').randomBytes(32).toString('base64'))"`.
> `.env` **không bao giờ** commit (INV-12). Worker Phase 0 dùng `GEMLOGIN_MODE=fake` — chưa mở browser thật.
