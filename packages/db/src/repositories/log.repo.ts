import { sql } from 'kysely';
import { ProfileHealth, UrlStatus, type Platform } from '@fastcheck/shared';
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

export interface PlatformRatio {
  platform: Platform;
  live: number;
  dead: number;
  inconclusive: number;
  blocked: number; // số lần profile_health=BLOCKED (INV-3: TÁCH BIỆT khỏi url_status)
  total: number;
}

/**
 * Tỷ lệ LIVE/DEAD/INCONCLUSIVE + số BLOCKED theo platform trong cửa sổ gần đây (dashboard/metrics).
 * Ba trạng thái đếm RIÊNG (INV-1/INV-3 — không gộp INCONCLUSIVE vào DEAD). Đọc từ check_logs (append-only).
 */
export async function ratiosByPlatform(db: DB, windowMinutes: number): Promise<PlatformRatio[]> {
  const rows = await db
    .selectFrom('check_logs')
    .innerJoin('check_jobs', 'check_jobs.id', 'check_logs.job_id')
    .where('check_logs.checked_at', '>', sql<Date>`now() - make_interval(mins => ${windowMinutes})`)
    .select((eb) => [
      'check_jobs.platform as platform',
      eb.fn
        .count<string>('check_logs.id')
        .filterWhere('check_logs.url_status', '=', UrlStatus.LIVE)
        .as('live'),
      eb.fn
        .count<string>('check_logs.id')
        .filterWhere('check_logs.url_status', '=', UrlStatus.DEAD)
        .as('dead'),
      eb.fn
        .count<string>('check_logs.id')
        .filterWhere('check_logs.url_status', '=', UrlStatus.INCONCLUSIVE)
        .as('inconclusive'),
      eb.fn
        .count<string>('check_logs.id')
        .filterWhere('check_logs.profile_health', '=', ProfileHealth.BLOCKED)
        .as('blocked'),
      eb.fn.count<string>('check_logs.id').as('total'),
    ])
    .groupBy('check_jobs.platform')
    .execute();

  return rows.map((r) => ({
    platform: r.platform,
    live: Number(r.live),
    dead: Number(r.dead),
    inconclusive: Number(r.inconclusive),
    blocked: Number(r.blocked),
    total: Number(r.total),
  }));
}
