import type { Redis } from 'ioredis';
import { UrlStatus } from '@fastcheck/shared';

export interface CachedResult {
  status: UrlStatus;
  checked_at: string;
}

/** Cache kết quả theo url_hash trong Redis (INV-5: mất cache → chậm, không sai). */
export class ResultCache {
  constructor(
    private readonly redis: Redis,
    private readonly ttlLiveSeconds: number,
    private readonly ttlDeadSeconds: number,
  ) {}

  private key(urlHash: string): string {
    return `fastcheck:result:${urlHash}`;
  }

  async get(urlHash: string): Promise<CachedResult | null> {
    const raw = await this.redis.get(this.key(urlHash));
    if (!raw) return null;
    return JSON.parse(raw) as CachedResult;
  }

  async set(urlHash: string, status: UrlStatus): Promise<void> {
    // INV-1: KHÔNG BAO GIỜ cache INCONCLUSIVE (lỗi tạm không được "đóng băng" thành kết quả).
    if (status === UrlStatus.INCONCLUSIVE) return;
    // TTL LIVE < TTL DEAD.
    const ttl = status === UrlStatus.LIVE ? this.ttlLiveSeconds : this.ttlDeadSeconds;
    await this.redis.set(
      this.key(urlHash),
      JSON.stringify({ status, checked_at: new Date().toISOString() }),
      'EX',
      ttl,
    );
  }
}
