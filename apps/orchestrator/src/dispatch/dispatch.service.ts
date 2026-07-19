import { randomUUID } from 'node:crypto';
import { Inject, Injectable } from '@nestjs/common';
import type { Channel, ConsumeMessage } from 'amqplib';
import type { Redis } from 'ioredis';
import { JobStatus, ProfileHealth, UrlStatus, type Logger, type Platform } from '@fastcheck/shared';
import {
  jobRepo,
  logRepo,
  profileRepo,
  proxyRepo,
  type CheckJob,
  type DB,
  type Profile,
} from '@fastcheck/db';
import { CookieCipher } from '@fastcheck/crypto';
import type { OrchestratorEnv } from '@fastcheck/config';
import {
  type CheckJobMessage,
  type JobResultMessage,
  type ServerCommand,
} from '@fastcheck/contracts';
import { COOKIE_CIPHER, DB_CONN, ENV, LOGGER, REDIS } from '../tokens.js';
import { StationRegistryService } from '../station-registry/station-registry.service.js';
import { RateLimiter } from '../ratelimit/rate-limiter.js';
import { JobPublisher } from './job-publisher.js';
import { CircuitBreakerService } from '../circuit/circuit-breaker.service.js';
import { MetricsService } from '../metrics/metrics.service.js';

/** Ngữ cảnh để ack đúng message RabbitMQ khi job hoàn tất (manual ack — INV-4/INV-10). */
export interface AckContext {
  channel: Channel;
  msg: ConsumeMessage;
}

interface PendingJob {
  ack: AckContext;
  job_id: string;
  trace_id: string;
  profile_id: string;
  station_id: string;
  url_hash: string;
  target_url: string;
  platform: Platform;
  retry_count: number;
}

/**
 * Điều phối job + auto-switch (spec §4.6, §6.6; skill profile-lifecycle):
 *  - dispatch: rate-limit (§4.1d) → claim profile atomic (INV-11) → chọn station còn slot →
 *    giải mã cookie (packages/crypto, INV-12) → WS RUN.
 *  - handleResult: ghi check_logs (url_status TÁCH BIỆT profile_health — INV-3).
 *      * Thành công (profile OK + kết quả chắc chắn) → markDone + cache + hồi health, trả profile.
 *      * Lỗi profile (BLOCKED/CHALLENGED/THROTTLED) → COOLDOWN/DEAD (+ xoay proxy nếu nghi), re-queue.
 *      * INCONCLUSIVE nhưng profile khoẻ → trả profile bình thường, re-queue (thử profile/lần khác).
 *      * Vượt max_retries → DLQ + DEAD_LETTER + ALERT (chống switch vô hạn).
 *  command_id nối RUN ↔ result (idempotent, INV-14); ack chỉ sau khi xử lý xong (INV-4).
 */
@Injectable()
export class DispatchService {
  private readonly pending = new Map<string, PendingJob>();

  constructor(
    @Inject(DB_CONN) private readonly db: DB,
    @Inject(LOGGER) private readonly logger: Logger,
    @Inject(ENV) private readonly env: OrchestratorEnv,
    @Inject(REDIS) private readonly redis: Redis,
    @Inject(COOKIE_CIPHER) private readonly cipher: CookieCipher,
    private readonly registry: StationRegistryService,
    private readonly rateLimiter: RateLimiter,
    private readonly publisher: JobPublisher,
    private readonly circuitBreaker: CircuitBreakerService,
    private readonly metrics: MetricsService,
  ) {}

