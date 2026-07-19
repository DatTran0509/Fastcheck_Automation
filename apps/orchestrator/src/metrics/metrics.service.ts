import {
  Inject,
  Injectable,
  type OnApplicationBootstrap,
  type OnModuleDestroy,
} from '@nestjs/common';
import {
  Counter,
  Gauge,
  Histogram,
  Registry,
  collectDefaultMetrics,
} from 'prom-client';
import {
  Platform,
  type Logger,
  type ProfileHealth,
  type UrlStatus,
} from '@fastcheck/shared';
import { profileRepo, proxyRepo, type DB } from '@fastcheck/db';
import type { OrchestratorEnv } from '@fastcheck/config';
import { DB_CONN, ENV, LOGGER } from '../tokens.js';
import { StationRegistryService } from '../station-registry/station-registry.service.js';
import { CircuitBreakerService } from '../circuit/circuit-breaker.service.js';

/**
 * Metric Prometheus (spec §10.4, tech-stack observability). Phơi ở `/metrics`:
 * tỷ lệ LIVE/DEAD/INCONCLUSIVE + BLOCKED theo platform, p95 latency, độ sâu queue, số profile theo trạng
 * thái, fail_count theo proxy, RAM/CPU worker, station load, circuit open. KHÔNG ELK (phân kỳ observability).
 *
 * Counter/histogram cập nhật INLINE khi có kết quả (chính xác tức thì); gauge lấy từ DB/registry cập nhật
 * ĐỊNH KỲ (refresh) để scrape luôn có số mới nhất. KHÔNG log/expose cookie (INV-12).
 */
@Injectable()
export class MetricsService implements OnApplicationBootstrap, OnModuleDestroy {
  private readonly registry = new Registry();
  private timer?: NodeJS.Timeout;

  private readonly resultTotal = new Counter({
    name: 'fastcheck_check_result_total',
    help: 'Số kết quả check theo platform + url_status (LIVE/DEAD/INCONCLUSIVE — TÁCH BIỆT, INV-1)',
    labelNames: ['platform', 'url_status'] as const,
    registers: [this.registry],
  });

  private readonly healthTotal = new Counter({
    name: 'fastcheck_profile_health_total',
    help: 'Số kết quả theo platform + profile_health (BLOCKED/CHALLENGED/... — INV-3)',
    labelNames: ['platform', 'profile_health'] as const,
    registers: [this.registry],
  });

  private readonly duration = new Histogram({
    name: 'fastcheck_check_duration_ms',
    help: 'Thời gian check (ms) theo platform — dùng tính p95',
    labelNames: ['platform'] as const,
    buckets: [50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000, 60000, 120000],
    registers: [this.registry],
  });

  private readonly queueMessages = new Gauge({
    name: 'fastcheck_queue_messages',
    help: 'Độ sâu queue RabbitMQ (backpressure)',
    labelNames: ['queue'] as const,
    registers: [this.registry],
  });

  private readonly profilesGauge = new Gauge({
    name: 'fastcheck_profiles',
    help: 'Số profile theo platform + trạng thái pool',
    labelNames: ['platform', 'status'] as const,
    registers: [this.registry],
  });

  private readonly proxyFail = new Gauge({
    name: 'fastcheck_proxy_fail_count',
    help: 'fail_count theo proxy (proxy chết = INCONCLUSIVE/BLOCKED hàng loạt)',
    labelNames: ['proxy_id'] as const,
    registers: [this.registry],
  });

  private readonly stationLoad = new Gauge({
    name: 'fastcheck_station_current_load',
    help: 'Tải hiện tại của station',
    labelNames: ['station'] as const,
    registers: [this.registry],
  });

  private readonly workerRam = new Gauge({
    name: 'fastcheck_worker_ram_mb',
    help: 'RAM (MB) máy trạm worker (từ heartbeat)',
    labelNames: ['station'] as const,
    registers: [this.registry],
  });

  private readonly workerCpu = new Gauge({
    name: 'fastcheck_worker_cpu_percent',
    help: 'CPU (%) máy trạm worker (từ heartbeat)',
    labelNames: ['station'] as const,
    registers: [this.registry],
  });

  private readonly circuitOpen = new Gauge({
    name: 'fastcheck_circuit_open',
    help: 'Circuit breaker theo platform: 1=MỞ (đang chặn), 0=đóng',
    labelNames: ['platform'] as const,
    registers: [this.registry],
  });

  constructor(
    @Inject(DB_CONN) private readonly db: DB,
    @Inject(ENV) private readonly env: OrchestratorEnv,
    @Inject(LOGGER) private readonly logger: Logger,
    private readonly stationRegistry: StationRegistryService,
    private readonly circuitBreaker: CircuitBreakerService,
  ) {
    collectDefaultMetrics({ register: this.registry, prefix: 'fastcheck_orchestrator_' });
  }

  onApplicationBootstrap(): void {
    this.timer = setInterval(() => void this.refresh(), this.env.QUEUE_METRICS_INTERVAL_MS);
  }

  /** Cập nhật inline khi có kết quả check (counter + histogram chính xác tức thì). */
  recordResult(
    platform: Platform,
    urlStatus: UrlStatus,
    profileHealth: ProfileHealth,
    durationMs: number | null,
  ): void {
    this.resultTotal.inc({ platform, url_status: urlStatus });
    this.healthTotal.inc({ platform, profile_health: profileHealth });
    if (durationMs != null) this.duration.observe({ platform }, durationMs);
  }

  /** Consumer đẩy độ sâu queue vào gauge (pull sẽ cần amqp trong metrics — đơn giản hoá bằng push). */
  setQueueDepth(queue: string, messages: number): void {
    this.queueMessages.set({ queue }, messages);
  }

  /** Làm mới các gauge lấy từ DB/registry. Bọc lỗi để scrape không bao giờ làm sập tiến trình. */
  async refresh(): Promise<void> {
    try {
      this.profilesGauge.reset();
      for (const row of await profileRepo.countByStatusAll(this.db)) {
        this.profilesGauge.set({ platform: row.platform, status: row.status }, row.count);
      }

      this.proxyFail.reset();
      for (const p of await proxyRepo.listProxies(this.db)) {
        this.proxyFail.set({ proxy_id: p.id }, p.fail_count);
      }

      this.stationLoad.reset();
      this.workerRam.reset();
      this.workerCpu.reset();
      for (const s of this.stationRegistry.list()) {
        this.stationLoad.set({ station: s.station_id }, s.current_load);
        if (s.ram_mb != null) this.workerRam.set({ station: s.station_id }, s.ram_mb);
        if (s.cpu_percent != null) this.workerCpu.set({ station: s.station_id }, s.cpu_percent);
      }

      this.circuitOpen.reset();
      for (const platform of Object.values(Platform)) {
        const st = await this.circuitBreaker.status(platform);
        this.circuitOpen.set({ platform }, st.open ? 1 : 0);
      }
    } catch (err) {
      this.logger.error({ err: (err as Error).message }, 'refresh metrics lỗi (bỏ qua vòng này)');
    }
  }

  contentType(): string {
    return this.registry.contentType;
  }

  async metricsText(): Promise<string> {
    await this.refresh(); // đảm bảo gauge tươi ngay lúc scrape
    return this.registry.metrics();
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
  }
}
