/* eslint-disable */
// Phase 5 — profile.platform NULLABLE (station-management-design §3, tiêu chí "danh sách profile trên máy
// KHỚP bảng profiles sau đồng bộ"). Bảng `profiles` là BẢN SAO (mirror) mọi profile GemLogin trên máy, không
// chỉ profile đã gán nền tảng. GemLogin không có field platform → profile chưa được gán (chưa "Nạp tài khoản"
// và chưa gắn nhãn `fastcheck-platform=` ở note) có platform = NULL: HIỂN THỊ trong "Xem profile", nhưng
// claimProfile lọc `platform = X` nên NULL không bao giờ được cấp job (không dispatch được tới khi gán nền tảng).

exports.shorthands = undefined;

exports.up = (pgm) => {
  pgm.alterColumn('profiles', 'platform', { notNull: false });
};

exports.down = (pgm) => {
  // Contract down: yêu cầu không còn dòng platform NULL (nếu có sẽ lỗi — đúng ý nghĩa hoàn nguyên).
  pgm.alterColumn('profiles', 'platform', { notNull: true });
};
