import type { Redis } from 'ioredis';

export interface RateLimitResult {
  allowed: boolean;
  /** Nếu bị từ chối: số ms tối thiểu chờ tới khi có 1 token (để hoãn job đúng nhịp, không dập dồn). */
  retryAfterMs: number;
}

/**
 * Token bucket ATOMIC trong Redis (spec §4.1d, §8.1) — giới hạn nhịp check theo platform và theo profile.
 * Key: `rl:{platform}` (toàn platform) và `rl:{platform}:{profile_id}` (từng profile — tránh dùng dồn dập,
 * skill §Health). Lua chạy trong Redis nên refill + tiêu token là một phép nguyên tử, không race giữa
 * nhiều orchestrator (INV-5: Redis là trí nhớ ngắn hạn, mất cache → chậm không sai).
 */
const TOKEN_BUCKET_LUA = `
local capacity = tonumber(ARGV[1])
local refill   = tonumber(ARGV[2])
local now      = tonumber(ARGV[3])
local ttl      = tonumber(ARGV[4])
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts     = tonumber(data[2])
if tokens == nil then tokens = capacity; ts = now end
local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + (elapsed / 1000.0) * refill)
local allowed = 0
local retry = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry = math.ceil(((1 - tokens) / refill) * 1000)
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], ttl)
return {allowed, retry}
`;

export class RateLimiter {
  constructor(private readonly redis: Redis) {}

  /**
   * Thử tiêu 1 token từ bucket `key` (dung lượng `capacity`, hồi `refillPerSec` token/giây).
   * `nowMs` cho phép test bơm thời gian; mặc định dùng đồng hồ thật.
   */
  async tryConsume(
    key: string,
    capacity: number,
    refillPerSec: number,
    nowMs: number = Date.now(),
  ): Promise<RateLimitResult> {
    // TTL bucket = thời gian nạp đầy lại từ rỗng ×2, tối thiểu 60s (dọn key nguội, không giữ mãi).
    const ttlMs = Math.max(60_000, Math.ceil((capacity / refillPerSec) * 1000) * 2);
    const res = (await this.redis.eval(
      TOKEN_BUCKET_LUA,
      1,
      key,
      capacity,
      refillPerSec,
      nowMs,
      ttlMs,
    )) as [number, number];
    return { allowed: res[0] === 1, retryAfterMs: res[1] };
  }
}
