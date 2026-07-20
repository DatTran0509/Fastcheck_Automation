import type { IncomingMessage } from 'node:http';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dashboard dev: proxy tới orchestrator (:3002) + API (:3001) để nút bấm hoạt động mà không lo CORS/SPA
// fallback. Bản build tĩnh trỏ thẳng qua VITE_ORCH_URL / VITE_API_URL. KHÔNG endpoint nào trả cookie (INV-12).
const ORCH = process.env.VITE_ORCH_PROXY ?? 'http://127.0.0.1:3002';
const API = process.env.VITE_API_PROXY ?? 'http://127.0.0.1:3001';

// Một số path proxy TRÙNG tên với route SPA (vd `/stations` vừa là route React Router vừa là endpoint
// orchestrator). Reload/bookmark `localhost:5173/stations` là điều hướng TRÌNH DUYỆT (Accept: text/html) →
// nếu proxy thẳng thì thấy JSON thô thay vì app. Chỉ proxy khi là fetch/XHR/SSE (không kèm text/html);
// điều hướng trình duyệt → trả về SPA (/index.html) để React Router tự route. fetch mặc định Accept `*/*`,
// EventSource `text/event-stream` → vẫn được proxy đúng; chỉ reload trên thanh địa chỉ mới nhận index.html.
const spaFallbackForBrowserNav = (req: IncomingMessage): string | undefined =>
  req.headers.accept?.includes('text/html') ? '/index.html' : undefined;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/dashboard': { target: ORCH, changeOrigin: true, bypass: spaFallbackForBrowserNav },
      '/metrics': { target: ORCH, changeOrigin: true, bypass: spaFallbackForBrowserNav },
      // Bề mặt ĐIỀU KHIỂN (mục 2): forward tới orchestrator để nút bấm hoạt động — nhưng reload trên
      // đường dẫn trùng route SPA phải trả app, không phải JSON (xem spaFallbackForBrowserNav).
      '/stations': { target: ORCH, changeOrigin: true, bypass: spaFallbackForBrowserNav },
      '/accounts': { target: ORCH, changeOrigin: true, bypass: spaFallbackForBrowserNav },
      // Gửi check → API service (:3001).
      '/check': { target: API, changeOrigin: true, bypass: spaFallbackForBrowserNav },
    },
  },
});
