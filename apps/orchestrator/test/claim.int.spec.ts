/**
 * Test 1 (Phase 3) — Claim đồng thời ATOMIC với Postgres THẬT (INV-11, không mock).
 *  - N=20 claim song song / pool 20 → mỗi claim nhận MỘT profile KHÁC NHAU (không trùng).
 *  - pool 5 + 20 claim → đúng 5 thành công, 15 nhận null (SKIP LOCKED, không treo).
 */
import { afterAll, beforeEach, describe, expect, it } from 'vitest';
import { Platform, ProfileStatus } from '@fastcheck/shared';
import { profileRepo, type DB } from '@fastcheck/db';
import { makeDb } from './helpers';

const LABEL = 'ptest-claim';
const STATION_ID = '00000000-0000-4000-8000-0000000000c1';
const PLATFORM = Platform.FACEBOOK; // cách ly khỏi profile TikTok seed của E2E

const db: DB = makeDb(25);

async function seedStation(): Promise<void> {
  await db
    .insertInto('stations')
    .values({ id: STATION_ID, name: 'claim-test', max_concurrency: 50 })
    .onConflict((oc) => oc.column('id').doNothing())
    .execute();
}

async function seedProfiles(n: number): Promise<void> {
  await db.deleteFrom('profiles').where('account_label', 'like', `${LABEL}%`).execute();
  const rows = Array.from({ length: n }, (_, i) => ({
    platform: PLATFORM,
    account_label: `${LABEL}-${i}`,
    status: ProfileStatus.AVAILABLE,
    health_score: 100,
  }));
  await db.insertInto('profiles').values(rows).execute();
}

beforeEach(async () => {
  await seedStation();
});

afterAll(async () => {
  await db.deleteFrom('profiles').where('account_label', 'like', `${LABEL}%`).execute();
  await db.deleteFrom('stations').where('id', '=', STATION_ID).execute();
  await db.destroy();
});

describe('claimProfile atomic (INV-11)', () => {
  it('20 claim song song / pool 20 → 20 profile KHÁC NHAU, không trùng', async () => {
    await seedProfiles(20);
    const results = await Promise.all(
      Array.from({ length: 20 }, () => profileRepo.claimProfile(db, PLATFORM, STATION_ID)),
    );
    const claimed = results.filter((p): p is NonNullable<typeof p> => p !== null);
    expect(claimed).toHaveLength(20);
    const uniqueIds = new Set(claimed.map((p) => p.id));
    expect(uniqueIds.size).toBe(20); // không có hai claim nào nhận cùng profile
  });

  it('20 claim / pool 5 → đúng 5 thành công, 15 null (SKIP LOCKED, không treo)', async () => {
    await seedProfiles(5);
    const results = await Promise.all(
      Array.from({ length: 20 }, () => profileRepo.claimProfile(db, PLATFORM, STATION_ID)),
    );
    const claimed = results.filter((p): p is NonNullable<typeof p> => p !== null);
    const nulls = results.filter((p) => p === null);
    expect(claimed).toHaveLength(5);
    expect(nulls).toHaveLength(15);
    expect(new Set(claimed.map((p) => p.id)).size).toBe(5); // 5 profile phân biệt
  });
});
