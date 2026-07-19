import {
  Inject,
  Injectable,
  type OnApplicationBootstrap,
  type OnModuleDestroy,
} from '@nestjs/common';
import { profileRepo, type DB } from '@fastcheck/db';
import type { Logger } from '@fastcheck/shared';
import type { OrchestratorEnv } from '@fastcheck/config';
import { DB_CONN, ENV, LOGGER } from '../tokens.js';

/**
 * Cron dọn lease (spec §6.4, INV-11): mỗi phút trả các profile `IN_USE` quá `lease_expires_at`
 * (worker treo, không kịp trả) về `AVAILABLE`. Van an toàn chống kẹt pool — job không kẹt vĩnh viễn.
 */
@Injectable()
export class LeaseReaperService implements OnApplicationBootstrap, OnModuleDestroy {
  private timer?: NodeJS.Timeout;

  constructor(
    @Inject(DB_CONN) private readonly db: DB,
    @Inject(LOGGER) private readonly logger: Logger,
    @Inject(ENV) private readonly env: OrchestratorEnv,
  ) {}

  onApplicationBootstrap(): void {
    this.timer = setInterval(() => void this.reap(), this.env.LEASE_REAP_INTERVAL_MS);
    this.logger.info(
      { intervalMs: this.env.LEASE_REAP_INTERVAL_MS },
      'cron dọn lease đã bật (INV-11)',
    );
  }

  /** Public để test gọi trực tiếp (không phải chờ interval). */
  async reap(): Promise<number> {
    try {
      const leased = await profileRepo.reapExpiredLeases(this.db);
      if (leased > 0) {
        this.logger.info({ reaped: leased }, 'cron dọn lease: trả profile IN_USE quá hạn về AVAILABLE');
      }
      // Dọn COOLDOWN hết hạn → AVAILABLE (nếu bỏ, COOLDOWN kẹt vĩnh viễn vì claim chỉ lấy AVAILABLE).
      const cooled = await profileRepo.reapExpiredCooldowns(this.db);
      if (cooled > 0) {
        this.logger.info({ reaped: cooled }, 'cron dọn cooldown: trả profile COOLDOWN hết hạn về AVAILABLE');
      }
      return leased + cooled;
    } catch (err) {
      // Không nuốt lỗi (error-handling rule): log rõ, để lần chạy sau thử lại.
      this.logger.error({ err: (err as Error).message }, 'cron dọn lease/cooldown lỗi');
      return 0;
    }
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
  }
}
