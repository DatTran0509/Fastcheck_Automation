import 'reflect-metadata';
import type { Server as HttpServer } from 'node:http';
import { NestFactory } from '@nestjs/core';
import { FastifyAdapter, type NestFastifyApplication } from '@nestjs/platform-fastify';
import { DocumentBuilder, SwaggerModule } from '@nestjs/swagger';
import type { OrchestratorEnv } from '@fastcheck/config';
import { AppModule } from './app.module.js';
import { ENV } from './tokens.js';
import { WsGatewayService } from './ws/ws.gateway.js';

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create<NestFastifyApplication>(AppModule, new FastifyAdapter(), {
    logger: ['error', 'warn', 'log'],
  });

  // CORS cho dashboard: đọc dữ liệu vận hành (GET /metrics, /dashboard/*) + BẤM NÚT điều khiển station
  // (POST/PATCH/DELETE) từ UI dashboard. KHÔNG endpoint nào trả cookie/credential ra ngoài (INV-12).
  app.enableCors({ origin: true, methods: ['GET', 'POST', 'PATCH', 'DELETE'] });

  // Swagger — bề mặt điều khiển Station Management cho operator (POST /check ở API service, cổng 3001).
  const swaggerConfig = new DocumentBuilder()
    .setTitle('FastCheck Orchestrator — Station Control')
    .setDescription(
      'Điều khiển Station Management: liệt kê station, CRUD profile GemLogin, mở/tắt browser, chạy kịch bản ' +
        'login, nạp tài khoản thật vào pool. KHÔNG endpoint nào trả cookie/credential (INV-12).',
    )
    .setVersion('1.0')
    .build();
  const document = SwaggerModule.createDocument(app, swaggerConfig);
  SwaggerModule.setup('docs', app, document);

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
