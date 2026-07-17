import { sql } from 'kysely';
import { ProfileStatus, type Platform } from '@fastcheck/shared';
import type { DB } from '../client.js';
import type { Profile } from '../types.js';

/**
 * Claim profile ATOMIC (INV-11): một câu UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *.
 * SKIP LOCKED cho tới 50 worker lấy song song không dẫm chân nhau; set lease chống kẹt IN_USE (docs/data-model.md §claim).
 * Trả `null` nếu pool cạn (không còn AVAILABLE cùng platform).
 */
export async function claimProfile(
  db: DB,
  platform: Platform,
  stationId: string,
  leaseMinutes = 5,
): Promise<Profile | null> {
  const result = await sql<Profile>`
    UPDATE profiles
    SET status = 'IN_USE',
        lease_expires_at = now() + make_interval(mins => ${leaseMinutes}),
        assigned_station_id = ${stationId},
        last_used_at = now()
    WHERE id = (
      SELECT id FROM profiles
      WHERE platform = ${platform}
        AND status = 'AVAILABLE'
        AND (cooldown_until IS NULL OR cooldown_until < now())
      ORDER BY health_score DESC, last_used_at ASC NULLS FIRST
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    RETURNING *;
  `.execute(db);
  return result.rows[0] ?? null;
}

/** Trả profile về pool (AVAILABLE), xoá lease. */
export async function releaseProfile(db: DB, profileId: string): Promise<void> {
  await db
    .updateTable('profiles')
    .set({ status: ProfileStatus.AVAILABLE, lease_expires_at: null, assigned_station_id: null })
    .where('id', '=', profileId)
    .execute();
}
