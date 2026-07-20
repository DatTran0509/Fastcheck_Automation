import { Injectable } from '@nestjs/common';
import type { CommandAckMessage } from '@fastcheck/contracts';

interface Pending {
  stationId: string;
  resolve: (ack: CommandAckMessage) => void;
  timer: ReturnType<typeof setTimeout>;
}

/**
 * Tương quan LỆNH gửi xuống station ↔ `command_ack` trả về (khoá theo `command_id` — INV-14).
 * Bề mặt điều khiển (REST) cần chờ kết quả một lệnh (mở browser, tạo profile, chạy login) đồng bộ; WS vốn
 * một chiều nên ở đây bắc cầu request→response: register(command_id) trả Promise, gateway gọi resolve khi ack
 * tới. Có TIMEOUT để không treo mãi khi station không phản hồi (fail loud, không nuốt — error-handling rule).
 */
@Injectable()
export class PendingCommandsService {
  private readonly pending = new Map<string, Pending>();

  /**
   * Chờ ack cho `commandId` (thuộc `stationId`), tối đa `timeoutMs`. Quá hạn → RESOLVE ack ok=false (không
   * throw): lệnh chạy lâu (vd login-by-info bị X challenge/captcha) là KẾT QUẢ CÓ NGHĨA, không phải lỗi server.
   * Trả ok=false + lý do rõ để dashboard hiện ❌ (thay vì HTTP 500 khó hiểu). Giống rejectStation (fail loud).
   */
  waitFor(commandId: string, stationId: string, timeoutMs: number): Promise<CommandAckMessage> {
    return new Promise<CommandAckMessage>((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(commandId);
        resolve({
          type: 'command_ack',
          command_id: commandId,
          station_id: stationId,
          ok: false,
          detail: `command_ack_timeout: station không phản hồi trong ${timeoutMs}ms (lệnh có thể vẫn đang chạy — vd login bị challenge/captcha; cân nhắc dùng cookie)`,
          profile_id: null,
        });
      }, timeoutMs);
      this.pending.set(commandId, { stationId, resolve, timer });
    });
  }

  /** Gateway gọi khi nhận `command_ack` — khớp về Promise đang chờ (nếu có). */
  resolve(ack: CommandAckMessage): void {
    const p = this.pending.get(ack.command_id);
    if (!p) return; // ack của lệnh do luồng dispatch tự động gửi (không ai chờ) — bỏ qua yên lặng
    clearTimeout(p.timer);
    this.pending.delete(ack.command_id);
    p.resolve(ack);
  }

  /**
   * Station rớt → giải phóng các Promise đang chờ CỦA RIÊNG station đó (không đụng station khác) bằng ack
   * ok=false, để REST trả lỗi có nghĩa ngay thay vì treo tới timeout. Giữ đúng kiểu trả về (không ném).
   */
  rejectStation(stationId: string, reason: string): void {
    for (const [commandId, p] of this.pending) {
      if (p.stationId !== stationId) continue;
      clearTimeout(p.timer);
      this.pending.delete(commandId);
      p.resolve({
        type: 'command_ack',
        command_id: commandId,
        station_id: stationId,
        ok: false,
        detail: reason,
        profile_id: null,
      });
    }
  }
}
