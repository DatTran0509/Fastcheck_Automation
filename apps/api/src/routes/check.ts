import type { FastifyInstance } from 'fastify';
import type { ChannelWrapper } from 'amqp-connection-manager';
import type { Redis } from 'ioredis';
import type { ZodTypeProvider } from 'fastify-type-provider-zod';
import { z } from 'zod';
import {
  JobStatus,
  circuitKeys,
  detectPlatform,
  newTraceId,
  normalizeUrl,
  urlHash,
  type Logger,
} from '@fastcheck/shared';
import {
  checkAcceptedSchema,
  checkCachedSchema,
  checkCircuitOpenSchema,
  checkRequestSchema,
  checkStatusResponseSchema,
  type CheckAccepted,
  type CheckCached,
  type CheckCircuitOpen,
  type CheckStatusResponse,
} from '@fastcheck/contracts';
import { jobRepo, type DB } from '@fastcheck/db';
import type { ResultCache } from '../services/cache.js';
import type { StampedeLock } from '../services/lock.js';
import { publishJob } from '../services/queue.js';

export interface CheckRouteDeps {
  db: DB;
  cache: ResultCache;
  lock: StampedeLock;
  redis: Redis; // đọc trạng thái circuit breaker (§10.6)
  channel: ChannelWrapper;
  logger: Logger;
}

const errorSchema = z.object({ error: z.string(), message: z.string().optional() });

function toIso(value: Date | string | null): string | null {
  if (value === null) return null;
  return value instanceof Date ? value.toISOString() : String(value);
}

export function registerCheckRoutes(app: FastifyInstance, deps: CheckRouteDeps): void {
  const r = app.withTypeProvider<ZodTypeProvider>();

  // POST /check → 202 + trace_id (miss) / 200 + result (cache hit <500ms) / 503 (circuit mở).
  r.post(
    '/check',
    {
      schema: {
        tags: ['check'],
        summary: 'Kiểm tra trạng thái link (LIVE / DEAD / INCONCLUSIVE)',
        description:
          'Nhận URL social (TikTok/Facebook/X/YouTube), chuẩn hoá + hash, tra cache. Cache HIT → trả ' +
          'kết quả ngay (SLA <500ms). MISS → tạo job + đẩy queue → 202 + trace_id (poll GET /check/{trace_id}; ' +
          'SLA check <3 phút). Ngữ nghĩa: LIVE=link sống, DEAD=chết chắc chắn, INCONCLUSIVE=không đủ tín hiệu ' +
          '(KHÔNG phải DEAD — INV-1). Rate-limit theo client. 503+Retry-After khi circuit breaker của platform MỞ (§10.6).',
        body: checkRequestSchema,
        response: {
          200: checkCachedSchema,
          202: checkAcceptedSchema,
          400: errorSchema,
          503: checkCircuitOpenSchema,
        },
      },
    },
    async (req, reply) => {
      const { url } = req.body;

      const platform = detectPlatform(url);
      if (!platform) {
        return reply
          .code(400)
          .send({ error: 'unsupported_platform', message: 'URL không thuộc TikTok/Facebook/X/YouTube' });
      }

      const targetUrl = normalizeUrl(url);
      const hash = urlHash(url); // INV-13: normalize rồi sha256

      // Cache HIT phục vụ ngay CẢ khi circuit mở (không tiêu pool) — chỉ MISS mới bị circuit chặn.
      const cached = await deps.cache.get(hash);
      if (cached) {
        deps.logger.info({ url_hash: hash, result: cached.status }, 'cache hit — trả <500ms');
        const res: CheckCached = { cached: true, result: cached.status, checked_at: cached.checked_at };
        return reply.code(200).send(res);
      }

      // Circuit breaker (§10.6): platform bị mở → 503 + retry_after (bảo vệ pool khỏi thiệt hại diện rộng).
      const openUntil = Number((await deps.redis.get(circuitKeys(platform).openUntil)) ?? 0);
      const now = Date.now();
      if (openUntil > now) {
        const retryAfter = Math.ceil((openUntil - now) / 1000);
        reply.header('Retry-After', String(retryAfter));
        deps.logger.warn({ platform, retryAfter }, 'circuit MỞ — trả 503');
        const res: CheckCircuitOpen = {
          error: 'circuit_open',
          platform,
          message: `Circuit breaker MỞ cho ${platform} — thử lại sau ${retryAfter}s`,
          retry_after_seconds: retryAfter,
        };
        return reply.code(503).send(res);
      }

      // Chống stampede (§6.2): giành khoá NX; kẻ thua đọc job hiện có thay vì cùng dựng job.
      const gotLock = await deps.lock.acquire(hash);
      if (!gotLock) {
        const existing = await jobRepo.findActiveJobByUrlHash(deps.db, hash);
        if (existing) {
          const res: CheckAccepted = {
            cached: false,
            trace_id: existing.trace_id,
            status: existing.status,
            platform,
            url_hash: hash,
          };
          return reply.code(202).send(res);
        }
      }

      try {
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
          deps.logger.info(
            { trace_id: traceId, job_id: created.id, platform, url_hash: hash },
            'check nhận (202) — đẩy job.pending',
          );
          const res: CheckAccepted = {
            cached: false,
            trace_id: traceId,
            status: JobStatus.PENDING,
            platform,
            url_hash: hash,
          };
          return reply.code(202).send(res);
        }

        // Dedupe (INV-4/INV-13): đã có job active cho url → trả trace_id hiện có, KHÔNG tạo dòng thứ hai.
        const existing = await jobRepo.findActiveJobByUrlHash(deps.db, hash);
        const res: CheckAccepted = {
          cached: false,
          trace_id: existing?.trace_id ?? traceId,
          status: existing?.status ?? JobStatus.PENDING,
          platform,
          url_hash: hash,
        };
        return reply.code(202).send(res);
      } finally {
        if (gotLock) await deps.lock.release(hash);
      }
    },
  );

  // GET /check/:trace_id → trạng thái từ check_jobs (nguồn sự thật, INV-4).
  r.get(
    '/check/:trace_id',
    {
      schema: {
        tags: ['check'],
        summary: 'Trạng thái + kết quả một job theo trace_id',
        params: z.object({ trace_id: z.string().uuid() }),
        response: {
          200: checkStatusResponseSchema,
          404: errorSchema,
        },
      },
    },
    async (req, reply) => {
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
    },
  );
}