  /**
   * Thử điều phối một job xuống một station. Trả `true` nếu đã gửi RUN (ack khi có kết quả);
   * `false` nếu bị bóp nhịp/không còn station/profile rảnh (caller requeue có trễ để thử lại).
   */
  async dispatch(job: CheckJobMessage, ack: AckContext): Promise<boolean> {
    // INV-4: check_jobs là NGUỒN SỰ THẬT. Message mồ côi (job đã DONE/xoá, hoặc đang RUNNING vì đã
    // dispatch) → ACK bỏ, KHÔNG dispatch. Chống double-dispatch + chống message cũ từ lần chạy trước
    // (queue có thể còn message rác) làm hỏng trạng thái / crash khi ghi log job không tồn tại.
    const current = await jobRepo.getJobById(this.db, job.job_id);
    if (!current || current.status !== JobStatus.PENDING) {
      this.logger.warn(
        { trace_id: job.trace_id, job_id: job.job_id, status: current?.status ?? 'MISSING' },
        'job không còn PENDING trong check_jobs — bỏ message mồ côi (INV-4)',
      );
      ack.channel.ack(ack.msg);
      return true; // đã ack tại chỗ; consumer không requeue, không chờ kết quả
    }

    // Rate-limit theo PLATFORM trước khi tiêu một profile (§4.1d, §8.1). Bị bóp → requeue.
    const plat = await this.rateLimiter.tryConsume(
      `rl:${job.platform}`,
      this.env.RATE_LIMIT_PLATFORM_CAPACITY,
      this.env.RATE_LIMIT_PLATFORM_REFILL_PER_SEC,
    );
    if (!plat.allowed) {
      this.logger.warn(
        { trace_id: job.trace_id, platform: job.platform, retryAfterMs: plat.retryAfterMs },
        'rate-limit platform — hoãn job',
      );
      return false;
    }

    const station = this.registry.pickAvailableStation();
    if (!station) {
      this.logger.warn({ trace_id: job.trace_id }, 'không có station còn slot — requeue');
      return false;
    }

    let profile: Profile | null = null;
    try {
      profile = await profileRepo.claimProfile(
        this.db,
        job.platform,
        station.station_id,
        this.env.LEASE_MINUTES,
      );
      if (!profile) {
        this.logger.warn(
          { trace_id: job.trace_id, platform: job.platform },
          'pool cạn profile cho platform — requeue',
        );
        this.registry.releaseSlot(station.station_id);
        return false;
      }

      // Rate-limit theo PROFILE (tránh dùng dồn dập — skill §Health). Bị bóp → trả profile + requeue.
      const perProfile = await this.rateLimiter.tryConsume(
        `rl:${job.platform}:${profile.id}`,
        this.env.RATE_LIMIT_PROFILE_CAPACITY,
        this.env.RATE_LIMIT_PROFILE_REFILL_PER_SEC,
      );
      if (!perProfile.allowed) {
        this.logger.warn(
          { trace_id: job.trace_id, profile_id: profile.id, retryAfterMs: perProfile.retryAfterMs },
          'rate-limit profile — hoãn job',
        );
        await profileRepo.releaseProfile(this.db, profile.id);
        this.registry.releaseSlot(station.station_id);
        return false;
      }

      // check_jobs là nguồn sự thật: RUNNING + cột dispatch trước khi gửi lệnh (INV-4/INV-15).
      await jobRepo.markRunning(this.db, {
        job_id: job.job_id,
        station_id: station.station_id,
        profile_id: profile.id,
      });

      const cookie = this.decryptCookie(profile);
      const command: ServerCommand = {
        type: 'command',
        command_id: randomUUID(),
        command: {
          name: 'script.run',
          trace_id: job.trace_id,
          job_id: job.job_id,
          target_url: job.target_url,
          platform: job.platform,
          profile_id: profile.id,
          // id phía GemLogin để worker real mode mở đúng browser (null ở fake mode — worker bỏ qua).
          gemlogin_profile_id: profile.gemlogin_profile_id,
          cookie, // giải mã ngay trước khi gửi; KHÔNG log (INV-12)
        },
      };

      const sent = this.registry.send(station.station_id, command);
      if (!sent) {
        await profileRepo.releaseProfile(this.db, profile.id);
        this.registry.releaseSlot(station.station_id);
        this.logger.warn({ trace_id: job.trace_id }, 'station rớt khi gửi RUN — requeue');
        return false;
      }

      this.pending.set(command.command_id, {
        ack,
        job_id: job.job_id,
        trace_id: job.trace_id,
        profile_id: profile.id,
        station_id: station.station_id,
        url_hash: job.url_hash,
        target_url: job.target_url,
        platform: job.platform,
        retry_count: job.retry_count,
      });
      this.logger.info(
        {
          trace_id: job.trace_id,
          station_id: station.station_id,
          command_id: command.command_id,
          retry_count: job.retry_count,
        },
        'đã gửi RUN xuống station',
      );
      return true;
    } catch (err) {
      this.registry.releaseSlot(station.station_id);
      if (profile) await profileRepo.releaseProfile(this.db, profile.id).catch(() => undefined);
      this.logger.error(
        { trace_id: job.trace_id, err: (err as Error).message },
        'lỗi khi dispatch — requeue',
      );
      return false;
    }
  }

