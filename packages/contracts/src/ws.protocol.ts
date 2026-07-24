import { z } from 'zod';
import { Platform, ProfileHealth, UrlStatus } from '@fastcheck/shared';
import { profileConfigSchema } from './profile-config.js';

// Giao thức WS Orchestrator ↔ Client App (station). WSS + token (INV-12).
// Lệnh Server→Client mang command_id để idempotent (INV-14). Message gắn job mang trace_id.

// ── Station info (khớp bảng stations) ────────────────────────────────────────
export const stationInfoSchema = z.object({
  station_id: z.string().uuid(),
  name: z.string().min(1),
  // .nullish(): worker Python (pydantic) phát JSON `null` cho field vắng — chấp nhận cả null lẫn undefined (ADR-0006).
  mac_address: z.string().nullish(),
  ip_address: z.string().nullish(),
  agent_version: z.string().min(1),
  max_concurrency: z.number().int().positive(),
});
export type StationInfo = z.infer<typeof stationInfoSchema>;

// ── Client → Server ───────────────────────────────────────────────────────────
export const registerMessageSchema = z.object({
  type: z.literal('register'),
  // Token KHÔNG nằm trong message này — xác thực ở HTTP upgrade qua header Authorization (INV-12).
  station: stationInfoSchema,
});

export const heartbeatMessageSchema = z.object({
  type: z.literal('heartbeat'),
  station_id: z.string().uuid(),
  current_load: z.number().int().nonnegative(),
  ts: z.string(),
  // Tài nguyên máy trạm để phơi metric RAM/CPU worker (§10.4). .nullish() cho tương thích ngược (ADR-0006).
  ram_mb: z.number().nullish(),
  cpu_percent: z.number().nullish(),
});

export const commandAckMessageSchema = z.object({
  type: z.literal('command_ack'),
  command_id: z.string().uuid(),
  station_id: z.string().uuid(),
  // Kết quả xử lý lệnh (browser.open/close, profile.*). ok=false + detail khi lỗi (không nuốt lỗi).
  ok: z.boolean(),
  detail: z.string().nullish(),
  // Trả về khi lệnh tạo/đụng tới một profile (vd id profile GemLogin mới). KHÔNG chứa cookie/CDP thô (INV-12).
  profile_id: z.string().nullish(),
});
export type CommandAckMessage = z.infer<typeof commandAckMessageSchema>;

// ── Đồng bộ danh sách profile GemLogin (Station → Server, §3 station-management-design) ──────
// Client hỏi API local GemLogin lấy danh sách profile rồi đẩy lên; Server cập nhật bảng `profiles`
// (gắn assigned_station_id) để biết profile nào ở máy nào. KHÔNG gửi cookie/credential (INV-12).
export const stationProfileSchema = z.object({
  gemlogin_profile_id: z.string().min(1),
  name: z.string().nullish(),
  // NULL = profile GemLogin chưa gán nền tảng (không nhãn `fastcheck-platform=` ở note). Vẫn mirror về server
  // để "Xem profile" khớp GemLogin (§3); server giữ platform NULL → không dispatch được tới khi gán.
  platform: z.nativeEnum(Platform).nullish(),
  // Trạng thái phía GemLogin (open/closed) — thông tin, KHÔNG phải profile_status của pool (INV-3).
  gem_status: z.string().nullish(),
});
export type StationProfile = z.infer<typeof stationProfileSchema>;

export const profileSyncMessageSchema = z.object({
  type: z.literal('profile_sync'),
  station_id: z.string().uuid(),
  profiles: z.array(stationProfileSchema),
  // TẤT CẢ id profile hiện có trong GemLogin (kể cả cái không map được platform) — để server ĐỒNG BỘ XOÁ:
  // profile trong DB (của station) mà id KHÔNG nằm trong đây = đã bị xoá bên GemLogin → gỡ khỏi pool. .nullish()
  // cho tương thích ngược (client cũ không gửi → không prune). Chỉ gửi khi list_profiles THÀNH CÔNG (tránh wipe oan).
  all_gemlogin_ids: z.array(z.string()).nullish(),
});
export type ProfileSyncMessage = z.infer<typeof profileSyncMessageSchema>;

export const jobResultMessageSchema = z.object({
  type: z.literal('job_result'),
  command_id: z.string().uuid(),
  trace_id: z.string().uuid(),
  job_id: z.string().uuid(),
  // url_status TÁCH BIỆT profile_health (INV-3)
  url_status: z.nativeEnum(UrlStatus),
  profile_health: z.nativeEnum(ProfileHealth),
  block_reason: z.string().nullable().optional(),
  response_time_ms: z.number().int().nonnegative().optional(),
});
export type JobResultMessage = z.infer<typeof jobResultMessageSchema>;

