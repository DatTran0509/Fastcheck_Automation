import type { FastifyInstance } from 'fastify';
import type { ChannelWrapper } from 'amqp-connection-manager';
import { JobStatus, detectPlatform, newTraceId, normalizeUrl, urlHash } from '@fastcheck/shared';
import {
  checkRequestSchema,
  type CheckResponse,
  type CheckStatusResponse,
} from '@fastcheck/contracts';
import { jobRepo, type DB } from '@fastcheck/db';
import type { ResultCache } from '../services/cache.js';
import { publishJob } from '../services/queue.js';

export interface CheckRouteDeps {
  db: DB;
  cache: ResultCache;
  channel: ChannelWrapper;
}

function toIso(value: Date | string | null): string | null {
  if (value === null) return null;
  return value instanceof Date ? value.toISOString() : String(value);
}

export function registerCheckRoutes(app: FastifyInstance, deps: CheckRouteDeps): void {
  // POST /check → 202 + trace_id (miss) hoặc 200 + result (cache hit < 500ms).
  app.post('/check', async (req, reply) => {
    const parsed = checkRequestSchema.safeParse(req.body);
    if (!parsed.success) {
      return reply.code(400).send({ error: 'invalid_request', issues: parsed.error.issues });
    }
    const { url } = parsed.data;

    const platform = detectPlatform(url);
    if (!platform) {
      return reply
        .code(400)
        .send({ error: 'unsupported_platform', message: 'URL không thuộc TikTok/Facebook/X/YouTube' });
    }

    const targetUrl = normalizeUrl(url);
    const hash = urlHash(url); // INV-13: normalize rồi sha256

    const cached = await deps.cache.get(hash);
    if (cached) {
      const res: CheckResponse = { cached: true, result: cached.status, checked_at: cached.checked_at };
      return reply.code(200).send(res);
    }

    const traceId = newTraceId();
    const created = await jobRepo.createPendingJob(deps.db, {
      trace_id: traceId,
      target_url: targetUrl,
      url_hash: hash,
      platform,
    });

    if (created) {
      await publishJob(deps.channel, {
        trace_id: traceId,
        job_id: created.id,
        target_url: targetUrl,
        url_hash: hash,
        platform,
        retry_count: 0,
      });
      const res: CheckResponse = {
        cached: false,
        trace_id: traceId,
        status: JobStatus.PENDING,
        platform,
        url_hash: hash,
      };
      return reply.code(202).send(res);
    }

    // Dedupe (INV-4/INV-13): đã có job PENDING/RUNNING cho url này → trả trace_id hiện có, KHÔNG tạo dòng thứ hai.
    const existing = await jobRepo.findActiveJobByUrlHash(deps.db, hash);
    const res: CheckResponse = {
      cached: false,
      trace_id: existing?.trace_id ?? traceId,
      status: existing?.status ?? JobStatus.PENDING,
      platform,
      url_hash: hash,
    };
    return reply.code(202).send(res);
  });

  // GET /check/:trace_id → trạng thái từ check_jobs (nguồn sự thật, INV-4).
  app.get<{ Params: { trace_id: string } }>('/check/:trace_id', async (req, reply) => {
    const job = await jobRepo.getJobByTraceId(deps.db, req.params.trace_id);
    if (!job) return reply.code(404).send({ error: 'not_found' });
    const res: CheckStatusResponse = {
      trace_id: job.trace_id,
      status: job.status,
      result: job.result ?? null,
      platform: job.platform,
      target_url: job.target_url,
      retry_count: job.retry_count,
      created_at: toIso(job.created_at) ?? new Date(0).toISOString(),
      finished_at: toIso(job.finished_at),
    };
    return reply.send(res);
  });
}
