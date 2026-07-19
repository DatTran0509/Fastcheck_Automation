import {
  Inject,
  Injectable,
  type OnApplicationBootstrap,
  type OnModuleDestroy,
} from '@nestjs/common';
import type { Logger } from '@fastcheck/shared';
import type { OrchestratorEnv } from '@fastcheck/config';
import { ENV, LOGGER } from '../tokens.js';
import { StationRegistryService } from '../station-registry/station-registry.service.js';
import { DispatchService } from '../dispatch/dispatch.service.js';

/**
 * Phát hiện station chết bằng NGƯỠNG heartbeat và thu hồi job (INV-15). Station ping ~10s; quá
 * `HEARTBEAT_TIMEOUT_MS` không ping → OFFLINE → tìm mọi check_jobs RUNNING của station → re-queue +
 * trả profile + clear cột dispatch (DispatchService.recoverStationJobs).
 *
 * Bắt cả trường hợp worker TREO mà socket vẫn mở (socket-close ở WS gateway không bắn). Idempotent:
 * `takeStale` chỉ trả station lần đầu vượt ngưỡng (đã lật OFFLINE) nên không thu hồi lặp.
 */
@Injectable()
export class StationMonitorService implements OnApplicationBootstrap, OnModuleDestroy {
  private timer?: NodeJS.Timeout;

  constructor(
    @Inject(ENV) private readonly env: OrchestratorEnv,
    @Inject(LOGGER) private readonly logger: Logger,
    private readonly registry: StationRegistryService,
    private readonly dispatch: DispatchService,
  ) {}

  onApplicationBootstrap(): void {
    this.timer = setInterval(() => void this.sweep(), this.env.STATION_MONITOR_INTERVAL_MS);
    this.logger.info(
      {
        intervalMs: this.env.STATION_MONITOR_INTERVAL_MS,
        timeoutMs: this.env.HEARTBEAT_TIMEOUT_MS,
      },
      'cron giám sát station đã bật (phát hiện chết theo heartbeat — INV-15)',
    );
  }

  /** Public để test gọi trực tiếp. Trả tổng số job đã thu hồi. */
  async sweep(): Promise<number> {
    try {
      const stale = this.registry.takeStale(this.env.HEARTBEAT_TIMEOUT_MS);
      let recovered = 0;
      for (const stationId of stale) {
        this.logger.warn(
          { station_id: stationId, timeoutMs: this.env.HEARTBEAT_TIMEOUT_MS },
          'station quá hạn heartbeat → OFFLINE + thu hồi job (INV-15)',
        );
        await this.registry.markOffline(stationId);
        recovered += await this.dispatch.recoverStationJobs(stationId);
      }
      return recovered;
    } catch (err) {
      // Không nuốt lỗi: log rõ, lần quét sau thử lại (van an toàn không được im lặng).
      this.logger.error({ err: (err as Error).message }, 'cron giám sát station lỗi');
      return 0;
    }
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
  }
}
