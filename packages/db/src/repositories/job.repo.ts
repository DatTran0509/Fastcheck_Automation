import { JobStatus, type Platform } from '@fastcheck/shared';
import type { DB } from '../client.js';
import type { CheckJob } from '../types.js';

export interface CreatePendingJobInput {
  trace_id: string;
  target_url: string;
  url_hash: string;
  platform: Platform;
}

/**
 * Tạo job PENDING. Dedupe qua UNIQUE(url_hash) WHERE status IN (PENDING,RUNNING) + ON CONFLICT DO NOTHING.
 * Trả `null` nếu đã có job active cho url này (không tạo dòng thứ hai — INV-4/INV-13).
 */
export async function createPendingJob(
  db: DB,
  input: CreatePendingJobInput,
): Promise<CheckJob | null> {
  const rows = await db
    .insertInto('check_jobs')
    .values({
      trace_id: input.trace_id,
      target_url: input.target_url,
      url_hash: input.url_hash,
      platform: input.platform,
      status: JobStatus.PENDING,
      retry_count: 0,
    })
    .onConflict((oc) =>
      oc.columns(['url_hash']).where('status', 'in', [JobStatus.PENDING, JobStatus.RUNNING]).doNothing(),
    )
    .returningAll()
    .execute();
  return rows[0] ?? null;
}

export async function findActiveJobByUrlHash(
  db: DB,
  urlHash: string,
): Promise<CheckJob | undefined> {
  return db
    .selectFrom('check_jobs')
    .selectAll()
    .where('url_hash', '=', urlHash)
    .where('status', 'in', [JobStatus.PENDING, JobStatus.RUNNING])
    .orderBy('created_at', 'desc')
    .executeTakeFirst();
}

export async function getJobByTraceId(db: DB, traceId: string): Promise<CheckJob | undefined> {
  return db
    .selectFrom('check_jobs')
    .selectAll()
    .where('trace_id', '=', traceId)
    .orderBy('created_at', 'desc')
    .executeTakeFirst();
}