  /** Nhận kết quả từ station: ghi log; thành công→chốt, ngược lại→auto-switch/re-queue/DLQ; rồi ack. */
  async handleResult(result: JobResultMessage): Promise<void> {
    const pending = this.pending.get(result.command_id);
    if (!pending) {
      this.logger.warn({ command_id: result.command_id }, 'job_result không khớp pending — bỏ qua');
      return;
    }
    this.pending.delete(result.command_id);

    try {
      // INV-3: url_status (TARGET) và profile_health (PROFILE) ghi RIÊNG BIỆT — LUÔN ghi mỗi lần thử.
      await logRepo.insertCheckLog(this.db, {
        trace_id: pending.trace_id,
        job_id: pending.job_id,
        profile_id: pending.profile_id,
        target_url: pending.target_url,
        url_status: result.url_status,
        profile_health: result.profile_health,
        block_reason: result.block_reason ?? null,
        response_time_ms: result.response_time_ms ?? null,
      });

      const profileHealthy = result.profile_health === ProfileHealth.OK;
      // Circuit breaker chỉ đếm PLATFORM chặn ta (CHALLENGED/BLOCKED). THROTTLED = lỗi HẠ TẦNG phía ta
      // (GemLogin kẹt) — KHÔNG phải platform chặn → KHÔNG được làm mở circuit oan (§10.6).
      const platformBlocking =
        result.profile_health === ProfileHealth.CHALLENGED ||
        result.profile_health === ProfileHealth.BLOCKED;

      // Metric + circuit breaker (§10.4/§10.6). Ghi vào cửa sổ trượt; vượt ngưỡng → mở circuit (API trả 503).
      this.metrics.recordResult(
        pending.platform,
        result.url_status,
        result.profile_health,
        result.response_time_ms ?? null,
      );
      await this.circuitBreaker.record(pending.platform, platformBlocking);

      const decisive = result.url_status !== UrlStatus.INCONCLUSIVE;

      if (profileHealthy && decisive) {
        await this.completeSuccess(pending, result.url_status);
        return;
      }

      await this.autoSwitch(pending, result);
    } finally {
      // Ack chỉ sau khi đã xử lý xong (manual ack — INV-4/INV-10). Giải phóng slot station.
      this.registry.releaseSlot(pending.station_id);
      pending.ack.channel.ack(pending.ack.msg);
    }
  }

  /** Kết quả chắc chắn + profile khoẻ: chốt job, cache, hồi health, trả profile về pool. */
  private async completeSuccess(pending: PendingJob, status: UrlStatus): Promise<void> {
    await jobRepo.markDone(this.db, pending.job_id, status);
    await this.setCache(pending.url_hash, status);
    await profileRepo.recordSuccess(this.db, pending.profile_id, this.env.PROFILE_HEALTH_BUMP);
    this.logger.info(
      { trace_id: pending.trace_id, job_id: pending.job_id, url_status: status, cached: true },
      'job hoàn tất (thành công) — chốt + cache + hồi health',
    );
  }

