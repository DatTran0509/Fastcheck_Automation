import { z } from 'zod';
import { JobStatus, Platform, UrlStatus } from '@fastcheck/shared';

/** POST /check body. */
export const checkRequestSchema = z.object({
  url: z.string().url(),
});
export type CheckRequest = z.infer<typeof checkRequestSchema>;

/** POST /check — cache MISS: job đã được tạo (hoặc đã tồn tại), trả trace_id để poll. */
export const checkAcceptedSchema = z.object({
  cached: z.literal(false),
  trace_id: z.string().uuid(),
  status: z.nativeEnum(JobStatus),
  platform: z.nativeEnum(Platform),
  url_hash: z.string(),
});
export type CheckAccepted = z.infer<typeof checkAcceptedSchema>;

/** POST /check — cache HIT: trả kết quả ngay (< 500ms). INCONCLUSIVE không bao giờ được cache (INV-1). */
export const checkCachedSchema = z.object({
  cached: z.literal(true),
  result: z.nativeEnum(UrlStatus),
  checked_at: z.string(),
});
export type CheckCached = z.infer<typeof checkCachedSchema>;

export const checkResponseSchema = z.union([checkAcceptedSchema, checkCachedSchema]);
export type CheckResponse = z.infer<typeof checkResponseSchema>;

/**
 * POST /check — 503 khi circuit breaker của platform đang MỞ (§10.6): tỷ lệ BLOCKED/lỗi vượt ngưỡng
 * trong cửa sổ trượt → chặn tạm để bảo vệ pool. Client thử lại sau `retry_after_seconds`.
 */
export const checkCircuitOpenSchema = z.object({
  error: z.literal('circuit_open'),
  platform: z.nativeEnum(Platform),
  message: z.string(),
  retry_after_seconds: z.number().int().nonnegative(),
});
export type CheckCircuitOpen = z.infer<typeof checkCircuitOpenSchema>;

/** GET /check/:trace_id — trạng thái vòng đời job (nguồn sự thật: bảng check_jobs, INV-4). */
export const checkStatusResponseSchema = z.object({
  trace_id: z.string().uuid(),
  status: z.nativeEnum(JobStatus),
  result: z.nativeEnum(UrlStatus).nullable(),
  platform: z.nativeEnum(Platform),
  target_url: z.string(),
  retry_count: z.number().int().nonnegative(),
  created_at: z.string(),
  finished_at: z.string().nullable(),
});
export type CheckStatusResponse = z.infer<typeof checkStatusResponseSchema>;
