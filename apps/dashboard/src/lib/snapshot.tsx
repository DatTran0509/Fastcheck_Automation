// Một kết nối SSE dùng chung cho toàn app (không mỗi trang mở một EventSource). Snapshot realtime từ
// orchestrator (§6.9) — chỉ dữ liệu vận hành, không cookie (INV-12).
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import type { DashboardSnapshot } from '@fastcheck/contracts';
import { ORCH_BASE } from './api.js';

interface SnapshotState {
  snap: DashboardSnapshot | null;
  live: boolean;
}

const Ctx = createContext<SnapshotState>({ snap: null, live: false });

export function SnapshotProvider({ children }: { children: ReactNode }): JSX.Element {
  const [snap, setSnap] = useState<DashboardSnapshot | null>(null);
  const [live, setLive] = useState(false);

  useEffect(() => {
    const es = new EventSource(`${ORCH_BASE}/dashboard/stream`);
    es.onopen = () => setLive(true);
    es.onmessage = (ev) => {
      try {
        setSnap(JSON.parse(ev.data) as DashboardSnapshot);
        setLive(true);
      } catch {
        setLive(false);
      }
    };
    es.onerror = () => setLive(false);
    return () => es.close();
  }, []);

  return <Ctx.Provider value={{ snap, live }}>{children}</Ctx.Provider>;
}

export function useSnapshot(): SnapshotState {
  return useContext(Ctx);
}
