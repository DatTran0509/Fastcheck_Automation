import type { IncomingMessage, Server as HttpServer } from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import { Inject, Injectable } from '@nestjs/common';
import { WebSocketServer, type RawData, type WebSocket } from 'ws';
import { wsClientMessageSchema, type WsClientMessage } from '@fastcheck/contracts';
import type { Logger } from '@fastcheck/shared';
import type { OrchestratorEnv } from '@fastcheck/config';
import { ENV, LOGGER } from '../tokens.js';
import { StationRegistryService } from '../station-registry/station-registry.service.js';

/**
 * WS Gateway station ↔ orchestrator (WSS + token — INV-12). Bám thẳng vào HTTP server của Nest tại /ws.
 * Xử lý message thô theo envelope contracts (register/heartbeat...). Lệnh idempotent + command_id ở chiều ngược (INV-14).
 */
@Injectable()
export class WsGatewayService {
  private wss?: WebSocketServer;

  constructor(
    @Inject(ENV) private readonly env: OrchestratorEnv,
    @Inject(LOGGER) private readonly logger: Logger,
    private readonly registry: StationRegistryService,
  ) {}

  attach(server: HttpServer): void {
    this.wss = new WebSocketServer({
      server,
      path: '/ws',
      // INV-12: xác thực token NGAY ở HTTP upgrade (handshake), trước khi chấp nhận kết nối / nhận message.
      verifyClient: (info: { req: IncomingMessage }) => this.authorize(info.req),
    });
    this.wss.on('connection', (socket: WebSocket) => this.onConnection(socket));
    this.logger.info('WS gateway gắn tại /ws (auth ở handshake)');
  }

  /** So khớp `Authorization: Bearer <token>` bằng so sánh hằng-thời-gian (chống timing side-channel). */
  private authorize(req: IncomingMessage): boolean {
    const provided = Buffer.from(req.headers['authorization'] ?? '');
    const expected = Buffer.from(`Bearer ${this.env.WS_AUTH_TOKEN}`);
    if (provided.length !== expected.length) return false;
    return timingSafeEqual(provided, expected);
  }

  private onConnection(socket: WebSocket): void {
    let stationId: string | null = null;
    socket.on('message', (raw: RawData) => {
      let msg: WsClientMessage;
      try {
        msg = wsClientMessageSchema.parse(JSON.parse(raw.toString()));
      } catch (err) {
        this.logger.warn({ err: (err as Error).message }, 'WS message không hợp lệ, bỏ qua');
        return;
      }
      void this.handle(msg, socket, (id) => {
        stationId = id;
      });
    });
    socket.on('close', () => {
      if (stationId) void this.registry.markOffline(stationId);
    });
    socket.on('error', (err: Error) => this.logger.warn({ err: err.message }, 'WS socket error'));
  }

  private async handle(
    msg: WsClientMessage,
    socket: WebSocket,
    setStationId: (id: string) => void,
  ): Promise<void> {
    switch (msg.type) {
      case 'register': {
        // Token đã được xác thực ở handshake (verifyClient) — ở đây chỉ ghi nhận đăng ký.
        setStationId(msg.station.station_id);
        await this.registry.register(msg.station, socket);
        socket.send(JSON.stringify({ type: 'registered', station_id: msg.station.station_id }));
        break;
      }
      case 'heartbeat':
        await this.registry.heartbeat(msg.station_id, msg.current_load);
        break;
      case 'command_ack':
      case 'job_result':
        // Phase 0: chưa dispatch job nên chưa xử lý ack/result. Dừng ở khung.
        break;
    }
  }
}