  /**
   * Auto-switch (§4.6): hạ cấp profile nếu lỗi profile (COOLDOWN/DEAD + xoay proxy nếu nghi),
   * hoặc trả profile bình thường nếu chỉ INCONCLUSIVE do target. Rồi re-queue với backoff, hoặc DLQ
   * nếu vượt max_retries. KHÔNG cache (INV-1) và KHÔNG chốt DONE cho các nhánh này.
   */
  private async autoSwitch(pending: PendingJob, result: JobResultMessage): Promise<void> {
    if (result.profile_health === ProfileHealth.THROTTLED) {
      // Lỗi HẠ TẦNG (browser mở không được / GemLogin kẹt) — KHÔNG phải lỗi tài khoản: nghỉ NGẮN để GemLogin
      // hồi + cắt vòng hammer (claimProfile bỏ qua profile cooldown), KHÔNG phạt health/không DEAD. Tự AVAILABLE.
      await profileRepo.cooldownProfile(
        this.db,
        pending.profile_id,
        this.env.PROFILE_THROTTLE_COOLDOWN_SECONDS,
      );
      this.logger.warn(
        {
          trace_id: pending.trace_id,
          profile_id: pending.profile_id,
          cooldown_s: this.env.PROFILE_THROTTLE_COOLDOWN_SECONDS,
          reason: result.block_reason,
        },
        'THROTTLED (lỗi hạ tầng mở browser): nghỉ ngắn cho profile — cắt vòng hammer, không kết tội tài khoản',
      );
    } else if (result.profile_health !== ProfileHealth.OK) {
      // Lỗi PROFILE thật (CHALLENGED/BLOCKED): hạ cấp có phạt health + ngưỡng DEAD (+ xoay proxy nếu BLOCKED).
      const updated = await profileRepo.recordFailure(this.db, {
        profileId: pending.profile_id,
        healthPenalty: this.env.PROFILE_HEALTH_PENALTY,
        cooldownSeconds: this.env.PROFILE_COOLDOWN_SECONDS,
        deadThreshold: this.env.PROFILE_DEAD_THRESHOLD,
      });
      if (result.profile_health === ProfileHealth.BLOCKED) {
        await this.maybeRotateProxy(pending.profile_id);
      }
      this.logger.warn(
        {
          trace_id: pending.trace_id,
          profile_id: pending.profile_id,
          profile_health: result.profile_health,
          new_status: updated?.status,
          consecutive_fails: updated?.consecutive_fails,
        },
        'auto-switch: hạ cấp profile (COOLDOWN/DEAD)',
      );
    } else {
      // INCONCLUSIVE nhưng profile khoẻ (vd selector vỡ / tạm thời): không phạt profile, trả về pool.
      await profileRepo.releaseProfile(this.db, pending.profile_id);
    }

    const nextRetry = pending.retry_count + 1;
    const retryMsg: CheckJobMessage = {
      trace_id: pending.trace_id,
      job_id: pending.job_id,
      target_url: pending.target_url,
      url_hash: pending.url_hash,
      platform: pending.platform,
      retry_count: nextRetry,
    };

    if (nextRetry > this.env.ORCHESTRATOR_MAX_RETRIES) {
      // Chống switch vô hạn: DLQ + DEAD_LETTER + ALERT (skill §auto-switch).
      await jobRepo.markDeadLetter(this.db, pending.job_id);
      await this.publisher.publishDlq(retryMsg);
      this.logger.error(
        {
          alert: true,
          trace_id: pending.trace_id,
          job_id: pending.job_id,
          retries: pending.retry_count,
          last_health: result.profile_health,
          last_url_status: result.url_status,
        },
        'ALERT: job vào DLQ sau khi vượt max_retries (DEAD_LETTER)',
      );
      return;
    }

    // Re-queue với backoff: expiration = base * 2^retry_count (cap). Message chờ ở job.retry rồi
    // dead-letter quay lại job.pending (backoff, không dập liên tục vào profile/proxy đang lỗi).
    const backoffMs = Math.min(
      this.env.RETRY_BACKOFF_MAX_MS,
      this.env.RETRY_BACKOFF_BASE_MS * 2 ** pending.retry_count,
    );
    await jobRepo.markRetrying(this.db, pending.job_id, nextRetry);
    await this.publisher.publishRetry(retryMsg, backoffMs);

    // Cảnh báo pool thấp (§4.6) — dấu hiệu sắp không còn profile để switch.
    const available = await profileRepo.countAvailable(this.db, pending.platform);
    if (available <= this.env.PROFILE_POOL_LOW_WATERMARK) {
      this.logger.warn(
        { alert: true, platform: pending.platform, available },
        'ALERT: pool profile xuống thấp',
      );
    }
    this.logger.info(
      { trace_id: pending.trace_id, job_id: pending.job_id, nextRetry, backoffMs, available },
      'auto-switch: re-queue job (backoff)',
    );
  }

  /** Nghi proxy khi BLOCKED: tăng fail_count; nếu profile có proxy khác ACTIVE → xoay cho phiên sau (INV-7). */
  private async maybeRotateProxy(profileId: string): Promise<void> {
    const profile = await profileRepo.getProfile(this.db, profileId);
    if (!profile?.proxy_id) return;
    await proxyRepo.noteProxyFailure(this.db, profile.proxy_id, this.env.PROXY_BAN_THRESHOLD);
    const newProxy = await proxyRepo.rotateProfileProxy(this.db, profileId);
    if (newProxy) {
      this.logger.info(
        { profile_id: profileId, new_proxy_id: newProxy },
        'nghi proxy → xoay proxy cho profile (phiên sau, INV-7)',
      );
    }
  }

  private decryptCookie(profile: Profile): string {
    if (!profile.cookie_ciphertext || !profile.cookie_key_id) return ''; // fake mode: có thể trống
    return this.cipher.decrypt({
      ciphertext: Buffer.from(profile.cookie_ciphertext),
      keyId: profile.cookie_key_id,
    });
  }

