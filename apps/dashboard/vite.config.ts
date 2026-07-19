import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dashboard dev: proxy tới orchestrator (:3002) + API (:3001) để nút bấm hoạt động mà không lo CORS/SPA
// fallback. Bản build tĩnh trỏ thẳng qua VITE_ORCH_URL / VITE_API_URL. KHÔNG endpoint nào trả cookie (INV-12).
const ORCH = process.env.VITE_ORCH_PROXY ?? 'http://127.0.0.1:3002';
const API = process.env.VITE_API_PROXY ?? 'http://127.0.0.1:3001';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/dashboard': { target: ORCH, changeOrigin: true },
      '/metrics': { target: ORCH, changeOrigin: true },
      // Bề mặt ĐIỀU KHIỂN (mục 2): forward tới orchestrator để nút bấm hoạt động (không lẫn SPA fallback).
      '/stations': { target: ORCH, changeOrigin: true },
      '/accounts': { target: ORCH, changeOrigin: true },
      // Gửi check → API service (:3001).
      '/check': { target: API, changeOrigin: true },
    },
  },
});
