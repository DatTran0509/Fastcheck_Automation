import { z } from 'zod';
import { Platform, StationStatus } from '@fastcheck/shared';
import { profileConfigSchema } from './profile-config.js';
import { loginMethodSchema } from './ws.protocol.js';

// DTO cho BỀ MẶT ĐIỀU KHIỂN (operator/dashboard → orchestrator REST, phơi qua Swagger /docs).
// Đây là kênh CON NGƯỜI vận hành Station Management: liệt kê station, CRUD profile GemLogin, mở/tắt browser,
// chạy script login, và nạp tài khoản thật vào pool để check. KHÔNG trả cookie/credential ra ngoài (INV-12).

// ── Kết quả một lệnh gửi xuống station (chờ command_ack tương ứng — INV-14) ──────────────────
export const commandResultSchema = z.object({
  ok: z.boolean(),
  command_id: z.string().uuid(),
  station_id: z.string().uuid(),
  detail: z.string().nullable(),
  // id profile GemLogin liên quan (vd profile vừa tạo). KHÔNG chứa cookie/CDP (INV-12).
  profile_id: z.string().nullable(),
});
export type CommandResult = z.infer<typeof commandResultSchema>;

// ── GET /stations ────────────────────────────────────────────────────────────
export const stationSummarySchema = z.object({
  station_id: z.string(),
  name: z.string().nullable(),
  agent_version: z.string().nullable(),
  max_concurrency: z.number().int(),
  current_load: z.number().int(),
  status: z.nativeEnum(StationStatus),
  last_ping_at: z.string(),
  ram_mb: z.number().nullable(),
  cpu_percent: z.number().nullable(),
});
export type StationSummary = z.infer<typeof stationSummarySchema>;

// ── GET /stations/:id/profiles (đọc từ bảng profiles, KHÔNG cookie) ──────────────────────────
export const stationProfileViewSchema = z.object({
  profile_id: z.string().uuid(),
  // NULL = profile GemLogin đã mirror nhưng chưa gán nền tảng ("Nạp tài khoản" để gán). Vẫn hiển thị trong
  // "Xem profile" để khớp GemLogin (§3); chưa gán thì POST /check chưa dùng được profile này.
  platform: z.nativeEnum(Platform).nullable(),
  gemlogin_profile_id: z.string().nullable(),
  account_label: z.string().nullable(),
  // status = trạng thái POOL (AVAILABLE/IN_USE/COOLDOWN/DEAD/BLOCKED) — KHÔNG phải profile_health (INV-3,
  // profile_health chỉ tồn tại theo TỪNG kết quả check ở check_logs). health_score = điểm sức khoẻ pool.
  status: z.string(),
  health_score: z.number().int(),
  consecutive_fails: z.number().int().nonnegative(),
  has_cookie: z.boolean(),
  // LÝ DO gần nhất profile bị COOLDOWN/DEAD (vd "CHALLENGED: guard đăng nhập thất bại") + mốc thời gian +
  // thời điểm hết cooldown — để dashboard GIẢI THÍCH lỗi + khuyến nghị (không để người dùng đoán).
  status_reason: z.string().nullish(),
  status_reason_at: z.string().nullish(),
  cooldown_until: z.string().nullish(),
  // Cấu hình vân tay đã đặt qua dashboard (nguồn sự thật server — GemLogin không cho đọc lại fingerprint).
  // Dùng để form "Sửa" pre-fill ĐÚNG cấu hình hiện tại (sync), không luôn hiện mặc định. NULL = chưa đặt.
  config: profileConfigSchema.nullish(),
});
export type StationProfileView = z.infer<typeof stationProfileViewSchema>;

// ── POST /stations/:id/profiles (tạo profile GemLogin qua Client) ────────────────────────────
// `config` (tuỳ chọn) = cấu hình vân tay đầy đủ (4 tab GemLogin). Có config → dashboard chỉ định vân tay cụ
// thể (KHÔNG để GemLogin random). Bỏ trống → hành vi cũ (chỉ name/proxy, GemLogin tự sinh vân tay).
// `platform` KHÔNG gán lúc tạo (optional): pool tự phân loại + gán khi "Nạp tài khoản" (POST /accounts).
export const createProfileRequestSchema = z.object({
  platform: z.nativeEnum(Platform).optional(),
  account_label: z.string().optional(),
  proxy: z.string().optional(),
  config: profileConfigSchema.optional(),
});
export type CreateProfileRequest = z.infer<typeof createProfileRequestSchema>;

// Sửa profile: dashboard gửi `config` đầy đủ (mô phỏng panel Update GemLogin) — adapter map nhóm field API
// hỗ trợ xuống GemLogin (nhóm GUI-only bỏ qua). account_label/proxy giữ để tương thích ngược.
export const updateProfileRequestSchema = z.object({
  account_label: z.string().optional(),
  proxy: z.string().optional(),
  config: profileConfigSchema.optional(),
});
export type UpdateProfileRequest = z.infer<typeof updateProfileRequestSchema>;

