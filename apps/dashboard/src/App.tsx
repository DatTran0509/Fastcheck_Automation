import { useEffect, useState } from 'react';
// CHỈ import type (erase lúc build) → không kéo runtime Node (pino/zod) vào bundle trình duyệt.
import type {
  DashboardSnapshot,
  DashboardRatio,
  DashboardStation,
} from '@fastcheck/contracts';

// Dev: rỗng → dùng đường tương đối (vite proxy tới orchestrator). Build tĩnh: đặt VITE_ORCH_URL.
const BASE = (import.meta.env as Record<string, string | undefined>).VITE_ORCH_URL ?? '';
// API service (POST /check) ở cổng riêng (3001). Dev: rỗng → đường tương đối qua Vite proxy (khỏi CORS).
// Build tĩnh: đặt VITE_API_URL trỏ thẳng API.
const API_BASE = (import.meta.env as Record<string, string | undefined>).VITE_API_URL ?? '';

const PLATFORMS = ['TIKTOK', 'FACEBOOK', 'TWITTER', 'YOUTUBE'] as const;

/** Gửi JSON tới một endpoint điều khiển; trả {ok,status,data} để hiển thị (KHÔNG chứa cookie — INV-12). */
async function sendJson(
  method: string,
  url: string,
  body?: unknown,
): Promise<{ ok: boolean; status: number; data: unknown }> {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  // Đọc body MỘT LẦN (text) rồi mới parse — body stream chỉ đọc được một lần (json() fail → text() sẽ ném
  // "body stream already read"). Parse text an toàn cho cả JSON lẫn body rỗng.
  const raw = await res.text();
  let data: unknown;
  try {
    data = raw ? JSON.parse(raw) : null;
  } catch {
    data = raw;
  }
  return { ok: res.ok, status: res.status, data };
}

// View lỏng (platform là string) để hiển thị mà không cần import runtime enum Platform (chỉ dùng type).
type RatioView = {
  platform: string;
  live: number;
  dead: number;
  inconclusive: number;
  blocked: number;
  total: number;
};

