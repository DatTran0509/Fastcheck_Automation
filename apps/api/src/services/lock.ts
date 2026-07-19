import type { Redis } from 'ioredis';

/**
 * Khoá chống stampede (spec §6.2, job-lifecycle §2): `SET lock:{url_hash} NX EX 10`.
 * Khi 100 request cùng một URL ập tới lúc cache miss, chỉ request GIỮ được khoá mới tạo job +
 * đẩy queue; các request khác đọc job hiện có. Chốt cuối chống trùng vẫn là UNIQUE(url_hash)
 * partial ở check_jobs (INV-13) — khoá chỉ giảm tải, không phải nguồn sự thật (INV-5).
 */
export class StampedeLock {
  constructor(
    private readonly redis: Redis,
    private readonly ttlSeconds = 10,
  ) {}

  private key(urlHash: string): string {
    return `lock:${urlHash}`;
  }

  /** Trả `true` nếu giành được khoá (chưa ai giữ). `SET NX EX` là atomic. */
  async acquire(urlHash: string): Promise<boolean> {
    const res = await this.redis.set(this.key(urlHash), '1', 'EX', this.ttlSeconds, 'NX');
    return res === 'OK';
  }

  /** Nhả khoá sớm khi đã tạo xong job (không phải chờ hết TTL). Mất Redis → tự hết hạn (INV-5). */
  async release(urlHash: string): Promise<void> {
    await this.redis.del(this.key(urlHash));
  }
}
