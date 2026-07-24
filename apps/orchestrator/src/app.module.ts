import { Module } from '@nestjs/common';
import { Redis } from 'ioredis';
import {
  cookieKeyringFromEnv,
  loadOrchestratorEnv,
  type OrchestratorEnv,
} from '@fastcheck/config';
import { createDb } from '@fastcheck/db';
import { createCookieCipher } from '@fastcheck/crypto';
import { createLogger } from '@fastcheck/shared';
import { COOKIE_CIPHER, DB_CONN, ENV, LOGGER, REDIS } from './tokens.js';
import { HealthController } from './health/health.controller.js';
import { StationRegistryService } from './station-registry/station-registry.service.js';
import { WsGatewayService } from './ws/ws.gateway.js';
import { CdpRelayGateway } from './cdp/cdp-relay.gateway.js';
import { RabbitConsumerService } from './consumer/rabbit.consumer.js';
import { DispatchService } from './dispatch/dispatch.service.js';
import { JobPublisher } from './dispatch/job-publisher.js';
import { RateLimiter } from './ratelimit/rate-limiter.js';
import { LeaseReaperService } from './lifecycle/lease-reaper.service.js';
import { StationMonitorService } from './lifecycle/station-monitor.service.js';
import { CircuitBreakerService } from './circuit/circuit-breaker.service.js';
import { MetricsService } from './metrics/metrics.service.js';
import { ObservabilityController } from './metrics/observability.controller.js';
import { DashboardService } from './dashboard/dashboard.service.js';
import { PendingCommandsService } from './control/pending-commands.service.js';
import { StationControlService } from './control/station-control.service.js';
import { StationControlController } from './control/station-control.controller.js';

@Module({
  controllers: [HealthController, ObservabilityController, StationControlController],
  providers: [
    { provide: ENV, useFactory: () => loadOrchestratorEnv() },
    {
      provide: LOGGER,
      useFactory: (env: OrchestratorEnv) => createLogger({ name: 'orchestrator', level: env.LOG_LEVEL }),
      inject: [ENV],
    },
    {
      provide: DB_CONN,
      useFactory: (env: OrchestratorEnv) => createDb(env.DATABASE_URL),
      inject: [ENV],
    },
    {
      provide: REDIS,
      useFactory: (env: OrchestratorEnv) => new Redis(env.REDIS_URL),
      inject: [ENV],
    },
    {
      // Một nơi duy nhất giải mã cookie (INV-12). Keyring gồm khoá active + khoá cũ (xoay khoá).
      provide: COOKIE_CIPHER,
      useFactory: (env: OrchestratorEnv) => {
        const ring = cookieKeyringFromEnv(env);
        return createCookieCipher(ring.activeKeyBase64, ring.activeKeyId, ring.olderKeys);
      },
      inject: [ENV],
    },
    {
      provide: RateLimiter,
      useFactory: (redis: Redis) => new RateLimiter(redis),
      inject: [REDIS],
    },
    StationRegistryService,
    JobPublisher,
    CircuitBreakerService,
    MetricsService,
    DashboardService,
    PendingCommandsService,
    StationControlService,
    DispatchService,
    WsGatewayService,
    CdpRelayGateway,
    RabbitConsumerService,
    LeaseReaperService,
    StationMonitorService,
  ],
})
export class AppModule {}
