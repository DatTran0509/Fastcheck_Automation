// Circuit breaker theo platform (§10.6) — KEY Redis dùng chung giữa orchestrator (ghi trạng thái) và
// API (đọc để trả 503). Đặt schema key MỘT nơi để hai service không lệch nhau. State ở Redis là trí nhớ
// ngắn hạn (INV-5): mất Redis → circuit reset về đóng (bảo vệ ít hơn, KHÔNG trả sai kết quả).

import type { Platform } from './enums.js';

export const CIRCUIT_PREFIX = 'cb';

export interface CircuitKeys {
  /** Mốc thời gian (epoch ms) circuit MỞ tới. Có + còn hạn = OPEN → API trả 503. */
  openUntil: string;
  /** ZSET tổng số kết quả trong cửa sổ trượt (score = ts ms). */
  total: string;
  /** ZSET số kết quả BLOCKED/lỗi trong cửa sổ trượt. */
  bad: string;
}

export function circuitKeys(platform: Platform | string): CircuitKeys {
  return {
    openUntil: `${CIRCUIT_PREFIX}:${platform}:open_until`,
    total: `${CIRCUIT_PREFIX}:${platform}:total`,
    bad: `${CIRCUIT_PREFIX}:${platform}:bad`,
  };
}

export type CircuitState = 'CLOSED' | 'OPEN' | 'HALF_OPEN';
