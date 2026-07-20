import { useSnapshot } from '../lib/snapshot.js';
import { PlatformBadge, StatusBadge, fmtTime } from '../lib/format.js';

export function StationsPage(): JSX.Element {
  const { snap } = useSnapshot();
  const stations = snap?.stations ?? [];
  const circuits = snap?.circuits ?? [];

  return (
    <div className="grid">
      <section className="card">
        <div className="card-head">
          <h2>Máy trạm ({stations.length})</h2>
          <span className="card-hint">Đăng ký = mở Station Management (§1)</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Tên</th>
                <th>Trạng thái</th>
                <th>Tải / Công suất</th>
                <th>Agent</th>
                <th>RAM (MB)</th>
                <th>CPU %</th>
                <th>Ping cuối</th>
              </tr>
            </thead>
            <tbody>
              {stations.length === 0 && (
                <tr>
                  <td colSpan={7} className="empty">
                    Chưa có station nào kết nối. Chạy <code>pnpm dev:worker</code> trên máy trạm.
                  </td>
                </tr>
              )}
              {stations.map((s) => {
                const load = s.max_concurrency ? s.current_load / s.max_concurrency : 0;
                return (
                  <tr key={s.station_id}>
                    <td>
                      <b>{s.name ?? s.station_id.slice(0, 8)}</b>
                    </td>
                    <td>
                      <StatusBadge status={s.status} />
                    </td>
                    <td className="mono">
                      {s.current_load} / {s.max_concurrency}
                      {load >= 1 && <span className="badge cooldown" style={{ marginLeft: 8 }}>đầy tải</span>}
                    </td>
                    <td className="muted">{s.agent_version ?? '—'}</td>
                    <td className="mono">{s.ram_mb ?? '—'}</td>
                    <td className="mono">{s.cpu_percent ?? '—'}</td>
                    <td className="muted">{fmtTime(s.last_ping_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Circuit breaker theo nền tảng</h2>
          <span className="card-hint">MỞ = tạm chặn platform để bảo vệ pool (§10.6)</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Nền tảng</th>
                <th>Trạng thái</th>
                <th>Thử lại sau</th>
              </tr>
            </thead>
            <tbody>
              {circuits.map((c) => (
                <tr key={c.platform}>
                  <td>
                    <PlatformBadge platform={c.platform} />
                  </td>
                  <td>
                    <span className={`badge ${c.open ? 'blocked' : 'done'}`}>
                      <span className="dot" />
                      {c.open ? 'MỞ (đang chặn)' : 'ĐÓNG (bình thường)'}
                    </span>
                  </td>
                  <td className="mono">{c.open ? `${c.retry_after_seconds}s` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
