import type { IncomingMessage, Server as HttpServer } from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import { Inject, Injectable } from '@nestjs/common';
import { WebSocketServer, type RawData, type WebSocket } from 'ws';
import type { Logger } from '@fastcheck/shared';
import type { OrchestratorEnv } from '@fastcheck/config';
import { ENV, LOGGER } from '../tokens.js';

/**
 * Relay CDP/WebSocket (Excel "forward CDP/websocket điều khiển trình duyệt" + INV-12).
 *
 * Đường đi:  controller ──WSS /cdp?role=controller&session──►  RELAY  ◄──WSS /cdp?role=worker&session── worker
 * Relay GHÉP CẦU hai socket cùng `session` rồi bơm gói HAI CHIỀU (không diễn giải nội dung CDP). Kênh luôn
 * WSS + token (INV-12) — KHÔNG bao giờ phơi CDP TRẦN ra ngoài. Mặc định TẮT; bật thì BẮT BUỘC có token.
 *
 * Đây là lớp TRANSPORT thật (thay cho stub chính sách cũ). Worker dựng cầu bằng CdpTunnel khi nhận lệnh
 * `cdp.forward START`; controller (automation/DevTools proxy phía server) attach vào cùng session_id.
 */
interface CdpSession {
  worker?: WebSocket;
  controller?: WebSocket;
}

@Injectable()
export class CdpRelayGateway {
  private wss?: WebSocketServer;
  private readonly sessions = new Map<string, CdpSession>();

  constructor(
    @Inject(ENV) private readonly env: OrchestratorEnv,
    @Inject(LOGGER) private readonly logger: Logger,
  ) {}

  attach(server: HttpServer): void {
    if (!this.env.CDP_FORWARD_ENABLED) {
      // Đường mặc định an toàn: không mở /cdp (login/detector chạy local trên máy trạm).
      this.logger.info('CDP relay TẮT (CDP_FORWARD_ENABLED=false) — không mở /cdp');
      return;
    }
    // Fail-fast (INV-12): bật forward mà thiếu token = cấu hình nguy hiểm → từ chối khởi động, không phơi CDP trần.
    if (!this.env.CDP_FORWARD_TOKEN) {
      throw new Error(
        'CDP_FORWARD_ENABLED=true nhưng thiếu CDP_FORWARD_TOKEN — từ chối mở /cdp (INV-12, không phơi CDP trần).',
      );
    }
    this.wss = new WebSocketServer({
      server,
      path: '/cdp',
      // INV-12: xác thực token NGAY ở HTTP upgrade, trước khi ghép cầu.
      verifyClient: (info: { req: IncomingMessage }) => this.authorize(info.req),
    });
    this.wss.on('connection', (socket: WebSocket, req: IncomingMessage) => this.onConnection(socket, req));
    this.logger.info('CDP relay gắn tại /cdp (WSS + token, ghép cầu theo session)');
  }

  /** Token /cdp: chấp nhận `Authorization: Bearer` (controller server-side) HOẶC `?token=` (controller không set được header). So sánh hằng-thời-gian. */
  private authorize(req: IncomingMessage): boolean {
    const expected = this.env.CDP_FORWARD_TOKEN ?? '';
    const header = req.headers['authorization'];
    if (typeof header === 'string' && this.safeEq(header, `Bearer ${expected}`)) return true;
    try {
      const q = new URL(req.url ?? '', 'http://x').searchParams.get('token') ?? '';
      return this.safeEq(q, expected);
    } catch {
      return false;
    }
  }

  private safeEq(a: string, b: string): boolean {
    const ba = Buffer.from(a);
    const bb = Buffer.from(b);
    if (ba.length !== bb.length) return false;
    return timingSafeEqual(ba, bb);
  }

  private onConnection(socket: WebSocket, req: IncomingMessage): void {
    let role: string | null = null;
    let session: string | null = null;
    try {
      const url = new URL(req.url ?? '', 'http://x');
      role = url.searchParams.get('role');
      session = url.searchParams.get('session');
    } catch {
      /* fallthrough → đóng dưới */
    }
    if (!session || (role !== 'worker' && role !== 'controller')) {
      socket.close(1008, 'thiếu role/session hợp lệ');
      return;
    }
    const sid = session;
    const sess = this.sessions.get(sid) ?? {};
    // Một role đến hai lần cùng session → đóng cái cũ (chống rò socket mồ côi).
    const prev = role === 'worker' ? sess.worker : sess.controller;
    if (prev && prev !== socket) {
      try {
        prev.close(1000, 'thay bằng kết nối mới cùng session');
      } catch {
        /* ignore */
      }
    }
    if (role === 'worker') sess.worker = socket;
    else sess.controller = socket;
    this.sessions.set(sid, sess);
    this.logger.info({ session: sid, role }, 'CDP relay: một đầu kết nối');

    socket.on('message', (data: RawData, isBinary: boolean) => {
      const cur = this.sessions.get(sid);
      const peer = role === 'worker' ? cur?.controller : cur?.worker;
      // Chưa đủ hai đầu → bỏ khung (controller thường attach trước, browser chỉ trả lời sau lệnh). Không nuốt lỗi:
      // đây là hành vi bình thường của relay, không phải lỗi.
      if (peer && peer.readyState === peer.OPEN) peer.send(data, { binary: isBinary });
    });
    socket.on('close', () => this.teardown(sid, role, socket));
    socket.on('error', (err: Error) =>
      this.logger.warn({ session: sid, role, err: err.message }, 'CDP relay socket error'),
    );
  }

  /** Một đầu đóng → đóng đầu kia + dọn session (không để cầu treo một nửa). */
  private teardown(sid: string, role: string | null, socket: WebSocket): void {
    const cur = this.sessions.get(sid);
    if (!cur) return;
    // Chỉ dọn nếu socket đóng CHÍNH là socket đang giữ (chống race khi thay kết nối cùng session).
    if (role === 'worker' && cur.worker !== socket) return;
    if (role === 'controller' && cur.controller !== socket) return;
    const peer = role === 'worker' ? cur.controller : cur.worker;
    if (peer) {
      try {
        peer.close(1000, 'đầu kia của cầu CDP đã đóng');
      } catch {
        /* ignore */
      }
    }
    this.sessions.delete(sid);
    this.logger.info({ session: sid, role }, 'CDP relay: cầu đóng, dọn session');
  }
}
