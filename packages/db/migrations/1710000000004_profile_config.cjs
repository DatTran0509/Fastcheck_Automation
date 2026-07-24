/* eslint-disable */
// LƯU CẤU HÌNH VÂN TAY (ProfileConfig) do dashboard đặt cho một profile GemLogin. GemLogin KHÔNG expose endpoint
// đọc lại fingerprint (`GET /api/profile/{id}` chỉ trả name/proxy/browser/group/note) → server tự giữ config
// làm NGUỒN SỰ THẬT để form "Sửa profile" hiển thị đúng cấu hình đã đặt (sync), thay vì luôn hiện mặc định.
// JSONB nullable: NULL = chưa từng đặt qua dashboard (form hiện mặc định). KHÔNG chứa cookie/credential (INV-12).

exports.shorthands = undefined;

exports.up = (pgm) => {
  pgm.addColumns('profiles', {
    config_json: { type: 'jsonb' },
  });
};

exports.down = (pgm) => {
  pgm.dropColumns('profiles', ['config_json']);
};
