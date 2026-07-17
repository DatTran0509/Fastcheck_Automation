import 'reflect-metadata';
import type { Server as HttpServer } from 'node:http';
import { NestFactory } from '@nestjs/core';
import { FastifyAdapter, type NestFastifyApplication } from '@nestjs/platform-fastify';
import type { OrchestratorEnv } from '@fastcheck/config';
import { AppModule } from './app.module.js';
import { ENV } from './tokens.js';
import { WsGatewayService } from './ws/ws.gateway.js';

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create<NestFastifyApplication>(AppModule, new FastifyAdapter(), {
    logger: ['error', 'warn', 'log'],
  });

  const env = app.get<OrchestratorEnv>(ENV);
  await app.listen(env.ORCHESTRATOR_PORT, env.ORCHESTRATOR_HOST);

  // Gắn WS server vào chính HTTP server của Nest (cùng cổng, path /ws).
  const httpServer = app.getHttpServer() as HttpServer;
  app.get(WsGatewayService).attach(httpServer);
}

bootstrap().catch((err) => {
  console.error('Orchestrator failed to start:', err);
  process.exit(1);
});
