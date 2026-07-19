import { randomUUID } from 'node:crypto';
import { Inject, Injectable } from '@nestjs/common';
import type { Redis } from 'ioredis';
import { circuitKeys, type CircuitState, type Logger, type Platform } from '@fastcheck/shared';
import type { OrchestratorEnv } from '@fastcheck/config';
import { ENV, LOGGER, REDIS } from '../tokens.js';

export interface CircuitStatus {
  state: CircuitState;
  open: boolean;
  retryAfterMs: number;
}

/**
 * Circuit breaker theo platform (§10.6). Tỷ lệ BLOCKED/lỗi trong CỬA SỔ TRƯỢT vượt ngưỡng → MỞ circuit
 * (API trả 503 + retry_after) để bảo vệ pool khỏi thiệt hại diện rộng. Sau cooldown → HALF_OPEN: kết quả
 * thăm dò kế tiếp quyết định ĐÓNG (hồi) hay MỞ lại. Khác DLQ (chặn job lẻ) — circuit chặn cả platform.
 *
 * Trạng thái ở Redis (trí nhớ ngắn hạn — INV-5): mất Redis → circuit reset về ĐÓNG (bảo vệ ít hơn, KHÔNG
 * bao giờ trả kết quả SAI). Cửa sổ trượt = ZSET theo timestamp; `open_until` = mốc còn MỞ.
 */
@Injectable()
export class CircuitBreakerService {
  constructor(
    @Inject(REDIS) private readonly redis: Redis,
    @Inject(ENV) private readonly env: OrchestratorEnv,
    @Inject(LOGGER) private readonly logger: Logger,
  ) {}

  /** Ghi kết quả một job vào cửa sổ trượt và cập nhật trạng thái circuit. `blocked` = profile bị siết/lỗi. */
  async record(platform: Platform, blocked: boolean): Promise<void> {
    const keys = circuitKeys(platform);
    const now = Date.now();
    const windowMs = this.env.CIRCUIT_WINDOW_SECONDS * 1000;
    const cooldownMs = this.env.CIRCUIT_COOLDOWN_SECONDS * 1000;

    const openUntil = Number((await this.redis.get(keys.openUntil)) ?? 0);
    if (openUntil > now) return; // OPEN: job mới bị chặn ở API; job đang chạy trả về thì bỏ qua (không đổi state)

    if (openUntil > 0) {
      // HALF_OPEN (đã hết cooldown, key còn): kết quả này là THĂM DÒ.
      if (blocked) await this.trip(keys, now + cooldownMs, platform, 'thăm dò half-open vẫn BLOCKED');
      else await this.close(keys, platform);
      return;
    }

    // CLOSED: cộng mẫu vào cửa sổ trượt rồi đánh giá ngưỡng.
    const member = `${now}-${randomUUID()}`;
    await this.redis.zadd(keys.total, now, member);
    if (blocked) await this.redis.zadd(keys.bad, now, member);
    const cutoff = now - windowMs;
    await this.redis.zremrangebyscore(keys.total, 0, cutoff);
    await this.redis.zremrangebyscore(keys.bad, 0, cutoff);
    await this.redis.pexpire(keys.total, windowMs);
    await this.redis.pexpire(keys.bad, windowMs);

    const total = await this.redis.zcount(keys.total, cutoff, '+inf');
    const bad = await this.redis.zcount(keys.bad, cutoff, '+inf');
    if (total >= this.env.CIRCUIT_MIN_SAMPLES && bad / total >= this.env.CIRCUIT_BLOCK_THRESHOLD) {
      await this.trip(keys, now + cooldownMs, platform, `${bad}/${total} BLOCKED vượt ngưỡng`);
    }
  }

  /** Đọc trạng thái (cho dashboard/metrics). open=true ⇒ còn trong cooldown. */
  async status(platform: Platform): Promise<CircuitStatus> {
    const openUntil = Number((await this.redis.get(circuitKeys(platform).openUntil)) ?? 0);
    const now = Date.now();
    if (openUntil > now) return { state: 'OPEN', open: true, retryAfterMs: openUntil - now };
    if (openUntil > 0) return { state: 'HALF_OPEN', open: false, retryAfterMs: 0 };
    return { state: 'CLOSED', open: false, retryAfterMs: 0 };
  }

  private async trip(
    keys: ReturnType<typeof circuitKeys>,
    openUntil: number,
    platform: Platform,
    reason: string,
  ): Promise<void> {
    const graceMs = this.env.CIRCUIT_COOLDOWN_SECONDS * 1000 + this.env.CIRCUIT_WINDOW_SECONDS * 1000;
    await this.redis.set(keys.openUntil, String(openUntil), 'PX', graceMs);
    // Reset cửa sổ để HALF_OPEN bắt đầu sạch (không dùng lại mẫu cũ đã làm mở).
    await this.redis.del(keys.total, keys.bad);
    this.logger.error(
      { alert: true, platform, reason, retry_after_s: this.env.CIRCUIT_COOLDOWN_SECONDS },
      'ALERT: circuit breaker MỞ — chặn platform để bảo vệ pool (§10.6)',
    );
  }

  private async close(keys: ReturnType<typeof circuitKeys>, platform: Platform): Promise<void> {
    await this.redis.del(keys.openUntil, keys.total, keys.bad);
    this.logger.info({ platform }, 'circuit breaker ĐÓNG lại — platform đã hồi (§10.6)');
  }
}
