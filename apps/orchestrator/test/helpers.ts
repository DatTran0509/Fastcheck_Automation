/** Tiện ích test tích hợp: kết nối Postgres/Redis THẬT (không mock) từ .env (spec Phase 3). */
import { loadOrchestratorEnv } from '@fastcheck/config';
import { createDb, type DB } from '@fastcheck/db';
import { Redis } from 'ioredis';

export function testEnv() {
  return loadOrchestratorEnv();
}

/** Pool đủ lớn để N claim chạy THẬT SỰ song song (test SKIP LOCKED). */
export function makeDb(maxConnections = 25): DB {
  return createDb(testEnv().DATABASE_URL, maxConnections);
}

export function makeRedis(): Redis {
  return new Redis(testEnv().REDIS_URL, { maxRetriesPerRequest: null, lazyConnect: false });
}
