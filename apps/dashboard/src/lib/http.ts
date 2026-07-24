// Client HTTP dùng AXIOS cho bề mặt điều khiển (chạy login, nạp pool…). Trả cùng shape `ApiResult` như sendJson
// để tương thích. validateStatus=true: KHÔNG ném khi 4xx/5xx (lệnh thất bại vẫn trả 200 {ok:false}) → tầng gọi
// tự xét ok. KHÔNG log/không đọc cookie-credential ở response (INV-12 — endpoint điều khiển không trả secret).
import axios, { type AxiosError } from 'axios';
import type { ApiResult } from './api.js';

export const http = axios.create({
  headers: { 'Content-Type': 'application/json' },
  validateStatus: () => true, // tự xét ok theo status + body.ok, không ném lỗi theo HTTP status
  timeout: 240_000, // login (nhất là nhánh mở Outlook lấy mã email) có thể lâu — nới rộng để không cắt sớm
});

/** POST JSON qua axios → ApiResult {ok,status,data}. Lỗi mạng/timeout → ok=false + thông điệp (không nuốt). */
export async function postJson(url: string, body?: unknown): Promise<ApiResult> {
  try {
    const res = await http.post(url, body ?? {});
    return { ok: res.status >= 200 && res.status < 300, status: res.status, data: res.data };
  } catch (e) {
    const err = e as AxiosError;
    return {
      ok: false,
      status: err.response?.status ?? 0,
      data: err.response?.data ?? err.message,
    };
  }
}
