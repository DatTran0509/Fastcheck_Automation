export function GuidePage(): JSX.Element {
  return (
    <div className="card guide">
      <div className="hint-box">
        FastCheck kiểm tra trạng thái <b>LIVE / DEAD / INCONCLUSIVE</b> của link social (TikTok, Facebook, X,
        YouTube). Triết lý: <b>một lỗi báo ra tốt hơn một lỗi âm thầm</b> — không chắc thì trả INCONCLUSIVE, không đoán DEAD.
      </div>

      <h2>1. Chuẩn bị GemLogin + máy trạm</h2>
      <div className="step">
        <div className="step-num">1</div>
        <div>
          Mở <b>GemLogin</b> (bản đã cài, API local <code>http://127.0.0.1:1010</code>). Tạo profile và{' '}
          <b>đăng nhập</b> nền tảng cần check ngay trong browser GemLogin (cookie sống lưu ở profile).
        </div>
      </div>
      <div className="step">
        <div className="step-num">2</div>
        <div>
          Chạy worker trên máy trạm: <code>pnpm dev:worker</code>. Station sẽ hiện ở trang <b>Stations</b> (ONLINE)
          và tự đồng bộ danh sách profile lên pool.
        </div>
      </div>
      <div className="step">
        <div className="step-num">3</div>
        <div>
          Lấy <b>id THẬT</b> của profile từ API GemLogin (<code>GET /api/profiles</code>) — <b>không dùng số dòng "#"</b>{' '}
          trên bảng GemLogin. Hoặc gắn nhãn <code>fastcheck-platform=TIKTOK</code> vào <i>note</i> của profile để pool tự nhận nền tảng.
        </div>
      </div>

      <h2>2. Nạp tài khoản & đăng nhập (trang Pool &amp; Tài khoản)</h2>
      <ul>
        <li>
          <b>Chạy login</b>: server gọi máy trạm mở profile và chạy kịch bản đăng nhập. <b>COOKIE</b> dùng cho cả 4 nền
          tảng; <b>INFO</b> (user/pass) chỉ TikTok & X. Kết quả hiện outcome rõ ràng (LOGGED_IN / COOKIE_DEAD / BLOCKED / OTP_REQUIRED).
        </li>
        <li>
          <b>Nạp tài khoản vào pool</b>: gán nền tảng + cookie (mã hoá) cho profile để check dùng được. Bật{' '}
          <b>Verify</b> để chặn nạp sai nền tảng (nguồn gây cooldown loạn).
        </li>
        <li>
          X (Twitter) khó nhất: ưu tiên <b>cookie sống</b> (cần <code>auth_token</code> + <code>ct0</code>). Login-by-info
          hay gặp captcha/"unusual activity" → hệ thống báo BLOCKED (đúng, không đoán bừa).
        </li>
      </ul>

      <h2>3. Gửi check & xem kết quả (trang Kết quả)</h2>
      <ul>
        <li>Gửi link ngay ở đầu trang <b>Kết quả → Gửi check</b>, hoặc gọi <code>POST /check</code> qua API/Swagger.</li>
        <li>Bảng <b>Kết quả</b>: tìm theo link, lọc theo nền tảng/trạng thái, cuộn để tải thêm (lazy-load), <b>Xuất Excel</b>.</li>
      </ul>

      <h2>4. Hiểu các trạng thái</h2>
      <h3>Trạng thái link (target)</h3>
      <p>
        <span className="badge live"><span className="dot" />LIVE</span> còn sống ·{' '}
        <span className="badge dead"><span className="dot" />DEAD</span> đã chết (có tín hiệu chắc chắn) ·{' '}
        <span className="badge inconclusive"><span className="dot" />INCONCLUSIVE</span> không chắc chắn (KHÔNG mặc định DEAD).
      </p>
      <h3>Trạng thái pool profile</h3>
      <p>
        <span className="badge available"><span className="dot" />AVAILABLE</span> sẵn sàng ·{' '}
        <span className="badge in_use"><span className="dot" />IN_USE</span> đang chạy ·{' '}
        <span className="badge cooldown"><span className="dot" />COOLDOWN</span> đang nghỉ ·{' '}
        <span className="badge dead"><span className="dot" />DEAD</span> bị loại.
      </p>
      <h3>Vì sao profile bị COOLDOWN?</h3>
      <ul>
        <li>
          <b>CHALLENGED</b>: guard đăng nhập thất bại (cookie chết / chưa đăng nhập đúng nền tảng) → đăng nhập lại rồi nạp lại.
        </li>
        <li>
          <b>BLOCKED</b>: nền tảng chặn (captcha) → đổi profile/proxy, giảm nhịp check.
        </li>
        <li>
          <b>THROTTLED</b>: GemLogin mở browser không kịp (hạ tầng) → tự hồi sau nghỉ ngắn; thêm profile hoặc giảm số job đồng thời.
        </li>
      </ul>
      <p className="muted">
        Trang <b>Profiles</b> hiển thị lý do + khuyến nghị cho từng profile. Cooldown do <i>kết quả check</i>, không phải do
        hàng đợi. Muốn tăng đồng thời (50–100 link) → nạp <b>nhiều profile mỗi nền tảng</b> (1 profile chạy 1 link/lượt — INV-6).
      </p>

      <h2>5. Theo dõi hệ thống (trang Tổng quan & Stations)</h2>
      <ul>
        <li><b>Tổng quan</b>: tỷ lệ LIVE/DEAD/INCONCLUSIVE/BLOCKED, biểu đồ theo nền tảng, cảnh báo.</li>
        <li><b>Stations</b>: máy trạm online, tải, RAM/CPU; <b>circuit breaker</b> MỞ = platform đang bị tạm chặn để bảo vệ pool.</li>
      </ul>
    </div>
  );
}
