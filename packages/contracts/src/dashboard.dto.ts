import { z } from 'zod';
import { JobStatus, Platform, ProfileStatus, StationStatus, UrlStatus } from '@fastcheck/shared';

// Payload realtime cho dashboard (SSE). NGUỒN SỰ THẬT shape: dùng ở orchestrator (phát) + React (nhận).
// TUYỆT ĐỐI không chứa cookie/credential (INV-12) — dashboard chỉ hiển thị vận hành.

export const dashboardStationSchema = z.object({
  station_id: z.string(),
  name: z.string().nullish(),
  status: z.nativeEnum(StationStatus),
  current_load: z.number().int().nonnegative(),
  max_concurrency: z.number().int().positive(),
  agent_version: z.string().nullish(),
  last_ping_at: z.string().nullish(),
  ram_mb: z.number().nullish(),
  cpu_percent: z.number().nullish(),
});
export type DashboardStation = z.infer<typeof dashboardStationSchema>;

/** Ba trạng thái kết quả hiển thị TÁCH BIỆT theo platform (INV-1/INV-3 — không gộp INCONCLUSIVE vào DEAD). */
export const dashboardRatioSchema = z.object({
  platform: z.nativeEnum(Platform),
  live: z.number().int().nonnegative(),
  dead: z.number().int().nonnegative(),
  inconclusive: z.number().int().nonnegative(),
  blocked: z.number().int().nonnegative(), // số lần profile_health=BLOCKED (cảnh báo block tăng)
  total: z.number().int().nonnegative(),
});
export type DashboardRatio = z.infer<typeof dashboardRatioSchema>;

export const dashboardPoolSchema = z.object({
  platform: z.nativeEnum(Platform),
  status: z.nativeEnum(ProfileStatus),
  count: z.number().int().nonnegative(),
});
export type DashboardPool = z.infer<typeof dashboardPoolSchema>;

export const dashboardJobSchema = z.object({
  trace_id: z.string(),
  platform: z.nativeEnum(Platform),
  status: z.nativeEnum(JobStatus),
  result: z.nativeEnum(UrlStatus).nullish(),
  retry_count: z.number().int().nonnegative(),
  created_at: z.string(),
});
export type DashboardJob = z.infer<typeof dashboardJobSchema>;

// ── GET /dashboard/jobs — LỊCH SỬ job có filter + phân trang (bảng Kết quả, export Excel) ──────────
// Nguồn: check_jobs (nguồn sự thật vòng đời — INV-4) LEFT JOIN check_log mới nhất theo trace_id (lấy
// profile_health/block_reason/response_time). KHÔNG cookie/credential (INV-12).
export const jobHistoryItemSchema = z.object({
  trace_id: z.string(),
  target_url: z.string(),
  platform: z.nativeEnum(Platform),
  status: z.nativeEnum(JobStatus),
  result: z.nativeEnum(UrlStatus).nullish(), // url_status (LIVE/DEAD/INCONCLUSIVE) — null khi chưa xong
  profile_health: z.string().nullish(), // OK/CHALLENGED/BLOCKED/THROTTLED (từ check_log mới nhất)
  block_reason: z.string().nullish(),
  response_time_ms: z.number().int().nullish(),
  retry_count: z.number().int().nonnegative(),
  created_at: z.string(),
  finished_at: z.string().nullish(),
});
export type JobHistoryItem = z.infer<typeof jobHistoryItemSchema>;

export const jobHistoryResponseSchema = z.object({
  items: z.array(jobHistoryItemSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
});
export type JobHistoryResponse = z.infer<typeof jobHistoryResponseSchema>;

export const dashboardCircuitSchema = z.object({
  platform: z.nativeEnum(Platform),
  open: z.boolean(),
  retry_after_seconds: z.number().int().nonnegative(),
});
export type DashboardCircuit = z.infer<typeof dashboardCircuitSchema>;

export const dashboardAlertSchema = z.object({
  level: z.enum(['warn', 'critical']),
  kind: z.string(),
  message: z.string(),
});
export type DashboardAlert = z.infer<typeof dashboardAlertSchema>;

/** Bước tiến trình job đang chạy (§8 stream): mở browser → login → detect → xong, theo trace_id. */
export const dashboardProgressSchema = z.object({
  trace_id: z.string(),
  platform: z.nativeEnum(Platform).nullish(),
  step: z.enum(['OPEN_BROWSER', 'LOGIN', 'DETECT', 'DONE']),
  detail: z.string().nullish(),
  ts: z.string(),
});
export type DashboardProgress = z.infer<typeof dashboardProgressSchema>;

export const dashboardSnapshotSchema = z.object({
  ts: z.string(),
  stations: z.array(dashboardStationSchema),
  ratios: z.array(dashboardRatioSchema),
  pool: z.array(dashboardPoolSchema),
  recent_jobs: z.array(dashboardJobSchema),
  circuits: z.array(dashboardCircuitSchema),
  alerts: z.array(dashboardAlertSchema),
  // Stream bước đang chạy (§8) — buffer vòng các sự kiện gần nhất; rỗng nếu chưa có job real-mode.
  progress: z.array(dashboardProgressSchema),
});
export type DashboardSnapshot = z.infer<typeof dashboardSnapshotSchema>;