// ── Refresh cookie sau phiên đăng nhập thành công (Station → Server, spec §4.4) ──────────────
// Worker thu cookie MỚI (đã xoay) từ browser sau phiên login OK → gửi lên để orchestrator MÃ HOÁ
// (packages/crypto) rồi cập nhật profiles.cookie_ciphertext. Worker KHÔNG tự mã hoá (ADR-0006).
// Cookie đi qua kênh WSS (đã mã hoá đường truyền); KHÔNG log giá trị (INV-12).
export const cookieRefreshMessageSchema = z.object({
  type: z.literal('cookie_refresh'),
  station_id: z.string().uuid(),
  profile_id: z.string().uuid(),
  gemlogin_profile_id: z.string().nullish(),
  cookie: z.string().min(1),
});
export type CookieRefreshMessage = z.infer<typeof cookieRefreshMessageSchema>;

// ── Stream tiến trình job đang chạy (Station → Server, §8 — điểm cộng dashboard) ──────────────
// Worker phát bước đang chạy theo trace_id để dashboard hiển thị realtime: mở browser → login →
// detect → xong. KHÔNG chứa cookie/credential (INV-12) — chỉ nhãn bước + chi tiết an toàn.
export const jobProgressStepSchema = z.enum([
  'OPEN_BROWSER',
  'LOGIN',
  'DETECT',
  'DONE',
]);
export type JobProgressStep = z.infer<typeof jobProgressStepSchema>;

export const jobProgressMessageSchema = z.object({
  type: z.literal('job_progress'),
  station_id: z.string().uuid(),
  trace_id: z.string().uuid(),
  job_id: z.string().uuid(),
  step: jobProgressStepSchema,
  detail: z.string().nullish(),
  ts: z.string(),
});
export type JobProgressMessage = z.infer<typeof jobProgressMessageSchema>;

export const wsClientMessageSchema = z.discriminatedUnion('type', [
  registerMessageSchema,
  heartbeatMessageSchema,
  commandAckMessageSchema,
  jobResultMessageSchema,
  profileSyncMessageSchema,
  cookieRefreshMessageSchema,
  jobProgressMessageSchema,
]);
export type WsClientMessage = z.infer<typeof wsClientMessageSchema>;

// ── Server → Client (lệnh, idempotent + command_id — INV-14) ────────────────────
export const runCommandSchema = z.object({
  name: z.literal('script.run'),
  trace_id: z.string().uuid(),
  job_id: z.string().uuid(),
  target_url: z.string(),
  platform: z.nativeEnum(Platform),
  profile_id: z.string().uuid(),
  // id profile phía GemLogin (để real mode mở đúng browser). .nullish(): trống ở fake mode (ADR-0006).
  gemlogin_profile_id: z.string().nullish(),
  // cookie đã giải mã (orchestrator giải mã qua packages/crypto) — gửi qua kênh WSS (ADR-0006).
  cookie: z.string(),
});

export const browserOpenCommandSchema = z.object({
  name: z.literal('browser.open'),
  profile_id: z.string().uuid(),
  // gemlogin_profile_id để mở đúng profile phía GemLogin (nếu khác id nội bộ). Cookie đi kèm để inject
  // TRƯỚC điều hướng (INV-2); orchestrator giải mã cookie (INV-12). Trống ở fake mode.
  gemlogin_profile_id: z.string().nullish(),
  cookie: z.string().nullish(),
});

export const browserCloseCommandSchema = z.object({
  name: z.literal('browser.close'),
  profile_id: z.string().uuid(),
  gemlogin_profile_id: z.string().nullish(),
});

// ── Lệnh CRUD profile GemLogin (Server → Client, §4). profile_id là id phía GemLogin (không phải uuid nội bộ) ──
export const profileCreateCommandSchema = z.object({
  name: z.literal('profile.create'),
  // KHÔNG gán nền tảng lúc tạo (nullish) — pool tự phân loại/gán khi nạp tài khoản.
  platform: z.nativeEnum(Platform).nullish(),
  account_label: z.string().nullish(),
  proxy: z.string().nullish(),
  // Cấu hình vân tay đầy đủ (4 tab GemLogin). Có → client map nhóm field API-supported xuống GemLogin thay vì
  // để GemLogin random. Vắng → hành vi cũ (chỉ name/proxy).
  config: profileConfigSchema.nullish(),
});

