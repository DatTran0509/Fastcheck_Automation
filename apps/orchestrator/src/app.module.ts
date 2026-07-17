import { Module } from '@nestjs/common';
import { loadOrchestratorEnv, type OrchestratorEnv } from '@fastcheck/config';
import { createDb } from '@fastcheck/db';
import { createLogger } from '@fastcheck/shared';
import { DB_CONN, ENV, LOGGER } from './tokens.js';
import { HealthController } from './health/health.controller.js';
import { StationRegistryService } from './station-registry/station-registry.service.js';
import { WsGatewayService } from './ws/ws.gateway.js';
import { RabbitConsumerService } from './consumer/rabbit.consumer.js';

@Module({
  controllers: [HealthController],
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
    StationRegistryService,
    WsGatewayService,
    RabbitConsumerService,
  ],
})
export class AppModule {}
