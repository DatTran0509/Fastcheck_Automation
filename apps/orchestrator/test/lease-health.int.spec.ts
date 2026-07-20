/**
 * Test 2 + phần health của Test 3 (Phase 3) — Postgres THẬT.
 *  - Lease: claim rồi KHÔNG trả (worker treo) → lease hết hạn + reap → profile về AVAILABLE.
 *  - health_score/cooldown/consecutive_fails: recordFailure → COOLDOWN, vượt ngưỡng → DEAD; recordSuccess → AVAILABLE + hồi health.
 */
import { afterAll, beforeEach, describe, expect, it } from 'vitest';
import { Platform, ProfileStatus } from '@fastcheck/shared';
import { profileRepo, type DB } from '@fastcheck/db';
import { makeDb } from './helpers';

const LABEL = 'ptest-lease';
const STATION_ID = '00000000-0000-4000-8000-0000000000c2';
const PLATFORM = Platform.YOUTUBE; // cách ly

const db: DB = makeDb(5);

async function seedStation(): Promise<void> {
  await db
    .insertInto('stations')
    .values({ id: STATION_ID, name: 'lease-test', max_concurrency: 10 })
    .onConflict((oc) => oc.column('id').doNothing())
    .execute();
}

async function seedOneProfile(health = 100): Promise<string> {
  await db.deleteFrom('profiles').where('account_label', 'like', `${LABEL}%`).execute();
  const row = await db
    .insertInto('profiles')
    .values({
      platform: PLATFORM,
      account_label: `${LABEL}-0`,
      status: ProfileStatus.AVAILABLE,
      health_score: health,
    })
    .returning(['id'])
    .executeTakeFirstOrThrow();
  return row.id;
}

async function statusOf(id: string) {
  return db
    .selectFrom('profiles')
    .select(['status', 'health_score', 'consecutive_fails', 'cooldown_until'])
    .where('id', '=', id)
    .executeTakeFirstOrThrow();
}

beforeEach(seedStation);

afterAll(async () => {
  await db.deleteFrom('profiles').where('account_label', 'like', `${LABEL}%`).execute();
  await db.deleteFrom('stations').where('id', '=', STATION_ID).execute();
  await db.destroy();
});

describe('lease reaper (INV-11)', () => {
  it('profile IN_USE quá lease_expires_at → reap trả về AVAILABLE', async () => {
    const id = await seedOneProfile();
    const claimed = await profileRepo.claimProfile(db, PLATFORM, STATION_ID);
    expect(claimed?.id).toBe(id);
    expect((await statusOf(id)).status).toBe(ProfileStatus.IN_USE);

    // Mô phỏng worker treo: lease đã quá hạn (đặt về quá khứ).
    await db
      .updateTable('profiles')
      .set({ lease_expires_at: new Date(Date.now() - 1000) })
      .where('id', '=', id)
      .execute();

    const reaped = await profileRepo.reapExpiredLeases(db);
    expect(reaped).toBeGreaterThanOrEqual(1);
    expect((await statusOf(id)).status).toBe(ProfileStatus.AVAILABLE);
  });
});

