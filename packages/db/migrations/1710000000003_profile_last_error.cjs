/* eslint-disable */
// Phase 5 — LƯU LÝ DO khi profile bị COOLDOWN/DEAD (error-handling-and-observability: "mọi lỗi phải giải thích
// được cho người dùng"). `last_error` = chuỗi ngắn "<HEALTH>: <chi tiết>" (vd "CHALLENGED: guard đăng nhập
// thất bại"), `last_error_at` = thời điểm. Dashboard hiển thị LÝ DO cooldown + khuyến nghị thay vì để người
// dùng đoán. Set khi recordFailure/cooldownProfile; xoá khi thành công/nạp lại. KHÔNG chứa cookie (INV-12).

exports.shorthands = undefined;

exports.up = (pgm) => {
  pgm.addColumns('profiles', {
    last_error: { type: 'varchar(300)' },
    last_error_at: { type: 'timestamptz' },
  });
};

exports.down = (pgm) => {
  pgm.dropColumns('profiles', ['last_error', 'last_error_at']);
};
