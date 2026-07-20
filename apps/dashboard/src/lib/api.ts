// Client gọi bề mặt điều khiển orchestrator (:3002 qua proxy) + API check (:3001). KHÔNG endpoint nào trả
// cookie/credential (INV-12). Dev: đường tương đối qua Vite proxy; build tĩnh: đặt VITE_ORCH_URL / VITE_API_URL.
import type { JobHistoryResponse } from '@fastcheck/contracts';

const env = import.meta.env as Record<string, string | undefined>;
export const ORCH_BASE = env.VITE_ORCH_URL ?? '';
export const API_BASE = env.VITE_API_URL ?? '';

export interface ApiResult {
  ok: boolean;
  status: number;
  data: unknown;
}

/** Gửi JSON tới một endpoint; đọc body MỘT LẦN (tránh "body stream already read"). */
export async function sendJson(method: string, url: string, body?: unknown): Promise<ApiResult> {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const raw = await res.text();
  let data: unknown;
  try {
    data = raw ? JSON.parse(raw) : null;
  } catch {
    data = raw;
  }
  return { ok: res.ok, status: res.status, data };
}

export interface JobsQuery {
  platform?: string;
  status?: string;
  q?: string;
  limit: number;
  offset: number;
}

/** Lịch sử job có filter + phân trang (bảng Kết quả). */
export async function fetchJobs(query: JobsQuery): Promise<JobHistoryResponse> {
  const p = new URLSearchParams();
  if (query.platform) p.set('platform', query.platform);
  if (query.status) p.set('status', query.status);
  if (query.q) p.set('q', query.q);
  p.set('limit', String(query.limit));
  p.set('offset', String(query.offset));
  const res = await fetch(`${ORCH_BASE}/dashboard/jobs?${p.toString()}`);
  if (!res.ok) throw new Error(`GET /dashboard/jobs → HTTP ${res.status}`);
  return (await res.json()) as JobHistoryResponse;
}

export const PLATFORMS = ['TIKTOK', 'FACEBOOK', 'TWITTER', 'YOUTUBE'] as const;
export const JOB_STATUSES = ['PENDING', 'RUNNING', 'DONE', 'DEAD_LETTER'] as const;
