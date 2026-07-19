import type { FastifyInstance } from 'fastify';
import { Counter, Histogram, Registry, collectDefaultMetrics } from 'prom-client';

/**
 * Metric Prometheus phía API (spec §10.4). Đo p95 latency POST /check (KPI <500ms khi cache hit) + đếm
 * request. Nghiệp vụ (tỷ lệ LIVE/DEAD/..., pool, queue) nằm ở /metrics của orchestrator. Không lộ dữ liệu nhạy cảm.
 */
export function registerApiMetrics(app: FastifyInstance): void {
  const registry = new Registry();
  collectDefaultMetrics({ register: registry, prefix: 'fastcheck_api_' });

  const httpDuration = new Histogram({
    name: 'fastcheck_api_request_duration_ms',
    help: 'Thời gian xử lý request API (ms) theo route + status',
    labelNames: ['method', 'route', 'status'] as const,
    buckets: [5, 10, 25, 50, 100, 250, 500, 1000, 2000],
    registers: [registry],
  });
  const httpTotal = new Counter({
    name: 'fastcheck_api_requests_total',
    help: 'Tổng số request API theo route + status',
    labelNames: ['method', 'route', 'status'] as const,
    registers: [registry],
  });

  app.addHook('onResponse', async (req, reply) => {
    // routeOptions.url = mẫu route (vd /check/:trace_id) — không đưa trace_id vào label (chống nổ cardinality).
    const route = req.routeOptions?.url ?? req.url;
    const labels = { method: req.method, route, status: String(reply.statusCode) };
    httpDuration.observe(labels, reply.elapsedTime);
    httpTotal.inc(labels);
  });

  app.get('/metrics', async (_req, reply) => {
    reply.header('Content-Type', registry.contentType);
    return registry.metrics();
  });
}
