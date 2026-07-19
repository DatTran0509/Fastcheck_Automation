# ADR 0007 — Worker concurrency: bounded thread pool thay process pool (tinh chỉnh ADR-0006 §5)

- **Trạng thái:** Đã quyết định (Accepted) — thay điểm §5 của ADR-0006.
- **Bối cảnh:** ADR-0006 §5 chốt "concurrency worker = **process pool**, 1 job = 1 process OS riêng". Điều đó đúng cho mô hình Playwright cũ (Node tự spawn browser trong tiến trình điều khiển). Nhưng với **DrissionPage attach vào CDP endpoint của GemLogin**, browser thật KHÔNG do worker spawn: **GemLogin đã chạy mỗi profile trong một tiến trình OS riêng** và phơi CDP ra. Worker chỉ *gửi lệnh CDP* (blocking I/O qua websocket/HTTP) và *chờ* trang tải/DOM render.

## Quyết định

1. **Concurrency worker = bounded `ThreadPoolExecutor`, size = `max_concurrency` = prefetch RabbitMQ** (backpressure — INV-10). KHÔNG dùng `p-limit` (đó là Node; worker là Python).
2. **Mỗi job vẫn 1 browser/context độc lập** (INV-6) — sự cách ly đó do **GemLogin** cấp (mỗi profile 1 tiến trình browser riêng + 1 vân tay + 1 proxy sticky), **không** phụ thuộc việc worker dùng thread hay process. Một thread điều khiển đúng **một** browser của **một** profile; không chia sẻ context/CDP giữa các thread.
3. **Vì sao thread hợp lý ở đây:** lệnh DrissionPage là **blocking I/O** (chờ CDP trả lời, chờ trang render). Thread chờ I/O nhả GIL → N thread chờ N browser song song hiệu quả, nhẹ hơn N tiến trình con (spawn trên Windows đắt, phải pickle payload). Không có CPU-bound trong đường điều khiển nên GIL không phải nút cổ chai.

## Hoà giải với invariant (không phá INV-6/INV-10)

- **INV-6 (1 job = 1 profile = 1 vân tay = 1 proxy = 1 browser context):** GIỮ NGUYÊN. Ranh giới cách ly là **tiến trình browser của GemLogin**, không phải tiến trình worker. ADR-0006 §5 từng nói "browser đã là process riêng" — chính xác, và đó là lý do worker KHÔNG cần thêm một lớp process nữa để cách ly.
- **INV-10 (backpressure nhất quán, không dùng thread *điều khiển browser*):** câu chữ INV-10/ADR-0006 cấm thread vì lo (a) chia sẻ trạng thái giữa các luồng và (b) cách ly kém hơn process. Ở đây (a) không xảy ra — mỗi thread giữ một browser riêng của GemLogin, không dùng chung global/CDP; (b) cách ly do GemLogin lo. Phần cốt lõi của INV-10 — **pool size = max_concurrency = prefetch** — được giữ NGUYÊN VẸN.
- **INV-9 (dọn tiến trình):** timeout cứng vẫn áp; khi quá hạn, dọn **tiến trình browser của GemLogin** theo cây (`taskkill /T /F` + psutil), không phải "kill thread". Thread điều khiển được bỏ chờ; browser bị kill ở tầng OS.

## Hệ quả

- `apps/worker/fastcheck_worker/runner.py`: `ProcessPoolExecutor` → `ThreadPoolExecutor(max_workers=max_concurrency)`. `run_check` giữ nguyên là hàm cấp module (giờ chạy trong thread thay vì tiến trình con) — không cần pickle payload, khởi động nhẹ hơn.
- Timeout: `asyncio.wait_for` vẫn bao job. Thread đang chạy không "cancel" được như process; ở đường thật, việc dừng là **kill tiến trình browser GemLogin** (INV-9) khiến lệnh CDP blocking bung lỗi → thread thoát. Ở fake mode, `urllib` có socket timeout nên thread không treo.
- Cập nhật `docs/invariants.md` INV-10 và skill `worker-process-hygiene` để phản ánh "bounded thread pool (I/O-bound) + cách ly do GemLogin".

## Không đổi

INV-6/INV-7 (mỗi profile 1 vân tay + 1 proxy sticky do GemLogin), pool size = max_concurrency = prefetch, timeout cứng + kill cây tiến trình browser (INV-9), và toàn bộ các invariant khác. Đây là tinh chỉnh **cơ chế chạy song song trong worker**, không đổi triết lý cách ly.
