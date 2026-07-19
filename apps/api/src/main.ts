import Fastify from 'fastify';
import { Redis } from 'ioredis';
import amqp from 'amqp-connection-manager';
import type { Channel } from 'amqplib';
import fastifySwagger from '@fastify/swagger';
import fastifySwaggerUi from '@fastify/swagger-ui';
import fastifyCors from '@fastify/cors';
import {
  serializerCompiler,
  validatorCompiler,
  jsonSchemaTransform,
} from 'fastify-type-provider-zod';
import { loadApiEnv } from '@fastcheck/config';
import { createLogger } from '@fastcheck/shared';
import { createDb } from '@fastcheck/db';
import { EXCHANGE, QUEUE_PENDING, ROUTING_PENDING } from '@fastcheck/contracts';
import { ResultCache } from './services/cache.js';
import { StampedeLock } from './services/lock.js';
import { registerApiMetrics } from './services/metrics.js';
import { registerCheckRoutes } from './routes/check.js';

async function main(): Promise<void> {
  const env = loadApiEnv();
  const logger = createLogger({ name: 'api', level: env.LOG_LEVEL });

  const db = createDb(env.DATABASE_URL);
  const redis = new Redis(env.REDIS_URL);
  const cache = new ResultCache(
    redis,
    env.RESULT_CACHE_TTL_LIVE_SECONDS,
    env.RESULT_CACHE_TTL_DEAD_SECONDS,
  );
  const lock = new StampedeLock(redis);

  // amqp-connection-manager: tự reconnect + buffer publish khi broker rớt (resilience — review P3).
  const amqpConnection = amqp.connect([env.RABBITMQ_URL]);
  const channel = amqpConnection.createChannel({
    json: false,
    setup: async (ch: Channel) => {
      await ch.assertExchange(EXCHANGE, 'direct', { durable: true });
      await ch.assertQueue(QUEUE_PENDING, { durable: true });
      await ch.bindQueue(QUEUE_PENDING, EXCHANGE, ROUTING_PENDING);
    },
  });
  await channel.waitForConnect();

  const app = Fastify({ logger: false });

  // CORS cho dashboard gửi POST /check từ trình duyệt (test thủ công qua UI). Không trả cookie/credential.
  await app.register(fastifyCors, { origin: true, methods: ['GET', 'POST'] });

  // Validate/serialize theo zod (DTO ở packages/contracts) — nguồn sự thật cho cả runtime lẫn tài liệu.
  app.setValidatorCompiler(validatorCompiler);
  app.setSerializerCompiler(serializerCompiler);

  // OpenAPI sinh TỪ zod (§6.11): schema, mã lỗi, ví dụ; phục vụ ở /docs. Đổi DTO trong contracts → docs đổi theo.
  await app.register(fastifySwagger, {
    openapi: {
      info: {
        title: 'FastCheck API',
        version: '0.0.0',
        description:
          'Kiểm tra trạng thái LIVE/DEAD/INCONCLUSIVE của link social. SLA: <500ms cache hit, <3 phút check thật. ' +
          'INCONCLUSIVE KHÔNG phải DEAD (INV-1). Rate-limit theo client; circuit breaker theo platform (503+Retry-After).',
      },
      tags: [{ name: 'check', description: 'Kiểm tra trạng thái link social' }],
    },
    transform: jsonSchemaTransform,
  });
  await app.register(fastifySwaggerUi, { routePrefix: '/docs' });

  app.get('/health', async () => ({ status: 'ok' }));
  registerApiMetrics(app);
  registerCheckRoutes(app, { db, cache, lock, redis, channel, logger });

  await app.listen({ host: env.API_HOST, port: env.API_PORT });
  logger.info({ host: env.API_HOST, port: env.API_PORT }, 'FastCheck API đang lắng nghe (/docs, /metrics)');

  const shutdown = async (): Promise<void> => {
    logger.info('API shutting down…');
    await app.close();
    await channel.close();
    await amqpConnection.close();
    redis.disconnect();
    await db.destroy();
    process.exit(0);
  };
  process.on('SIGINT', () => void shutdown());
  process.on('SIGTERM', () => void shutdown());
}

main().catch((err) => {
  console.error('API failed to start:', err);
  process.exit(1);
});
