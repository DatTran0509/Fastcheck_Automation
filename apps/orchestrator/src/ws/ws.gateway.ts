import type { IncomingMessage, Server as HttpServer } from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import { Inject, Injectable } from '@nestjs/common';
import { WebSocketServer, type RawData, type WebSocket } from 'ws';
import { wsClientMessageSchema, type WsClientMessage } from '@fastcheck/contracts';
import type { Logger } from '@fastcheck/shared';
import type { OrchestratorEnv } from '@fastcheck/config';
import { ENV, LOGGER } from '../tokens.js';
import { StationRegistryService } from '../station-registry/station-registry.service.js';
import { DispatchService } from '../dispatch/dispatch.service.js';
import { DashboardService } from '../dashboard/dashboard.service.js';
import { PendingCommandsService } from '../control/pending-commands.service.js';

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
    private readonly dispatch: DispatchService,
    private readonly dashboard: DashboardService,
    private readonly pending: PendingCommandsService,
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
      // Một message lỗi KHÔNG được làm sập cả gateway (nếu không, một job_result mồ côi / lỗi DB sẽ
      // giết orchestrator và mọi station mất điều phối). Bắt + log, giữ service sống (self-healing).
      this.handle(msg, socket, (id) => {
        stationId = id;
      }).catch((err: unknown) =>
        this.logger.error(
          { err: (err as Error).message, type: msg.type },
          'lỗi xử lý message WS (đã nuốt để không làm sập gateway)',
        ),
      );
    });
    socket.on('close', () => {
      // Chỉ xử lý nếu socket đang đóng CHÍNH là socket hiện hành (chống race lúc reconnect: worker mở
      // kết nối mới + register trước khi 'close' của socket cũ bắn — INV-15).
      if (stationId && this.registry.isActiveSocket(stationId, socket)) {
        void this.handleStationDown(stationId);
      }
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
        await this.registry.heartbeat(msg.station_id, msg.current_load, msg.ram_mb, msg.cpu_percent);
        break;
      case 'job_result':
        // Kết quả check từ station → ghi log + cập nhật job + cache + ack (INV-3/INV-4).
        await this.dispatch.handleResult(msg);
        break;
      case 'profile_sync':
        // Đồng bộ danh sách profile GemLogin của station → cập nhật bảng profiles (§3).
        await this.registry.syncProfiles(msg);
        break;
      case 'command_ack':
        // Xác nhận đã xử lý lệnh (browser.open/close, profile.*, login.run). ok=false → log để không nuốt lỗi.
        if (!msg.ok) {
          this.logger.warn(
            { command_id: msg.command_id, station_id: msg.station_id, detail: msg.detail },
            'station báo lệnh THẤT BẠI (command_ack ok=false)',
          );
        }
        // Khớp về REST đang chờ (bề mặt điều khiển). Ack của lệnh dispatch tự động → không ai chờ → bỏ qua.
        this.pending.resolve(msg);
        break;
      case 'cookie_refresh':
        // Cookie mới sau phiên login OK → orchestrator MÃ HOÁ & lưu (spec §4.4, INV-12). KHÔNG log cookie.
        await this.dispatch.refreshCookie(msg);
        break;
      case 'job_progress':
        // Bước tiến trình job (mở browser → login → detect → xong) → feed dashboard stream (§8).
        this.dashboard.recordProgress(msg);
        break;
    }
  }

  /** Station rớt kết nối → OFFLINE + thu hồi mọi job RUNNING của nó (INV-15). */
  private async handleStationDown(stationId: string): Promise<void> {
    this.logger.warn({ station_id: stationId }, 'WS đóng — station DOWN, thu hồi job (INV-15)');
    // Giải phóng các REST đang chờ command_ack của station này (không để treo tới timeout).
    this.pending.rejectStation(stationId, 'station rớt kết nối trước khi ack');
    await this.registry.markOffline(stationId);
    await this.dispatch.recoverStationJobs(stationId);
  }
}
