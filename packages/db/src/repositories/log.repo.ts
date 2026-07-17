import type { ProfileHealth, UrlStatus } from '@fastcheck/shared';
import type { DB } from '../client.js';

export interface InsertLogInput {
  trace_id: string;
  job_id?: string | null;
  profile_id?: string | null;
  target_url: string;
  url_status: UrlStatus; // TARGET (INV-3)
  profile_health: ProfileHealth; // PROFILE — TÁCH BIỆT (INV-3)
  block_reason?: string | null;
  response_time_ms?: number | null;
}

/** Ghi một lần thử vào check_logs (append-only). url_status và profile_health tách riêng (INV-3). */
export async function insertCheckLog(db: DB, input: InsertLogInput): Promise<void> {
  await db
    .insertInto('check_logs')
    .values({
      trace_id: input.trace_id,
      job_id: input.job_id ?? null,
      profile_id: input.profile_id ?? null,
      target_url: input.target_url,
      url_status: input.url_status,
      profile_health: input.profile_health,
      block_reason: input.block_reason ?? null,
      response_time_ms: input.response_time_ms ?? null,
    })
    .execute();
}
