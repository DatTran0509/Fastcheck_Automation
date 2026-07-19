import { JobStatus, type Platform, type UrlStatus } from '@fastcheck/shared';
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

export interface MarkRunningInput {
  job_id: string;
  station_id: string;
  profile_id: string;
}

/**
 * Chuyển job sang RUNNING + ghi 3 cột dispatch (INV-15): biết job nào đang ở station/profile nào
 * để thu hồi khi station chết. check_jobs là nguồn sự thật vòng đời (INV-4).
 */
export async function markRunning(db: DB, input: MarkRunningInput): Promise<void> {
  await db
    .updateTable('check_jobs')
    .set({
      status: JobStatus.RUNNING,
      assigned_station_id: input.station_id,
      assigned_profile_id: input.profile_id,
      dispatched_at: new Date(),
    })
    .where('id', '=', input.job_id)
    .execute();
}

/** Ghi kết quả cuối: DONE + result + finished_at. */
export async function markDone(db: DB, jobId: string, result: UrlStatus): Promise<void> {
  await db
    .updateTable('check_jobs')
    .set({ status: JobStatus.DONE, result, finished_at: new Date() })
    .where('id', '=', jobId)
    .execute();
}

/**
 * Auto-switch (§4.6): trả job về PENDING để re-queue bằng profile khác, tăng retry_count, xoá cột dispatch.
 * Giữ nguồn sự thật ở check_jobs (INV-4); vẫn "active" nên UNIQUE(url_hash) chống job trùng vẫn đúng.
 */
export async function markRetrying(db: DB, jobId: string, retryCount: number): Promise<void> {
  await db
    .updateTable('check_jobs')
    .set({
      status: JobStatus.PENDING,
      retry_count: retryCount,
      assigned_station_id: null,
      assigned_profile_id: null,
      dispatched_at: null,
    })
    .where('id', '=', jobId)
    .execute();
}

/** Vượt max_retries → DEAD_LETTER (chốt), finished_at. Chống switch vô hạn (skill §auto-switch). */
export async function markDeadLetter(db: DB, jobId: string): Promise<void> {
  await db
    .updateTable('check_jobs')
    .set({ status: JobStatus.DEAD_LETTER, finished_at: new Date() })
    .where('id', '=', jobId)
    .execute();
}

/**
 * Thu hồi job của station chết (INV-15): mọi job RUNNING gắn `assigned_station_id` = station đó.
 * Nguồn sự thật ở check_jobs (INV-4) → thu hồi được kể cả khi registry in-memory mất (orchestrator restart).
 */
export async function findRunningByStation(db: DB, stationId: string): Promise<CheckJob[]> {
  return db
    .selectFrom('check_jobs')
    .selectAll()
    .where('status', '=', JobStatus.RUNNING)
    .where('assigned_station_id', '=', stationId)
    .execute();
}

/**
 * Startup sweep (INV-15): mọi job RUNNING (bất kỳ station). Orchestrator vừa khởi động không còn phiên
 * nào trong RAM → mọi RUNNING là mồ côi (station sẽ trả job_result với command_id lạ → bị bỏ) → thu hồi.
 */
export async function findAllRunning(db: DB): Promise<CheckJob[]> {
  return db.selectFrom('check_jobs').selectAll().where('status', '=', JobStatus.RUNNING).execute();
}

export async function getJobById(db: DB, jobId: string): Promise<CheckJob | undefined> {
  return db.selectFrom('check_jobs').selectAll().where('id', '=', jobId).executeTakeFirst();
}

/** Job gần đây nhất (dashboard: tiến trình job theo trace_id). Không chứa cookie/credential (INV-12). */
export async function recentJobs(db: DB, limit = 20): Promise<CheckJob[]> {
  return db
    .selectFrom('check_jobs')
    .selectAll()
    .orderBy('created_at', 'desc')
    .limit(limit)
    .execute();
}

export async function getJobByTraceId(db: DB, traceId: string): Promise<CheckJob | undefined> {
  return db
    .selectFrom('check_jobs')
    .selectAll()
    .where('trace_id', '=', traceId)
    .orderBy('created_at', 'desc')
    .executeTakeFirst();
}
