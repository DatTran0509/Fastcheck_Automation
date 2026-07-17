# CLAUDE.md — apps/dashboard (React)

Điểm cộng theo đề bài. Web theo dõi hệ thống, realtime. Build tĩnh, host ở đâu cũng được.

## Trách nhiệm
- Hiển thị realtime (WS/SSE, **không** polling DB nặng): trạng thái + load từng station, tiến trình job theo `trace_id`, tỷ lệ **LIVE/DEAD/INCONCLUSIVE**, sức khoẻ pool profile, cảnh báo khi tỷ lệ block tăng.

## Luật cục bộ
- Dùng type từ `packages/contracts` cho mọi payload nhận từ server — không tự định nghĩa lại shape.
- Hiển thị đủ **ba** trạng thái kết quả (đừng gộp INCONCLUSIVE vào DEAD trên UI — nó là tín hiệu vận hành quan trọng, INV-1/INV-3).
- Không hiển thị/gọi API trả về cookie/credential (INV-12).
- Ưu tiên đọc từ endpoint tổng hợp của Orchestrator, tránh query DB trực tiếp từ FE.

## Frontend design
- Trước khi dựng UI, đọc skill/tài liệu `frontend-design` nếu có trong môi trường. Giữ giao diện rõ ràng, thiên về bảng số liệu realtime hơn là hiệu ứng.

Chi tiết: `docs/architecture.md`, spec `§4.9`.