// ── POST /stations/:id/browser/open|close ────────────────────────────────────────────────────
export const browserActionRequestSchema = z.object({
  gemlogin_profile_id: z.string().min(1),
  // uuid profile nội bộ (tùy chọn — nếu có cookie đã lưu, orchestrator sẽ giải mã & inject TRƯỚC điều hướng).
  profile_id: z.string().uuid().optional(),
});
export type BrowserActionRequest = z.infer<typeof browserActionRequestSchema>;

// ── POST /stations/:id/login (server GỌI station chạy kịch bản login — §7) ────────────────────
// method=COOKIE dùng `cookie` (hoặc cookie đã lưu theo profile_id); method=INFO dùng username/password (Google
// OAuth); method=USERPASS dùng username/password/otp_secret + hotmail_* (X native + fallback mã email).
// Credential đi qua WSS xuống Client, KHÔNG log (INV-12).
export const runLoginRequestSchema = z.object({
  gemlogin_profile_id: z.string().min(1),
  platform: z.nativeEnum(Platform),
  method: loginMethodSchema,
  // profile_id (uuid) để tra cookie/credential đã lưu; nếu không truyền cookie/username trực tiếp.
  profile_id: z.string().uuid().optional(),
  cookie: z.string().optional(),
  username: z.string().optional(),
  password: z.string().optional(),
  otp_secret: z.string().optional(),
  // @handle của X cho bước "Confirm your account" (chống bot) — khác `username` (email đăng nhập). Chỉ X.
  confirm_username: z.string().optional(),
  // USERPASS (X native): hộp thư khôi phục lấy mã xác minh email — token ưu tiên, fallback email/password.
  hotmail_email: z.string().optional(),
  hotmail_password: z.string().optional(),
  hotmail_token: z.string().optional(),
});
export type RunLoginRequest = z.infer<typeof runLoginRequestSchema>;

// ── POST /accounts — nạp TÀI KHOẢN THẬT vào pool để check (cookie mã hoá at-rest — INV-12) ─────
// Tạo/cập nhật một dòng `profiles` (AVAILABLE) gắn gemlogin_profile_id để POST /check dùng được.
export const registerAccountRequestSchema = z.object({
  platform: z.nativeEnum(Platform),
  gemlogin_profile_id: z.string().min(1),
  station_id: z.string().uuid().optional(),
  account_label: z.string().optional(),
  // Cookie đăng nhập (JSON hoặc chuỗi) — orchestrator MÃ HOÁ (packages/crypto) rồi lưu. Bỏ trống nếu đã đăng
  // nhập tay trong GemLogin (session lưu ở profile → guard vẫn pass mà không cần cookie).
  cookie: z.string().optional(),
  proxy: z.string().optional(),
  // Mặc định VERIFY trước khi nạp (chống nạp sai platform → cooldown loạn): mở profile trên station + kiểm
  // đã đăng nhập ĐÚNG platform chưa. Không đăng nhập đúng → từ chối. Đặt false để bỏ qua (cần station online).
  verify: z.boolean().optional(),
});
export type RegisterAccountRequest = z.infer<typeof registerAccountRequestSchema>;

// ── POST /stations/:id/cdp/forward | /cdp/stop (forward CDP điều khiển browser — §5, INV-12) ──
// Server ra lệnh station bắc cầu CDP của một browser về relay orchestrator (WSS+token). Trả session_id +
// attach_path để controller (automation/DevTools proxy phía server) nối vào cùng phiên.
export const cdpForwardRequestSchema = z.object({
  gemlogin_profile_id: z.string().min(1),
  profile_id: z.string().uuid().optional(),
});
export type CdpForwardRequest = z.infer<typeof cdpForwardRequestSchema>;

export const cdpStopRequestSchema = z.object({
  session_id: z.string().uuid(),
});
export type CdpStopRequest = z.infer<typeof cdpStopRequestSchema>;

export const cdpForwardResponseSchema = z.object({
  ok: z.boolean(),
  command_id: z.string().uuid(),
  station_id: z.string().uuid(),
  detail: z.string().nullable(),
  session_id: z.string().uuid(),
  // Đường controller nối vào relay orchestrator để điều khiển browser (kèm token qua header/query — INV-12).
  attach_path: z.string(),
});
export type CdpForwardResponse = z.infer<typeof cdpForwardResponseSchema>;

export const accountResponseSchema = z.object({
  profile_id: z.string().uuid(),
  platform: z.nativeEnum(Platform),
  gemlogin_profile_id: z.string(),
  status: z.string(),
  has_cookie: z.boolean(),
});
export type AccountResponse = z.infer<typeof accountResponseSchema>;
