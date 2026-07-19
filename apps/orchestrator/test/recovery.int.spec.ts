/**
 * Phase 4 — Test 2 (thu hồi job khi station chết, INV-15) ở tầng logic, Postgres THẬT.
 *  - recoverStationJobs: mọi check_jobs RUNNING của station → PENDING (clear cột dispatch) + profile về
 *    AVAILABLE + re-queue (publishPending). GIỮ retry_count (station chết = lỗi hạ tầng, không tiêu retry).
 *  - recoverOrphanRunning (startup sweep): thu hồi mọi RUNNING kể cả khi orchestrator vừa restart (không
 *    còn registry RAM) — nhờ cột dispatch trong check_jobs (nguồn sự thật, INV-4/INV-15).
 *  - StationRegistry.takeStale: phát hiện station quá hạn heartbeat (idempotent — chỉ trả lần đầu).
 */
import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { JobStatus, Platform, ProfileStatus, StationStatus } from '@fastcheck/shared';
import { createLogger } from '@fastcheck/shared';
import type { CheckJobMessage } from '@fastcheck/contracts';
import { type DB } from '@fastcheck/db';
import { DispatchService } from '../src/dispatch/dispatch.service';
import type { JobPublisher } from '../src/dispatch/job-publisher';
import { StationRegistryService } from '../src/station-registry/station-registry.service';
import { makeDb } from './helpers';

const PLATFORM = Platform.FACEBOOK; // cách ly khỏi các test khác
const STATION_ID = '00000000-0000-4000-8000-0000000000d1';
const OTHER_STATION = '00000000-0000-4000-8000-0000000000d2';
const LABEL = 'rec-p4';
const logger = createLogger({ name: 'test-recovery', level: 'error' });

const db: DB = makeDb(5);

/** Publisher giả: ghi lại message đã re-queue để khẳng định job được đẩy lại job.pending. */
function fakePublisher() {
  const pending: CheckJobMessage[] = [];
  const pub = {
    waitReady: vi.fn(async () => undefined),
    publishPending: vi.fn(async (m: CheckJobMessage) => {
      pending.push(m);
    }),
    publishRetry: vi.fn(async () => undefined),
    publishDlq: vi.fn(async () => undefined),
  };
  return { pub: pub as unknown as JobPublisher, pending };
}

function makeDispatch(pub: JobPublisher): DispatchService {
  // Chỉ nhánh reclaim được test → các dep khác là stub (không được gọi tới trong đường thu hồi).
  return new DispatchService(
    db,
    logger,
    { LEASE_MINUTES: 5 } as never,
    {} as never, // redis
    {} as never, // cipher
    {} as never, // registry
    {} as never, // rateLimiter
    pub,
  );
}

async function seedStations(): Promise<void> {
  for (const id of [STATION_ID, OTHER_STATION]) {
    await db
      .insertInto('stations')
      .values({ id, name: 'rec-test', max_concurrency: 10, status: StationStatus.ONLINE })
      .onConflict((oc) => oc.column('id').doUpdateSet({ status: StationStatus.ONLINE }))
      .execute();
  }
}

/** Seed 1 profile IN_USE (đang bị job giữ) + 1 job RUNNING gắn station/profile (đủ 3 cột dispatch). */
async function seedRunningJob(
  station: string,
  urlSuffix: string,
  retryCount = 0,
): Promise<{ jobId: string; profileId: string }> {
  const profile = await db
    .insertInto('profiles')
    .values({
      platform: PLATFORM,
      account_label: `${LABEL}-${urlSuffix}`,
      status: ProfileStatus.IN_USE,
      assigned_station_id: station,
      lease_expires_at: new Date(Date.now() + 5 * 60_000),
    })
    .returning(['id'])
    .executeTakeFirstOrThrow();

  const job = await db
    .insertInto('check_jobs')
    .values({
      trace_id: crypto.randomUUID(),
      target_url: `https://facebook.com/${LABEL}/${urlSuffix}`,
      url_hash: `${LABEL}-${urlSuffix}-${Date.now()}`,
      platform: PLATFORM,
      status: JobStatus.RUNNING,
      retry_count: retryCount,
      assigned_station_id: station,
      assigned_profile_id: profile.id,
      dispatched_at: new Date(),
    })
    .returning(['id'])
    .executeTakeFirstOrThrow();

  return { jobId: job.id, profileId: profile.id };
}

