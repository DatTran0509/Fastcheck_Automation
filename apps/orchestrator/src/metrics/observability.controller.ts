import { Controller, Get, Inject, Query, Res, Sse, type MessageEvent } from '@nestjs/common';
import type { FastifyReply } from 'fastify';
import { interval, map, startWith, switchMap, type Observable } from 'rxjs';
import { JobStatus, Platform } from '@fastcheck/shared';
import type { OrchestratorEnv } from '@fastcheck/config';
import { ENV } from '../tokens.js';
import { MetricsService } from './metrics.service.js';
import { DashboardService } from '../dashboard/dashboard.service.js';

const asEnum = <T extends string>(all: readonly T[], v?: string): T | undefined =>
  v && (all as readonly string[]).includes(v) ? (v as T) : undefined;
const asInt = (v: string | undefined, def: number, min: number, max: number): number =>
  Math.min(Math.max(parseInt(v ?? '', 10) || def, min), max);

/**
 * Observability HTTP: `/metrics` (Prometheus), `/dashboard/snapshot` (một lần), `/dashboard/stream` (SSE
 * realtime). Tất cả CHỈ dữ liệu vận hành — không cookie/credential (INV-12).
 */
@Controller()
export class ObservabilityController {
  constructor(
    @Inject(ENV) private readonly env: OrchestratorEnv,
    private readonly metrics: MetricsService,
    private readonly dashboard: DashboardService,
  ) {}

  @Get('metrics')
  async getMetrics(@Res() reply: FastifyReply): Promise<void> {
    reply.header('Content-Type', this.metrics.contentType());
    reply.send(await this.metrics.metricsText());
  }

  @Get('dashboard/snapshot')
  async getSnapshot() {
    return this.dashboard.snapshot();
  }

  /**
   * Lịch sử job có filter + phân trang cho bảng Kết quả (search theo url, filter platform/status, lazy-load,
   * export). limit tối đa 5000 (đủ cho một lần export). Giá trị enum sai → bỏ filter đó (không lỗi 400 vặt).
   */
  @Get('dashboard/jobs')
  async getJobs(
    @Query('platform') platform?: string,
    @Query('status') status?: string,
    @Query('q') q?: string,
    @Query('limit') limit?: string,
    @Query('offset') offset?: string,
  ) {
    return this.dashboard.jobsHistory({
      platform: asEnum(Object.values(Platform), platform),
      status: asEnum(Object.values(JobStatus), status),
      q: q?.trim() || undefined,
      limit: asInt(limit, 50, 1, 5000),
      offset: asInt(offset, 0, 0, 10_000_000),
    });
  }

  /** SSE: đẩy snapshot theo chu kỳ (realtime, không polling DB nặng phía FE). */
  @Sse('dashboard/stream')
  stream(): Observable<MessageEvent> {
    return interval(this.env.DASHBOARD_STREAM_INTERVAL_MS).pipe(
      startWith(0),
      switchMap(() => this.dashboard.snapshot()),
      map((snapshot) => ({ data: snapshot }) as MessageEvent),
    );
  }
}
