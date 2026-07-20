import { Inject, Injectable } from '@nestjs/common';
import { JobStatus, Platform, ProfileStatus } from '@fastcheck/shared';
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
  JobHistoryResponse,
  JobProgressMessage,
} from '@fastcheck/contracts';

const toIso = (d: Date | string): string =>
  d instanceof Date ? d.toISOString() : String(d);
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

  /**
   * Lịch sử job có filter + phân trang (GET /dashboard/jobs) — cho bảng Kết quả (search/filter/lazy-load/export).
   * Đọc từ check_jobs (nguồn sự thật — INV-4) + join check_log mới nhất. KHÔNG cookie/credential (INV-12).
   */
  async jobsHistory(f: {
    platform?: Platform;
    status?: JobStatus;
    q?: string;
    limit: number;
    offset: number;
  }): Promise<JobHistoryResponse> {
    const [rows, total] = await Promise.all([
      jobRepo.listJobs(this.db, f),
      jobRepo.countJobs(this.db, { platform: f.platform, status: f.status, q: f.q }),
    ]);
    return {
      items: rows.map((r) => ({
        trace_id: r.trace_id,
        target_url: r.target_url,
        platform: r.platform,
        status: r.status,
        result: r.result ?? null,
        profile_health: r.profile_health ?? null,
        block_reason: r.block_reason ?? null,
        response_time_ms: r.response_time_ms ?? null,
        retry_count: r.retry_count,
        created_at: toIso(r.created_at),
        finished_at: r.finished_at ? toIso(r.finished_at) : null,
      })),
      total,
      limit: f.limit,
      offset: f.offset,
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