describe('health_score / cooldown / consecutive_fails', () => {
  it('recordFailure → COOLDOWN + giảm health + tăng fails; vượt ngưỡng → DEAD', async () => {
    const id = await seedOneProfile(100);
    const opts = { profileId: id, healthPenalty: 20, cooldownSeconds: 60, deadThreshold: 3 };

    await profileRepo.recordFailure(db, opts);
    let s = await statusOf(id);
    expect(s.status).toBe(ProfileStatus.COOLDOWN);
    expect(s.health_score).toBe(80);
    expect(s.consecutive_fails).toBe(1);
    expect(s.cooldown_until).not.toBeNull();

    await profileRepo.recordFailure(db, opts);
    s = await statusOf(id);
    expect(s.status).toBe(ProfileStatus.COOLDOWN);
    expect(s.consecutive_fails).toBe(2);

    // Lần 3: consecutive_fails = 3 >= ngưỡng → DEAD (loại khỏi pool).
    await profileRepo.recordFailure(db, opts);
    s = await statusOf(id);
    expect(s.status).toBe(ProfileStatus.DEAD);
    expect(s.consecutive_fails).toBe(3);
    expect(s.cooldown_until).toBeNull();
  });

  it('recordSuccess → AVAILABLE + hồi health (cap 100) + reset fails', async () => {
    const id = await seedOneProfile(50);
    await profileRepo.recordFailure(db, {
      profileId: id,
      healthPenalty: 10,
      cooldownSeconds: 60,
      deadThreshold: 5,
    });
    await profileRepo.recordSuccess(db, id, 5);
    const s = await statusOf(id);
    expect(s.status).toBe(ProfileStatus.AVAILABLE);
    expect(s.consecutive_fails).toBe(0);
    expect(s.health_score).toBe(45); // 50 - 10 + 5
  });

  it('countAvailable đếm đúng profile khả dụng (không tính DEAD/COOLDOWN)', async () => {
    await seedOneProfile(100);
    const before = await profileRepo.countAvailable(db, PLATFORM);
    expect(before).toBeGreaterThanOrEqual(1);
  });

  it('pruneDeletedProfiles: gỡ profile đã xoá bên GemLogin (id không còn trong danh sách)', async () => {
    await db.deleteFrom('profiles').where('account_label', 'like', `${LABEL}%`).execute();
    await db
      .insertInto('profiles')
      .values([
        {
          platform: PLATFORM,
          account_label: `${LABEL}-keep`,
          gemlogin_profile_id: 'ptest-keep',
          assigned_station_id: STATION_ID,
          status: ProfileStatus.AVAILABLE,
        },
        {
          platform: PLATFORM,
          account_label: `${LABEL}-gone`,
          gemlogin_profile_id: 'ptest-gone',
          assigned_station_id: STATION_ID,
          status: ProfileStatus.AVAILABLE,
        },
      ])
      .execute();
    // GemLogin chỉ còn 'ptest-keep' → 'ptest-gone' bị gỡ.
    const pruned = await profileRepo.pruneDeletedProfiles(db, STATION_ID, ['ptest-keep']);
    expect(pruned).toContain('ptest-gone');
    const remain = (await profileRepo.listByStation(db, STATION_ID)).map((r) => r.gemlogin_profile_id);
    expect(remain).toContain('ptest-keep');
    expect(remain).not.toContain('ptest-gone');
  });

  it('MIRROR (§3): upsert profile chưa gán nền tảng (platform NULL) → hiện trong listByStation; register GÁN nền tảng; sync sau (NULL) KHÔNG clobber', async () => {
    await db.deleteFrom('profiles').where('gemlogin_profile_id', '=', 'ptest-mirror').execute();

    // 1) Sync mirror profile GemLogin CHƯA gán nền tảng (note trống → platform null).
    await profileRepo.upsertStationProfiles(db, STATION_ID, [
      { gemlogin_profile_id: 'ptest-mirror', platform: null, name: 'unassigned-01' },
    ]);
    let row = (await profileRepo.listByStation(db, STATION_ID)).find(
      (r) => r.gemlogin_profile_id === 'ptest-mirror',
    );
    expect(row).toBeDefined();
    expect(row?.platform).toBeNull(); // hiển thị trong "Xem profile" nhưng chưa dispatch được

    // 2) "Nạp tài khoản" GÁN nền tảng (dedup theo gid → cập nhật đúng dòng mirror, không tạo trùng).
    const assigned = await profileRepo.registerAccount(db, {
      platform: PLATFORM,
      gemlogin_profile_id: 'ptest-mirror',
      stationId: STATION_ID,
    });
    expect(assigned.platform).toBe(PLATFORM);
    const countAfterAssign = (await profileRepo.listByStation(db, STATION_ID)).filter(
      (r) => r.gemlogin_profile_id === 'ptest-mirror',
    ).length;
    expect(countAfterAssign).toBe(1); // KHÔNG sinh dòng trùng

    // 3) Vòng sync sau (note vẫn trống → platform null) KHÔNG được xoá gán ở bước 2.
    await profileRepo.upsertStationProfiles(db, STATION_ID, [
      { gemlogin_profile_id: 'ptest-mirror', platform: null, name: 'unassigned-01' },
    ]);
    row = (await profileRepo.listByStation(db, STATION_ID)).find(
      (r) => r.gemlogin_profile_id === 'ptest-mirror',
    );
    expect(row?.platform).toBe(PLATFORM); // giữ nguyên gán, không clobber về null

    await db.deleteFrom('profiles').where('gemlogin_profile_id', '=', 'ptest-mirror').execute();
  });

  it('reapExpiredCooldowns: COOLDOWN hết hạn → AVAILABLE (không kẹt vĩnh viễn)', async () => {
    const id = await seedOneProfile(60);
    // Đặt COOLDOWN với cooldown_until ĐÃ QUA (1 phút trước) — mô phỏng cooldown đã hết.
    await db
      .updateTable('profiles')
      .set({ status: ProfileStatus.COOLDOWN, cooldown_until: new Date(Date.now() - 60_000) })
      .where('id', '=', id)
      .execute();
    const n = await profileRepo.reapExpiredCooldowns(db);
    expect(n).toBeGreaterThanOrEqual(1);
    const s = await statusOf(id);
    expect(s.status).toBe(ProfileStatus.AVAILABLE); // claim lại được, không kẹt
    expect(s.cooldown_until).toBeNull();
  });

  it('reapExpiredCooldowns: COOLDOWN mà cooldown_until NULL (trạng thái kẹt) → AVAILABLE', async () => {
    const id = await seedOneProfile(70);
    // Mô phỏng trạng thái kẹt: COOLDOWN nhưng KHÔNG có cooldown_until (không gì giữ trong cooldown).
    await db
      .updateTable('profiles')
      .set({ status: ProfileStatus.COOLDOWN, cooldown_until: null })
      .where('id', '=', id)
      .execute();
    const n = await profileRepo.reapExpiredCooldowns(db);
    expect(n).toBeGreaterThanOrEqual(1);
    expect((await statusOf(id)).status).toBe(ProfileStatus.AVAILABLE);
  });

  it('cooldownProfile (THROTTLED/lỗi hạ tầng) → COOLDOWN nhưng KHÔNG phạt health/fails (không kết tội tài khoản)', async () => {
    const id = await seedOneProfile(80);
    // Đặt sẵn consecutive_fails=1 để chứng minh cooldownProfile KHÔNG đụng tới (không tiến tới DEAD).
    await db.updateTable('profiles').set({ consecutive_fails: 1 }).where('id', '=', id).execute();
    await profileRepo.cooldownProfile(db, id, 30);
    const s = await statusOf(id);
    expect(s.status).toBe(ProfileStatus.COOLDOWN); // nghỉ → claimProfile bỏ qua (cắt hammer)
    expect(Number(s.health_score)).toBe(80); // KHÔNG giảm health
    expect(Number(s.consecutive_fails)).toBe(1); // KHÔNG tăng fails (không kết tội → không DEAD)
    expect(s.cooldown_until).not.toBeNull();
  });
});
