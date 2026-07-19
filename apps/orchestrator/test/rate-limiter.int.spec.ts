/**
 * Test 5 (Phase 3) — Rate-limit token bucket với Redis THẬT (§4.1d, §8.1).
 *  - Vượt dung lượng → bị từ chối (job sẽ bị hoãn, không dùng dồn dập).
 *  - Refill theo THỜI GIAN: sau khi đủ thời gian, token hồi lại → cho phép tiếp.
 * Dùng `nowMs` tường minh để test tất định (không phụ thuộc sleep thật).
 */
import { afterAll, describe, expect, it } from 'vitest';
import type { Redis } from 'ioredis';
import { RateLimiter } from '../src/ratelimit/rate-limiter';
import { makeRedis } from './helpers';

const redis: Redis = makeRedis();
const limiter = new RateLimiter(redis);

afterAll(async () => {
  await redis.del('rl:test:burst', 'rl:test:refill');
  redis.disconnect();
});

describe('RateLimiter token bucket', () => {
  it('tiêu tối đa = capacity rồi bị từ chối (retryAfter > 0)', async () => {
    const key = 'rl:test:burst';
    await redis.del(key);
    const now = 1_000_000;
    // refill cực nhỏ → trong cùng mốc thời gian không hồi token.
    const r1 = await limiter.tryConsume(key, 3, 0.0001, now);
    const r2 = await limiter.tryConsume(key, 3, 0.0001, now);
    const r3 = await limiter.tryConsume(key, 3, 0.0001, now);
    const r4 = await limiter.tryConsume(key, 3, 0.0001, now);
    expect([r1.allowed, r2.allowed, r3.allowed]).toEqual([true, true, true]);
    expect(r4.allowed).toBe(false);
    expect(r4.retryAfterMs).toBeGreaterThan(0);
  });

  it('refill theo thời gian: hết token → chờ đủ lâu → cho phép lại', async () => {
    const key = 'rl:test:refill';
    await redis.del(key);
    // capacity 1, refill 10 token/giây → 1 token mỗi 100ms.
    const a = await limiter.tryConsume(key, 1, 10, 2_000_000);
    expect(a.allowed).toBe(true);
    const b = await limiter.tryConsume(key, 1, 10, 2_000_000); // ngay lập tức → hết token
    expect(b.allowed).toBe(false);
    expect(b.retryAfterMs).toBeGreaterThan(0);
    const c = await limiter.tryConsume(key, 1, 10, 2_000_150); // +150ms → đã hồi ≥1 token
    expect(c.allowed).toBe(true);
  });
});
