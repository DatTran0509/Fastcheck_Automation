import { z } from 'zod';
import { Platform } from '@fastcheck/shared';

/** Payload đẩy lên RabbitMQ (job.pending). Queue CHỈ vận chuyển — trạng thái ở check_jobs (INV-4). */
export const checkJobMessageSchema = z.object({
  trace_id: z.string().uuid(),
  job_id: z.string().uuid(),
  target_url: z.string(),
  url_hash: z.string(),
  platform: z.nativeEnum(Platform),
  retry_count: z.number().int().nonnegative().default(0),
});
export type CheckJobMessage = z.infer<typeof checkJobMessageSchema>;
