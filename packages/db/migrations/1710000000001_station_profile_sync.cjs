/* eslint-disable */
// Phase 4 — đồng bộ profile GemLogin (Station → Server, station-management-design §3).
// Profile định danh bằng (assigned_station_id, gemlogin_profile_id). Thêm cột + unique partial để
// upsert idempotent theo id GemLogin trên từng máy. Cột nullable → không phá dữ liệu seed cũ.

exports.shorthands = undefined;

exports.up = (pgm) => {
  pgm.addColumn('profiles', {
    // Id profile phía GemLogin (khác id UUID nội bộ của bảng). Nguồn nhận diện khi station đồng bộ.
    gemlogin_profile_id: { type: 'varchar(128)' },
  });

  // Một profile GemLogin là duy nhất TRÊN MỖI máy (§3: định danh = id GemLogin + station_id).
  // Partial: chỉ ràng buộc khi cả hai cột có giá trị (seed thủ công không set vẫn hợp lệ).
  pgm.createIndex('profiles', ['assigned_station_id', 'gemlogin_profile_id'], {
    name: 'uq_profiles_station_gemlogin',
    unique: true,
    where: 'gemlogin_profile_id IS NOT NULL AND assigned_station_id IS NOT NULL',
  });
};

exports.down = (pgm) => {
  pgm.dropIndex('profiles', ['assigned_station_id', 'gemlogin_profile_id'], {
    name: 'uq_profiles_station_gemlogin',
  });
  pgm.dropColumn('profiles', 'gemlogin_profile_id');
};