export const profileUpdateCommandSchema = z.object({
  name: z.literal('profile.update'),
  gemlogin_profile_id: z.string().min(1),
  account_label: z.string().nullish(),
  proxy: z.string().nullish(),
  config: profileConfigSchema.nullish(),
});

export const profileDeleteCommandSchema = z.object({
  name: z.literal('profile.delete'),
  gemlogin_profile_id: z.string().min(1),
});

// ── Lệnh chạy KỊCH BẢN ĐĂNG NHẬP (Server → Client, §7 / spec §4.4) ──────────────
// Server *gọi* station chạy script login; kịch bản LƯU PHÍA CLIENT (đúng yêu cầu Excel). Client mở browser
// GemLogin → chạy login (cookie ×4 / info TT&X) → trả `command_ack`. Credential đi qua WSS (mã hoá đường
// truyền), KHÔNG log giá trị (INV-12).
//   COOKIE   → dùng `cookie`.
//   INFO     → đăng nhập bằng tài khoản Google/OAuth (TikTok/YouTube/X qua "Continue with Google"): username/password(/otp_secret).
//   USERPASS → đăng nhập X native bằng username+password+2FA(TOTP). Nếu X đòi mã 6 số qua email (LoginAcid) ở
//              BẤT KỲ bước nào, worker mở tab mới đăng nhập Hotmail lấy mã: `hotmail_token` (inject) ưu tiên,
//              fallback `hotmail_email`/`hotmail_password`. Vượt được 2FA mà không gặp LoginAcid thì bỏ qua Hotmail.
export const loginMethodSchema = z.enum(['COOKIE', 'INFO', 'USERPASS']);
export type LoginMethodDto = z.infer<typeof loginMethodSchema>;

export const loginRunCommandSchema = z.object({
  name: z.literal('login.run'),
  profile_id: z.string().uuid(),
  gemlogin_profile_id: z.string().nullish(),
  platform: z.nativeEnum(Platform),
  method: loginMethodSchema,
  cookie: z.string().nullish(),
  username: z.string().nullish(),
  password: z.string().nullish(),
  otp_secret: z.string().nullish(),
  // X chèn bước "Confirm your account" (hỏi @username) khi nghi bot, TRƯỚC bước mật khẩu/OTP. Đây là @handle
  // của X (khác `username` — thứ dùng để đăng nhập, thường là email). Chỉ dùng cho X info-login.
  confirm_username: z.string().nullish(),
  // USERPASS (X native): hộp thư khôi phục để lấy mã xác minh email (LoginAcid). KHÔNG log (INV-12).
  hotmail_email: z.string().nullish(),
  hotmail_password: z.string().nullish(),
  // Microsoft/RPS auth token (M.C...$$) — inject cookie để vào thẳng hộp thư, né form login + 2FA của Microsoft.
  hotmail_token: z.string().nullish(),
});

// ── Lệnh FORWARD CDP (Server → Client, §5 / Excel "forward CDP/websocket điều khiển trình duyệt") ──
// Server ra lệnh station BẮC CẦU (bridge) kênh CDP của một browser đang mở về relay orchestrator, qua WSS +
// token (INV-12 — KHÔNG phơi CDP trần). action=START: mở tunnel cho session_id; STOP: đóng. Client tự biết
// orchestrator WS url + token (config); session_id để relay ghép cầu worker↔controller.
export const cdpForwardCommandSchema = z.object({
  name: z.literal('cdp.forward'),
  action: z.enum(['START', 'STOP']),
  session_id: z.string().uuid(),
  profile_id: z.string().uuid().nullish(),
  // Browser đang mở cần forward CDP (id GemLogin). Client tra CDP address từ handle đang mở.
  gemlogin_profile_id: z.string().nullish(),
});

export const commandPayloadSchema = z.discriminatedUnion('name', [
  runCommandSchema,
  browserOpenCommandSchema,
  browserCloseCommandSchema,
  profileCreateCommandSchema,
  profileUpdateCommandSchema,
  profileDeleteCommandSchema,
  loginRunCommandSchema,
  cdpForwardCommandSchema,
]);
export type CommandPayload = z.infer<typeof commandPayloadSchema>;

export const serverCommandSchema = z.object({
  type: z.literal('command'),
  command_id: z.string().uuid(), // idempotent (INV-14)
  command: commandPayloadSchema,
});
export type ServerCommand = z.infer<typeof serverCommandSchema>;

export const registeredMessageSchema = z.object({
  type: z.literal('registered'),
  station_id: z.string().uuid(),
});

export const wsServerMessageSchema = z.discriminatedUnion('type', [
  serverCommandSchema,
  registeredMessageSchema,
]);
export type WsServerMessage = z.infer<typeof wsServerMessageSchema>;
