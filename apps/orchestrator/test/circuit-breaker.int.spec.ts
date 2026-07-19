/**
 * Phase 5 — Test 1 (circuit breaker §10.6) ở tầng logic, Redis THẬT.
 *  - Chuỗi BLOCKED vượt ngưỡng trong cửa sổ → MỞ (status OPEN + retryAfter>0).
 *  - Hết cooldown → HALF_OPEN; kết quả thăm dò OK → ĐÓNG; thăm dò BLOCKED → MỞ lại.
 *  - Chưa đủ MIN_SAMPLES thì KHÔNG mở (tránh nhiễu).
 */
import { afterAll, beforeEach, describe, expect, it } from 'vitest';
import { Redis } from 'ioredis';
import { Platform, circuitKeys, createLogger } from '@fastcheck/shared';
import type { OrchestratorEnv } from '@fastcheck/config';
import { CircuitBreakerService } from '../src/circuit/circuit-breaker.service';
import { testEnv } from './helpers';

const PLATFORM = Platform.FACEBOOK; // cách ly khỏi test khác
const logger = createLogger({ name: 'test-circuit', level: 'error' });
const redis = new Redis(testEnv().REDIS_URL, { maxRetriesPerRequest: null });

function makeService(overrides: Partial<OrchestratorEnv>): CircuitBreakerService {
  const env = { ...testEnv(), CIRCUIT_MIN_SAMPLES: 3, CIRCUIT_COOLDOWN_SECONDS: 1, ...overrides };
  return new CircuitBreakerService(redis, env as OrchestratorEnv, logger);
}

async function clearKeys(): Promise<void> {
  const k = circuitKeys(PLATFORM);
  await redis.del(k.openUntil, k.total, k.bad);
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

beforeEach(clearKeys);
afterAll(async () => {
  await clearKeys();
  redis.disconnect();
});

describe('CircuitBreaker (§10.6)', () => {
  it('chuỗi BLOCKED vượt ngưỡng → MỞ + retryAfter>0', async () => {
    const cb = makeService({});
    expect((await cb.status(PLATFORM)).open).toBe(false);
    for (let i = 0; i < 3; i += 1) await cb.record(PLATFORM, true);
    const st = await cb.status(PLATFORM);
    expect(st.open).toBe(true);
    expect(st.state).toBe('OPEN');
    expect(st.retryAfterMs).toBeGreaterThan(0);
  });

  it('chưa đủ MIN_SAMPLES thì KHÔNG mở (dù toàn BLOCKED)', async () => {
    const cb = makeService({ CIRCUIT_MIN_SAMPLES: 10 });
    for (let i = 0; i < 3; i += 1) await cb.record(PLATFORM, true);
    expect((await cb.status(PLATFORM)).open).toBe(false);
  });

  it('không mở nếu tỷ lệ BLOCKED dưới ngưỡng', async () => {
    const cb = makeService({ CIRCUIT_BLOCK_THRESHOLD: 0.8 });
    // 3 mẫu: 1 blocked / 2 ok = 0.33 < 0.8 → không mở
    await cb.record(PLATFORM, true);
    await cb.record(PLATFORM, false);
    await cb.record(PLATFORM, false);
    expect((await cb.status(PLATFORM)).open).toBe(false);
  });

  it('hết cooldown → HALF_OPEN; thăm dò OK → ĐÓNG lại', async () => {
    const cb = makeService({});
    for (let i = 0; i < 3; i += 1) await cb.record(PLATFORM, true);
    expect((await cb.status(PLATFORM)).open).toBe(true);

    await sleep(1100); // qua cooldown 1s
    expect((await cb.status(PLATFORM)).state).toBe('HALF_OPEN');

    await cb.record(PLATFORM, false); // thăm dò thành công → đóng
    expect((await cb.status(PLATFORM)).state).toBe('CLOSED');
  });

  it('HALF_OPEN + thăm dò BLOCKED → MỞ lại', async () => {
    const cb = makeService({});
    for (let i = 0; i < 3; i += 1) await cb.record(PLATFORM, true);
    await sleep(1100);
    expect((await cb.status(PLATFORM)).state).toBe('HALF_OPEN');

    await cb.record(PLATFORM, true); // thăm dò vẫn bị block → mở lại
    expect((await cb.status(PLATFORM)).open).toBe(true);
  });
});