  /**
   * Refresh cookie sau phiên login thành công (spec §4.4): worker gửi cookie MỚI (plaintext, qua WSS) →
   * orchestrator MÃ HOÁ (packages/crypto, INV-12) rồi lưu vào profiles. Worker KHÔNG tự mã hoá (ADR-0006).
   * KHÔNG log giá trị cookie — chỉ profile_id.
   */
  async refreshCookie(msg: { profile_id: string; cookie: string }): Promise<void> {
    const enc = this.cipher.encrypt(msg.cookie);
    await profileRepo.updateCookie(this.db, msg.profile_id, enc.ciphertext, enc.keyId);
    this.logger.info({ profile_id: msg.profile_id }, 'cookie refreshed (mã hoá & lưu — INV-12)');
  }

  private async setCache(urlHash: string, status: UrlStatus): Promise<void> {
    if (status === UrlStatus.INCONCLUSIVE) return; // INV-1
    const ttl =
      status === UrlStatus.LIVE
        ? this.env.RESULT_CACHE_TTL_LIVE_SECONDS // LIVE ngắn hơn DEAD
        : this.env.RESULT_CACHE_TTL_DEAD_SECONDS;
    await this.redis.set(
      `fastcheck:result:${urlHash}`,
      JSON.stringify({ status, checked_at: new Date().toISOString() }),
      'EX',
      ttl,
    );
  }

  // ── Thu hồi job khi station chết (INV-15) ───────────────────────────────────

  /**
   * Station quá hạn heartbeat / rớt kết nối → thu hồi mọi job RUNNING gắn station đó: trả profile về pool,
   * đưa check_jobs về PENDING (clear cột dispatch), re-queue. Tìm theo `assigned_station_id` trong
   * check_jobs (nguồn sự thật — INV-4/INV-15), KHÔNG dựa registry RAM.
   */
  async recoverStationJobs(stationId: string): Promise<number> {
    const jobs = await jobRepo.findRunningByStation(this.db, stationId);
    return this.reclaim(jobs, 'station-offline');
  }

  /**
   * Startup sweep (INV-15): orchestrator vừa khởi động không còn phiên nào trong RAM → mọi job RUNNING là
   * mồ côi (station sẽ trả job_result với command_id lạ → bị bỏ). Thu hồi tất cả nhờ cột dispatch để
   * không mất job khi orchestrator restart giữa chừng. Gọi sau khi consumer đã bind queue + publisher sẵn sàng.
   */
  async recoverOrphanRunning(): Promise<number> {
    await this.publisher.waitReady();
    const jobs = await jobRepo.findAllRunning(this.db);
    if (jobs.length > 0) {
      this.logger.warn(
        { count: jobs.length },
        'startup sweep: thu hồi job RUNNING mồ côi sau restart (INV-15)',
      );
    }
    return this.reclaim(jobs, 'startup-sweep');
  }

  /**
   * Trả profile + đưa job về PENDING + re-queue cho từng job. GIỮ NGUYÊN retry_count: station chết là
   * lỗi HẠ TẦNG, không phải lỗi profile/target → không tiêu ngân sách retry của job (khác auto-switch §4.6).
   */
  private async reclaim(jobs: CheckJob[], reason: string): Promise<number> {
    for (const job of jobs) {
      // Nếu orchestrator còn sống và đang giữ message RabbitMQ của job này: ack để bỏ (ta re-queue bản mới).
      for (const [commandId, p] of this.pending) {
        if (p.job_id === job.id) {
          try {
            p.ack.channel.ack(p.ack.msg);
          } catch {
            /* kênh có thể đã đóng — bỏ qua, message sẽ tự requeue khi kết nối rớt */
          }
          this.pending.delete(commandId);
        }
      }
      if (job.assigned_profile_id) {
        await profileRepo.releaseProfile(this.db, job.assigned_profile_id).catch((err: unknown) => {
          this.logger.warn(
            { job_id: job.id, err: (err as Error).message },
            'thu hồi: trả profile lỗi (bỏ qua, lease reaper sẽ dọn)',
          );
        });
      }
      await jobRepo.markRetrying(this.db, job.id, job.retry_count);
      await this.publisher.publishPending({
        trace_id: job.trace_id,
        job_id: job.id,
        target_url: job.target_url,
        url_hash: job.url_hash,
        platform: job.platform,
        retry_count: job.retry_count,
      });
      this.logger.warn(
        { trace_id: job.trace_id, job_id: job.id, reason },
        'INV-15: thu hồi + re-queue job của station chết',
      );
    }
    return jobs.length;
  }
}