function useSnapshot(): { snap: DashboardSnapshot | null; live: boolean } {
  const [snap, setSnap] = useState<DashboardSnapshot | null>(null);
  const [live, setLive] = useState(false);
  useEffect(() => {
    const es = new EventSource(`${BASE}/dashboard/stream`);
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
  return { snap, live };
}

function StatusBadge({ status }: { status: string }): JSX.Element {
  return <span className={`badge b-${status.toLowerCase()}`}>{status}</span>;
}

function StationsPanel({ stations }: { stations: DashboardStation[] }): JSX.Element {
  return (
    <section className="card">
      <h2>Station ({stations.length})</h2>
      <table>
        <thead>
          <tr>
            <th>Tên</th>
            <th>Trạng thái</th>
            <th>Tải</th>
            <th>Agent</th>
            <th>RAM (MB)</th>
            <th>CPU %</th>
            <th>Ping cuối</th>
          </tr>
        </thead>
        <tbody>
          {stations.length === 0 && (
            <tr>
              <td colSpan={7} className="muted">
                chưa có station online
              </td>
            </tr>
          )}
          {stations.map((s) => (
            <tr key={s.station_id}>
              <td>{s.name ?? s.station_id.slice(0, 8)}</td>
              <td>
                <StatusBadge status={s.status} />
              </td>
              <td>
                {s.current_load}/{s.max_concurrency}
              </td>
              <td className="muted">{s.agent_version ?? '—'}</td>
              <td>{s.ram_mb != null ? s.ram_mb.toFixed(0) : '—'}</td>
              <td>{s.cpu_percent != null ? s.cpu_percent.toFixed(0) : '—'}</td>
              <td className="muted">
                {s.last_ping_at ? new Date(s.last_ping_at).toLocaleTimeString() : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

/** Ba trạng thái LIVE/DEAD/INCONCLUSIVE hiển thị TÁCH BIỆT (INV-1/INV-3 — không gộp). */
function RatioBar({ r }: { r: RatioView }): JSX.Element {
  const t = Math.max(1, r.total);
  const pct = (n: number) => `${((n / t) * 100).toFixed(0)}%`;
  return (
    <div className="ratio-row">
      <div className="ratio-name">{r.platform}</div>
      <div className="ratio-bar" title={`LIVE ${r.live} · DEAD ${r.dead} · INCONCLUSIVE ${r.inconclusive}`}>
        <span className="seg s-live" style={{ width: pct(r.live) }} />
        <span className="seg s-dead" style={{ width: pct(r.dead) }} />
        <span className="seg s-inconclusive" style={{ width: pct(r.inconclusive) }} />
      </div>
      <div className="ratio-nums">
        <span className="n-live">LIVE {r.live}</span>
        <span className="n-dead">DEAD {r.dead}</span>
        <span className="n-inconclusive">INCONCLUSIVE {r.inconclusive}</span>
        <span className="n-blocked">BLOCKED {r.blocked}</span>
      </div>
    </div>
  );
}

function RatiosPanel({ ratios }: { ratios: DashboardRatio[] }): JSX.Element {
  const byPlatform = new Map<string, RatioView>(ratios.map((r) => [r.platform, r]));
  return (
    <section className="card">
      <h2>Tỷ lệ kết quả (theo platform)</h2>
      {PLATFORMS.map((p) => {
        const r: RatioView =
          byPlatform.get(p) ?? { platform: p, live: 0, dead: 0, inconclusive: 0, blocked: 0, total: 0 };
        return <RatioBar key={p} r={r} />;
      })}
    </section>
  );
}

/**
 * Bảng ĐIỀU KHIỂN Station Management (mục 2 Excel) — bấm nút thật: mở/tắt browser GemLogin, CRUD profile,
 * GỌI kịch bản login, nạp tài khoản thật, và gửi check. Gọi REST orchestrator (:3002) + API (:3001).
 * KHÔNG hiển thị cookie/credential trả về (INV-12) — chỉ ok/detail/profile_id.
 */
function ControlPanel({ stations }: { stations: DashboardStation[] }): JSX.Element {
  const [stationId, setStationId] = useState('');
  const [platform, setPlatform] = useState<string>('TIKTOK');
  const [gemId, setGemId] = useState('');
  const [cookie, setCookie] = useState('');
  const [method, setMethod] = useState('COOKIE');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<string>('');

  const sid = stationId || stations[0]?.station_id || '';

  async function act(label: string, fn: () => Promise<{ ok: boolean; status: number; data: unknown }>) {
    setBusy(label);
    setResult(`⏳ ${label}...`);
    try {
      const r = await fn();
      setResult(`${r.ok ? '✅' : '❌'} [${label}] HTTP ${r.status}\n${JSON.stringify(r.data, null, 2)}`);
    } catch (e) {
      setResult(`❌ [${label}] ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card wide">
      <h2>Điều khiển Station (mục 2 — tương tác thật)</h2>
      <div className="ctl-grid">
        <label>
          Station
          <select value={sid} onChange={(e) => setStationId(e.target.value)}>
            {stations.length === 0 && <option value="">(chưa có station)</option>}
            {stations.map((s) => (
              <option key={s.station_id} value={s.station_id}>
                {s.name ?? s.station_id.slice(0, 8)} · {s.status}
              </option>
            ))}
          </select>
        </label>
        <label>
          Platform
          <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
            {PLATFORMS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label>
          GemLogin profile id
          <input value={gemId} onChange={(e) => setGemId(e.target.value)} placeholder="vd 1" />
        </label>
        <label>
          Login method
          <select value={method} onChange={(e) => setMethod(e.target.value)}>
            <option value="COOKIE">COOKIE</option>
            <option value="INFO">INFO (TikTok/X)</option>
          </select>
        </label>
      </div>

      <div className="ctl-grid">
        <label className="wide-field">
          Cookie (JSON hoặc k=v; — để nạp tài khoản / login cookie)
          <textarea value={cookie} onChange={(e) => setCookie(e.target.value)} rows={2} placeholder='[{"name":"sessionid","value":"..."}]' />
        </label>
        <label>
          Username (info)
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" />
        </label>
        <label>
          Password (info)
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="off" />
        </label>
      </div>

      <div className="ctl-actions">
        <button disabled={!sid || !!busy} onClick={() => act('list-profiles', () => sendJson('GET', `${BASE}/stations/${sid}/profiles`))}>
          Xem profile
        </button>
        <button disabled={!sid || !!busy} onClick={() => act('create-profile', () => sendJson('POST', `${BASE}/stations/${sid}/profiles`, { platform, account_label: `fastcheck-${platform}` }))}>
          Tạo profile
        </button>
        <button disabled={!sid || !gemId || !!busy} onClick={() => act('open-browser', () => sendJson('POST', `${BASE}/stations/${sid}/browser/open`, { gemlogin_profile_id: gemId }))}>
          Mở browser
        </button>
        <button disabled={!sid || !gemId || !!busy} onClick={() => act('close-browser', () => sendJson('POST', `${BASE}/stations/${sid}/browser/close`, { gemlogin_profile_id: gemId }))}>
          Tắt browser
        </button>
        <button disabled={!sid || !gemId || !!busy} onClick={() => act('run-login', () => sendJson('POST', `${BASE}/stations/${sid}/login`, { gemlogin_profile_id: gemId, platform, method, cookie: cookie || undefined, username: username || undefined, password: password || undefined }))}>
          Chạy login
        </button>
        <button disabled={!gemId || !!busy} onClick={() => act('register-account', () => sendJson('POST', `${BASE}/accounts`, { platform, gemlogin_profile_id: gemId, station_id: sid || undefined, cookie: cookie || undefined }))}>
          Nạp tài khoản vào pool
        </button>
      </div>

      <div className="ctl-check">
        <input className="check-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://www.tiktok.com/@user/video/123 — check link" />
        <button disabled={!url || !!busy} onClick={() => act('check', () => sendJson('POST', `${API_BASE}/check`, { url }))}>
          Gửi check (→ API :3001)
        </button>
      </div>

      {result && <pre className="ctl-result">{result}</pre>}
    </section>
  );
}

function App(): JSX.Element {
  const { snap, live } = useSnapshot();
  const circuitsOpen = (snap?.circuits ?? []).filter((c) => c.open);

  return (
    <div className="app">
      <header>
        <h1>FastCheck · Dashboard</h1>
        <span className={`conn ${live ? 'on' : 'off'}`}>{live ? 'realtime ●' : 'mất kết nối ○'}</span>
        {snap && <span className="muted ts">cập nhật {new Date(snap.ts).toLocaleTimeString()}</span>}
      </header>

      {(snap?.alerts.length || circuitsOpen.length) ? (
        <section className="alerts">
          {circuitsOpen.map((c) => (
            <div key={c.platform} className="alert critical">
              ⛔ Circuit MỞ: {c.platform} (thử lại sau {c.retry_after_seconds}s)
            </div>
          ))}
          {(snap?.alerts ?? []).map((a, i) => (
            <div key={i} className={`alert ${a.level}`}>
              {a.level === 'critical' ? '⛔' : '⚠️'} {a.message}
            </div>
          ))}
        </section>
      ) : null}

      <div className="grid">
        <ControlPanel stations={snap?.stations ?? []} />
        <StationsPanel stations={snap?.stations ?? []} />
        <RatiosPanel ratios={snap?.ratios ?? []} />

        <section className="card">
          <h2>Pool profile</h2>
          <table>
            <thead>
              <tr>
                <th>Platform</th>
                <th>Trạng thái</th>
                <th>Số lượng</th>
              </tr>
            </thead>
            <tbody>
              {(snap?.pool ?? []).length === 0 && (
                <tr>
                  <td colSpan={3} className="muted">
                    chưa có dữ liệu
                  </td>
                </tr>
              )}
              {(snap?.pool ?? []).map((p) => (
                <tr key={`${p.platform}-${p.status}`}>
                  <td>{p.platform}</td>
                  <td>
                    <StatusBadge status={p.status} />
                  </td>
                  <td>{p.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="card">
          <h2>Circuit breaker</h2>
          <table>
            <thead>
              <tr>
                <th>Platform</th>
                <th>Trạng thái</th>
                <th>Retry after</th>
              </tr>
            </thead>
            <tbody>
              {PLATFORMS.map((p) => {
                const c = (snap?.circuits ?? []).find((x) => (x.platform as string) === p);
                const open = c?.open ?? false;
                return (
                  <tr key={p}>
                    <td>{p}</td>
                    <td>
                      <span className={`badge ${open ? 'b-open' : 'b-closed'}`}>
                        {open ? 'OPEN' : 'CLOSED'}
                      </span>
                    </td>
                    <td>{open ? `${c?.retry_after_seconds ?? 0}s` : '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>

        <section className="card wide">
          <h2>Job gần đây (theo trace_id)</h2>
          <table>
            <thead>
              <tr>
                <th>trace_id</th>
                <th>Platform</th>
                <th>Trạng thái</th>
                <th>Kết quả</th>
                <th>Retry</th>
                <th>Tạo lúc</th>
              </tr>
            </thead>
            <tbody>
              {(snap?.recent_jobs ?? []).length === 0 && (
                <tr>
                  <td colSpan={6} className="muted">
                    chưa có job
                  </td>
                </tr>
              )}
              {(snap?.recent_jobs ?? []).map((j) => (
                <tr key={j.trace_id}>
                  <td className="mono">{j.trace_id.slice(0, 8)}</td>
                  <td>{j.platform}</td>
                  <td>
                    <StatusBadge status={j.status} />
                  </td>
                  <td>{j.result ? <StatusBadge status={j.result} /> : <span className="muted">—</span>}</td>
                  <td>{j.retry_count}</td>
                  <td className="muted">{new Date(j.created_at).toLocaleTimeString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="card wide">
          <h2>Tiến trình job đang chạy (stream)</h2>
          <table>
            <thead>
              <tr>
                <th>trace_id</th>
                <th>Bước</th>
                <th>Chi tiết</th>
                <th>Lúc</th>
              </tr>
            </thead>
            <tbody>
              {(snap?.progress ?? []).length === 0 && (
                <tr>
                  <td colSpan={4} className="muted">
                    chưa có bước tiến trình (chỉ real mode phát: mở browser → login → detect → xong)
                  </td>
                </tr>
              )}
              {(snap?.progress ?? []).map((p, i) => (
                <tr key={`${p.trace_id}-${p.step}-${i}`}>
                  <td className="mono">{p.trace_id.slice(0, 8)}</td>
                  <td>{p.step}</td>
                  <td className="muted">{p.detail ?? '—'}</td>
                  <td className="muted">{new Date(p.ts).toLocaleTimeString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  );
}

export { App };
