import { Inject, Injectable } from '@nestjs/common';
import { Platform, ProfileStatus } from '@fastcheck/shared';
import { jobRepo, logRepo, profileRepo, type DB } from '@fastcheck/db';
import type { OrchestratorEnv } from '@fastcheck/config';
import type {
  DashboardAlert,
  DashboardCircuit,
  DashboardJob,
  DashboardProgress,
  DashboardRatio,
  DashboardSnapshot,
  DashboardStation,
  JobProgressMessage,
} from '@fastcheck/contracts';
import { DB_CONN, ENV } from '../tokens.js';
import { StationRegistryService } from '../station-registry/station-registry.service.js';
import { CircuitBreakerService } from '../circuit/circuit-breaker.service.js';

/**
 * Dựng snapshot realtime cho dashboard (§6.9). Chỉ dữ liệu VẬN HÀNH — TUYỆT ĐỐI không cookie/credential
 * (INV-12). Ba trạng thái LIVE/DEAD/INCONCLUSIVE hiển thị TÁCH BIỆT (INV-1/INV-3). Dữ liệu lấy từ nguồn
 * sự thật (Postgres) + registry realtime + circuit state (Redis).
 */
@Injectable()
export class DashboardService {
  // Buffer vòng các sự kiện tiến trình gần nhất (§8 stream). Chỉ trong bộ nhớ — dashboard là thông tin
  // vận hành, KHÔNG phải nguồn sự thật (nguồn sự thật vòng đời job là check_jobs — INV-4).
  private readonly progressLog: DashboardProgress[] = [];
  private static readonly PROGRESS_LIMIT = 50;

  constructor(
    @Inject(DB_CONN) private readonly db: DB,
    @Inject(ENV) private readonly env: OrchestratorEnv,
    private readonly registry: StationRegistryService,
    private readonly circuitBreaker: CircuitBreakerService,
  ) {}

  /** Ghi nhận một bước tiến trình từ worker (§8). Giữ tối đa PROGRESS_LIMIT sự kiện gần nhất. */
  recordProgress(msg: JobProgressMessage): void {
    this.progressLog.push({
      trace_id: msg.trace_id,
      step: msg.step,
      detail: msg.detail ?? null,
      ts: msg.ts,
    });
    if (this.progressLog.length > DashboardService.PROGRESS_LIMIT) {
      this.progressLog.splice(0, this.progressLog.length - DashboardService.PROGRESS_LIMIT);
    }
  }

  async snapshot(): Promise<DashboardSnapshot> {
    const stations: DashboardStation[] = this.registry.list().map((s) => ({
      station_id: s.station_id,
      name: s.name,
      status: s.status,
      current_load: s.current_load,
      max_concurrency: s.max_concurrency,
      agent_version: s.agent_version,
      last_ping_at: s.last_ping_at,
      ram_mb: s.ram_mb,
      cpu_percent: s.cpu_percent,
    }));

    const ratios: DashboardRatio[] = await logRepo.ratiosByPlatform(
      this.db,
      this.env.DASHBOARD_RATIO_WINDOW_MINUTES,
    );

    const pool = await profileRepo.countByStatusAll(this.db);

    const recent = await jobRepo.recentJobs(this.db, 20);
    const recentJobs: DashboardJob[] = recent.map((j) => ({
      trace_id: j.trace_id,
      platform: j.platform,
      status: j.status,
      result: j.result ?? null,
      retry_count: j.retry_count,
      created_at: j.created_at instanceof Date ? j.created_at.toISOString() : String(j.created_at),
    }));

    const circuits: DashboardCircuit[] = [];
    for (const platform of Object.values(Platform)) {
      const st = await this.circuitBreaker.status(platform);
      circuits.push({
        platform,
        open: st.open,
        retry_after_seconds: Math.ceil(st.retryAfterMs / 1000),
      });
    }

    return {
      ts: new Date().toISOString(),
      stations,
      ratios,
      pool,
      recent_jobs: recentJobs,
      circuits,
      alerts: this.deriveAlerts(ratios, pool, circuits),
      progress: [...this.progressLog].reverse(), // mới nhất trước
    };
  }

  /** Cảnh báo vận hành (§8 dashboard): circuit mở, tỷ lệ block cao, pool cạn. */
  private deriveAlerts(
    ratios: DashboardRatio[],
    pool: { platform: Platform; status: ProfileStatus; count: number }[],
    circuits: DashboardCircuit[],
  ): DashboardAlert[] {
    const alerts: DashboardAlert[] = [];

    for (const c of circuits) {
      if (c.open) {
        alerts.push({
          level: 'critical',
          kind: 'circuit_open',
          message: `Circuit ${c.platform} đang MỞ — chặn ${c.retry_after_seconds}s để bảo vệ pool`,
        });
      }
    }

    for (const r of ratios) {
      if (r.total >= this.env.CIRCUIT_MIN_SAMPLES && r.blocked / r.total >= 0.3) {
        alerts.push({
          level: 'warn',
          kind: 'block_spike',
          message: `${r.platform}: BLOCKED tăng (${r.blocked}/${r.total}) — có thể bị siết`,
        });
      }
    }

    const availableByPlatform = new Map<string, number>();
    for (const p of pool) {
      if (p.status === ProfileStatus.AVAILABLE) availableByPlatform.set(p.platform, p.count);
    }
    for (const [platform, available] of availableByPlatform) {
      if (available <= this.env.PROFILE_POOL_LOW_WATERMARK) {
        alerts.push({
          level: 'warn',
          kind: 'pool_low',
          message: `${platform}: pool profile thấp (${available} khả dụng)`,
        });
      }
    }

    return alerts;
  }
}
