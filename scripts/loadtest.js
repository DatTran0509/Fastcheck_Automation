// FastCheck load test (k6) — Hạng mục KPI "≥50 request đồng thời không crash" (spec §4.3, §10.4).
//
// Chạy:  k6 run -e API=http://127.0.0.1:3001 -e VUS=50 -e DURATION=30s scripts/loadtest.js
//
// Đo ở TẦNG API (nhận→đẩy queue→trả 202). "50 concurrent ở API" (dễ) khác "50 check thật song song"
// (khó — số browser giới hạn bởi tổng max_concurrency các station). Backpressure: tải vượt công suất thì
// job XẾP HÀNG trong queue, hệ thống giảm tốc chứ KHÔNG sập (INV-10). URL duy nhất mỗi request để tránh
// dedupe/cache gộp thành một job. GEMLOGIN_MODE=fake để đo pipeline không cần GemLogin thật.
//
// k6 chưa cài trong môi trường dev này → dùng scripts/e2e_phase4_loadtest.py (driver asyncio tương đương)
// để đo thực tế; file này là artifact chuẩn để chạy trên máy có k6.

import http from 'k6/http';
import { check } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const API = __ENV.API || 'http://127.0.0.1:3001';
const VUS = parseInt(__ENV.VUS || '50', 10);
const DURATION = __ENV.DURATION || '30s';

const accepted = new Counter('checks_accepted'); // số job nhận (HTTP 202)
const postLatency = new Trend('post_latency_ms', true);

export const options = {
  scenarios: {
    concurrent: {
      executor: 'constant-vus',
      vus: VUS,
      duration: DURATION,
    },
  },
  thresholds: {
    // KPI: API trả nhanh khi chỉ nhận job (đẩy queue). Cache-hit <500ms; nhận job cũng phải nhanh.
    http_req_duration: ['p(95)<500'],
    http_req_failed: ['rate<0.01'], // không sập dưới tải
  },
};

export default function () {
  // URL duy nhất theo VU + lần lặp → job riêng (không dedupe). Token 'live' → fixture LIVE ở fake mode.
  const unique = `${__VU}-${__ITER}-${Date.now()}`;
  const url = `https://www.tiktok.com/@fc/video/live-${unique}`;
  const res = http.post(`${API}/check`, JSON.stringify({ url }), {
    headers: { 'Content-Type': 'application/json' },
  });
  postLatency.add(res.timings.duration);
  check(res, { 'accepted (202)': (r) => r.status === 202 });
  if (res.status === 202) accepted.add(1);
}
