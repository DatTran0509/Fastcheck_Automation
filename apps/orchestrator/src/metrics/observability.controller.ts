import { Controller, Get, Inject, Res, Sse, type MessageEvent } from '@nestjs/common';
import type { FastifyReply } from 'fastify';
import { interval, map, startWith, switchMap, type Observable } from 'rxjs';
import type { OrchestratorEnv } from '@fastcheck/config';
import { ENV } from '../tokens.js';
import { MetricsService } from './metrics.service.js';
import { DashboardService } from '../dashboard/dashboard.service.js';

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
