import { z } from 'zod';

// ProfileConfig — cấu hình VÂN TAY một profile GemLogin, ánh xạ 1-1 với panel Update GemLogin (4 tab:
// Overview / Network / Cookies / Advanced settings). NGUỒN SỰ THẬT cho form dashboard + phần "bắn config
// xuống GemLogin" (adapter worker map sang payload API GemLogin).
//
// QUY ƯỚC QUAN TRỌNG: giá trị các enum ở đây dùng ĐÚNG CHÍNH TẢ chuỗi enum trong SCHEMA GemLogin phát hành
// (vd `replace|real|disabled`, `real|noise`, `custom|random`, `Windows|macOS|...`) — KHÔNG dùng UPPER_SNAKE
// nội bộ — để cái gửi lên GemLogin khớp tuyệt đối schema (nếu không GemLogin BỎ QUA/từ chối). Đây là ngoại
// lệ có chủ đích với quy ước enum UPPER_SNAKE của repo (các enum kia là domain/DB; enum này là "từ vựng của
// GemLogin"). Mọi field có .default() theo ảnh người dùng gửi.
//
// PHÂN NHÓM (create/update REQUEST body của GemLogin — xem docs/station-management-design.md §4):
//   • API-SUPPORTED: field create/update nhận (map & bắn xuống được).
//   • GUI-ONLY: có trong GUI/Fingerprints model nhưng KHÔNG có trong create/update request (ssl, plugins,
//     speech_voices, hardware_concurrency, device_memory, device_name, mac_address, do_not_track, flash,
//     port_scan_protection, hardware_acceleration, fonts) → chỉ hiển thị/lưu, không bắn.
// KHÔNG chứa cookie/credential (INV-12).

// ── Enum theo ĐÚNG chuỗi schema GemLogin ───────────────────────────────────────
export const osTypeSchema = z.enum(['Windows', 'macOS', 'Android', 'IOS', 'Linux']);
export const realNoiseSchema = z.enum(['real', 'noise']); // canvas/webgl_image/audio/media/client_rects/speech
export const realCustomSchema = z.enum(['real', 'custom']); // webgl_metadata/device_name/mac_address