async function jobRow(id: string) {
  return db
    .selectFrom('check_jobs')
    .select(['status', 'retry_count', 'assigned_station_id', 'assigned_profile_id', 'dispatched_at'])
    .where('id', '=', id)
    .executeTakeFirstOrThrow();
}

async function profileStatus(id: string): Promise<string> {
  const r = await db
    .selectFrom('profiles')
    .select(['status'])
    .where('id', '=', id)
    .executeTakeFirstOrThrow();
  return r.status;
}

async function cleanup(): Promise<void> {
  await db.deleteFrom('check_jobs').where('url_hash', 'like', `${LABEL}-%`).execute();
  await db.deleteFrom('profiles').where('account_label', 'like', `${LABEL}-%`).execute();
}

beforeEach(async () => {
  await cleanup();
  await seedStations();
});

afterAll(async () => {
  await cleanup();
  await db.deleteFrom('stations').where('id', 'in', [STATION_ID, OTHER_STATION]).execute();
  await db.destroy();
});

describe('INV-15: thu hồi job khi station chết', () => {
  it('recoverStationJobs: RUNNING→PENDING + clear cột dispatch + profile về AVAILABLE + re-queue', async () => {
    const { jobId, profileId } = await seedRunningJob(STATION_ID, 'a', 1);
    const { pub, pending } = fakePublisher();
    const dispatch = makeDispatch(pub);

    const n = await dispatch.recoverStationJobs(STATION_ID);
    expect(n).toBe(1);

    const job = await jobRow(jobId);
    expect(job.status).toBe(JobStatus.PENDING); // re-queue
    expect(job.assigned_station_id).toBeNull(); // clear cột dispatch (INV-15)
    expect(job.assigned_profile_id).toBeNull();
    expect(job.dispatched_at).toBeNull();
    expect(job.retry_count).toBe(1); // GIỮ retry_count: station chết không tiêu retry của job

    expect(await profileStatus(profileId)).toBe(ProfileStatus.AVAILABLE); // profile trả về pool
    expect(pending).toHaveLength(1); // đã đẩy lại job.pending
    expect(pending[0]?.job_id).toBe(jobId);
  });

  it('chỉ thu hồi job của ĐÚNG station chết, không đụng station khác', async () => {
    const dead = await seedRunningJob(STATION_ID, 'dead');
    const alive = await seedRunningJob(OTHER_STATION, 'alive');
    const { pub } = fakePublisher();
    const dispatch = makeDispatch(pub);

    await dispatch.recoverStationJobs(STATION_ID);

    expect((await jobRow(dead.jobId)).status).toBe(JobStatus.PENDING);
    expect((await jobRow(alive.jobId)).status).toBe(JobStatus.RUNNING); // station khác: nguyên vẹn
    expect(await profileStatus(alive.profileId)).toBe(ProfileStatus.IN_USE);
  });

  it('recoverOrphanRunning (startup sweep): thu hồi MỌI RUNNING sau orchestrator restart', async () => {
    const a = await seedRunningJob(STATION_ID, 'orphan-a');
    const b = await seedRunningJob(OTHER_STATION, 'orphan-b');
    const { pub, pending } = fakePublisher();
    const dispatch = makeDispatch(pub);

    const n = await dispatch.recoverOrphanRunning();
    expect(n).toBeGreaterThanOrEqual(2);
    expect((await jobRow(a.jobId)).status).toBe(JobStatus.PENDING);
    expect((await jobRow(b.jobId)).status).toBe(JobStatus.PENDING);
    expect(pending.length).toBeGreaterThanOrEqual(2);
  });
});

describe('StationRegistry.takeStale: phát hiện station quá hạn heartbeat', () => {
  it('trả station quá hạn (lần đầu) rồi lật OFFLINE — idempotent, không thu hồi lặp', async () => {
    const registry = new StationRegistryService(db, logger);
    // Fake socket đủ để register (không gửi gì trong test này).
    const fakeSocket = { readyState: 1, OPEN: 1, send: () => undefined } as never;
    await registry.register(
      { station_id: STATION_ID, name: 'stale', agent_version: '1', max_concurrency: 1 },
      fakeSocket,
    );

    // timeoutMs = -1 → mọi station ONLINE bị coi là quá hạn ngay (now - lastPing > -1 luôn đúng).
    const first = registry.takeStale(-1);
    expect(first).toContain(STATION_ID);
    // Lần 2: đã OFFLINE → không trả lại (chỉ thu hồi MỘT lần).
    expect(registry.takeStale(-1)).not.toContain(STATION_ID);
  });
});
