import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useSnapshot } from '../lib/snapshot.js';
import { platformIcon } from '../lib/format.js';

const C = {
  live: '#22c55e',
  dead: '#ef4444',
  inconclusive: '#f59e0b',
  blocked: '#a855f7',
};
const tooltipStyle = {
  background: '#1a2138',
  border: '1px solid #28324f',
  borderRadius: 8,
  color: '#e7eaf3',
  fontSize: 13,
};

export function OverviewPage(): JSX.Element {
  const { snap } = useSnapshot();
  const ratios = snap?.ratios ?? [];
  const stations = snap?.stations ?? [];
  const alerts = snap?.alerts ?? [];
  const circuits = (snap?.circuits ?? []).filter((c) => c.open);

  const tot = ratios.reduce(
    (a, r) => ({
      live: a.live + r.live,
      dead: a.dead + r.dead,
      inconclusive: a.inconclusive + r.inconclusive,
      blocked: a.blocked + r.blocked,
      total: a.total + r.total,
    }),
    { live: 0, dead: 0, inconclusive: 0, blocked: 0, total: 0 },
  );
  const onlineStations = stations.filter((s) => s.status === 'ONLINE').length;
  const pct = (n: number) => (tot.total ? `${Math.round((n / tot.total) * 100)}%` : '—');

  const pieData = [
    { name: 'LIVE', value: tot.live, color: C.live },
    { name: 'DEAD', value: tot.dead, color: C.dead },
    { name: 'INCONCLUSIVE', value: tot.inconclusive, color: C.inconclusive },
  ].filter((d) => d.value > 0);

  const barData = ratios.map((r) => ({
    name: `${platformIcon(r.platform)} ${r.platform}`,
    LIVE: r.live,
    DEAD: r.dead,
    INCONCLUSIVE: r.inconclusive,
    BLOCKED: r.blocked,
  }));

  return (
    <div>
      {(alerts.length > 0 || circuits.length > 0) && (
        <div className="alert-stack">
          {circuits.map((c) => (
            <div key={c.platform} className="alert critical">
              ⛔ Circuit MỞ: <b>{c.platform}</b> — chặn {c.retry_after_seconds}s để bảo vệ pool
            </div>
          ))}
          {alerts.map((a, i) => (
            <div key={i} className={`alert ${a.level}`}>
              {a.level === 'critical' ? '⛔' : '⚠️'} {a.message}
            </div>
          ))}
        </div>
      )}

      <div className="kpis">
        <div className="kpi live">
          <div className="kpi-label">LIVE</div>
          <div className="kpi-value">{tot.live}</div>
          <div className="kpi-foot">{pct(tot.live)} tổng kết quả</div>
        </div>
        <div className="kpi dead">
          <div className="kpi-label">DEAD</div>
          <div className="kpi-value">{tot.dead}</div>
          <div className="kpi-foot">{pct(tot.dead)} tổng kết quả</div>
        </div>
        <div className="kpi inconclusive">
          <div className="kpi-label">INCONCLUSIVE</div>
          <div className="kpi-value">{tot.inconclusive}</div>
          <div className="kpi-foot">{pct(tot.inconclusive)} — cần xem lại</div>
        </div>
        <div className="kpi blocked">
          <div className="kpi-label">BLOCKED</div>
          <div className="kpi-value">{tot.blocked}</div>
          <div className="kpi-foot">lần profile bị chặn</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Station online</div>
          <div className="kpi-value">{onlineStations}/{stations.length}</div>
          <div className="kpi-foot">máy trạm kết nối</div>
        </div>
      </div>

      <div className="grid grid-2">
        <section className="card">
          <div className="card-head">
            <h2>Tỷ lệ kết quả</h2>
            <span className="card-hint">LIVE / DEAD / INCONCLUSIVE (INV-1)</span>
          </div>
          {pieData.length === 0 ? (
            <div className="empty">Chưa có dữ liệu check</div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <PieChart>
                <Pie data={pieData} dataKey="value" nameKey="name" innerRadius={64} outerRadius={100} paddingAngle={2}>
                  {pieData.map((d) => (
                    <Cell key={d.name} fill={d.color} stroke="#0a0e1a" strokeWidth={2} />
                  ))}
                </Pie>
                <Tooltip contentStyle={tooltipStyle} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
          )}
        </section>

        <section className="card">
          <div className="card-head">
            <h2>Kết quả theo nền tảng</h2>
            <span className="card-hint">FB · TT · X · YT</span>
          </div>
          {barData.length === 0 ? (
            <div className="empty">Chưa có dữ liệu check</div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={barData} barCategoryGap="22%">
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2740" vertical={false} />
                <XAxis dataKey="name" tick={{ fill: '#8a93ab', fontSize: 12 }} axisLine={{ stroke: '#28324f' }} />
                <YAxis allowDecimals={false} tick={{ fill: '#8a93ab', fontSize: 12 }} axisLine={{ stroke: '#28324f' }} />
                <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="LIVE" stackId="a" fill={C.live} radius={[0, 0, 0, 0]} />
                <Bar dataKey="DEAD" stackId="a" fill={C.dead} />
                <Bar dataKey="INCONCLUSIVE" stackId="a" fill={C.inconclusive} />
                <Bar dataKey="BLOCKED" stackId="a" fill={C.blocked} radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </section>
      </div>
    </div>
  );
}