export const profileConfigSchema = z.object({
  // ══ Tab OVERVIEW (API-supported) ══
  os_type: osTypeSchema.default('macOS'),
  // os_version là ENUM chuỗi theo schema: win7/win8/win10/win11 · macos10..macos13 · android9..android14 ·
  // ios14/ios15 · all_linux. UI cấp dropdown đúng theo os_type. Default macOS → macos13.
  os_version: z.string().default('macos13'),
  browser_version: z.string().default('141'),
  startup_url: z.string().default(''),
  // user_agent_mode là cờ NỘI BỘ của UI (không phải field GemLogin): 'auto' → không gửi user_agent (GemLogin
  // tự sinh); 'custom' → gửi user_agent cụ thể.
  user_agent_mode: z.enum(['auto', 'custom']).default('auto'),
  user_agent: z.string().nullish(),
  // Vị trí/ngôn ngữ (schema Location) — create/update đều nhận `country`, `language`, `time_zone`.
  country: z.string().default('Vietnam'),
  language: z.string().default('vi,en'),
  time_zone: z.string().default('Asia/Ho_Chi_Minh'),

  // ══ Tab NETWORK (API-supported) ══
  // proxy_type là cờ NỘI BỘ (GemLogin chỉ có raw_proxy). 'none' = Do not use proxies.
  proxy_type: z.enum(['none', 'http', 'https', 'socks5']).default('none'),
  proxy: z.string().nullish(),

  // ══ Tab ADVANCED — nhóm API-SUPPORTED (đúng enum schema) ══
  // WebRTC: chỉ Replace/Disable. Bỏ 'real' (dùng IP thật) — thực tế không dùng + giá trị write cho Real
  // GemLogin không nhận ('real' bị từ chối). Replace/Disable đã xác nhận hoạt động cả create lẫn update.
  web_rtc: z.enum(['replace', 'disabled']).default('disabled'),
  screen_resolution: z.enum(['custom', 'random']).default('random'),
  // Giá trị resolution cụ thể khi screen_resolution='custom' (vd '1920x1080'). Random → bỏ qua. Tên field API
  // GemLogin cho giá trị này CHƯA xác nhận chắc → adapter gửi best-effort lúc create (`resolution`).
  resolution: z.string().default('1920x1080'),
  canvas: realNoiseSchema.default('noise'),
  webgl_image: realNoiseSchema.default('noise'),
  // GUI GemLogin có 3 lựa chọn (schema phát hành ghi thiếu 'random'). custom/random đều = mask; real = không.
  webgl_metadata: z.enum(['custom', 'real', 'random']).default('custom'),
  // webgl_vendor/webgl_renderer: CHỈ dùng khi webgl_metadata='custom'. GemLogin schema CÓ 2 field này (create) —
  // UI điền sẵn mặc định phù hợp theo os_type (macOS→Apple, Windows→NVIDIA…), user sửa được.
  webgl_vendor: z.string().nullish(),
  webgl_renderer: z.string().nullish(),
  audio_context: realNoiseSchema.default('noise'),
  media_device: realNoiseSchema.default('noise'),
  client_rects: realNoiseSchema.default('noise'),

  // ══ Tab ADVANCED — nhóm GUI-ONLY (KHÔNG có trong create/update request → hiển thị/lưu, không bắn) ══
  fonts: z.enum(['default', 'custom']).default('default'),
  speech_voices: realNoiseSchema.default('noise'),
  // ssl/plugins KHÔNG có trong Fingerprints schema GemLogin nhưng CÓ trong GUI → giữ để mô phỏng GUI, GUI-only.
  ssl: realNoiseSchema.default('real'),
  plugins: realNoiseSchema.default('noise'),
  // Enum số theo schema: [2,4,8,10,12,16,20,24].
  hardware_concurrency: z.number().int().positive().default(10),
  device_memory: z.number().int().positive().default(10),
  device_name_mode: realCustomSchema.default('custom'),
  device_name: z.string().nullish(),
  mac_address_mode: realCustomSchema.default('custom'),
  mac_address: z.string().nullish(),
  do_not_track: z.enum(['default', 'open', 'close']).default('open'),
  flash: z.enum(['accept', 'decline']).default('accept'),
  port_scan_protection: z.enum(['accept', 'decline']).default('accept'),
  hardware_acceleration: z.enum(['default', 'accept', 'decline']).default('accept'),
});
export type ProfileConfig = z.infer<typeof profileConfigSchema>;

// Field GUI-ONLY: có trong GUI/Fingerprints model nhưng KHÔNG có trong create/update request → không bắn.
export const GUI_ONLY_CONFIG_FIELDS = [
  'fonts',
  'speech_voices',
  'ssl',
  'plugins',
  'hardware_concurrency',
  'device_memory',
  'device_name_mode',
  'device_name',
  'mac_address_mode',
  'mac_address',
  'do_not_track',
  'flash',
  'port_scan_protection',
  'hardware_acceleration',
] as const;

// os.version hợp lệ theo os_type (enum schema GemLogin) — UI dropdown dùng, adapter/verify tham chiếu.
export const OS_VERSIONS_BY_TYPE: Record<z.infer<typeof osTypeSchema>, string[]> = {
  Windows: ['win7', 'win8', 'win10', 'win11'],
  macOS: ['macos10', 'macos11', 'macos12', 'macos13'],
  Android: ['android9', 'android10', 'android11', 'android12', 'android13', 'android14'],
  IOS: ['ios14', 'ios15'],
  Linux: ['all_linux'],
};

// Enum số hardware_concurrency & device_memory theo schema.
export const HW_CONCURRENCY_ENUM = [2, 4, 8, 10, 12, 16, 20, 24] as const;

/** Bộ config mặc định (đúng theo ảnh người dùng gửi) — UI import để khởi tạo form. */
export function defaultProfileConfig(): ProfileConfig {
  return profileConfigSchema.parse({});
}
