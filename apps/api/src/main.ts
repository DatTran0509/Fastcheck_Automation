import Fastify from 'fastify';
import { Redis } from 'ioredis';
import amqp from 'amqp-connection-manager';
import type { Channel } from 'amqplib';
import { loadApiEnv } from '@fastcheck/config';
import { createLogger } from '@fastcheck/shared';
import { createDb } from '@fastcheck/db';
import { EXCHANGE, QUEUE_PENDING, ROUTING_PENDING } from '@fastcheck/contracts';
import { ResultCache } from './services/cache.js';
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
  app.get('/health', async () => ({ status: 'ok' }));
  registerCheckRoutes(app, { db, cache, channel });

  await app.listen({ host: env.API_HOST, port: env.API_PORT });
  logger.info({ host: env.API_HOST, port: env.API_PORT }, 'FastCheck API đang lắng nghe');

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
