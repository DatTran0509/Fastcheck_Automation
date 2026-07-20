import { useCallback, useEffect, useState } from 'react';
import type { StationProfileView } from '@fastcheck/contracts';
import { ORCH_BASE, sendJson } from '../lib/api.js';
import { useSnapshot } from '../lib/snapshot.js';
import { PlatformBadge, StatusBadge, cooldownLeft, recommend } from '../lib/format.js';
import { AccountControls } from '../components/AccountControls.js';

export function ProfilesPage(): JSX.Element {
  const { snap } = useSnapshot();
  const stations = snap?.stations ?? [];
  const [sid, setSid] = useState<string>('');
  const [rows, setRows] = useState<StationProfileView[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  // Chọn station đầu tiên khi có danh sách (thường chỉ 1 dev-station).
  useEffect(() => {
    if (!sid && stations.length > 0) setSid(stations[0].station_id);
  }, [stations, sid]);

  const load = useCallback(async () => {
    if (!sid) return;
    setLoading(true);
    setErr(null);
    try {
      const res = await fetch(`${ORCH_BASE}/stations/${sid}/profiles`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setRows((await res.json()) as StationProfileView[]);
    } catch (e) {
      setErr((e as Error).message);
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [sid]);

  // Tải khi đổi station + tự làm mới mỗi 8s (cooldown/health thay đổi realtime).
  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 8000);
    return () => clearInterval(t);
  }, [load]);

  // Xoá profile GemLogin (Server → Client `profile.delete`): worker xoá ở GemLogin, untrack, rồi đồng bộ lại
  // → pool tự loại profile đã biến mất. THAO TÁC PHÁ HUỶ → xác nhận trước (xoá cả profile thật trên GemLogin).
  const remove = async (p: StationProfileView): Promise<void> => {
    if (!sid || !p.gemlogin_profile_id) return;
    const nhan = p.account_label ?? p.gemlogin_profile_id;
    if (!window.confirm(`Xoá profile "${nhan}" (GemLogin id ${p.gemlogin_profile_id})?\nProfile sẽ bị xoá khỏi GemLogin trên máy trạm — không thể hoàn tác.`))
      return;
    setDeleting(p.profile_id);
    setErr(null);
    try {
      const r = await sendJson('DELETE', `${ORCH_BASE}/stations/${sid}/profiles/${p.gemlogin_profile_id}`);
      // Lệnh THẤT BẠI vẫn trả HTTP 200 với body {ok:false, detail} (chỉ station-offline mới 503) → PHẢI đọc
      // ok cấp-lệnh, không chỉ HTTP. Vd GemLogin free: detail "gemlogin_error:...free version...".
      const cmd = (r.data ?? {}) as { ok?: boolean; detail?: string | null };
      if (!r.ok || cmd.ok === false) {
        const detail = cmd.detail ?? `HTTP ${r.status}`;
        // GemLogin bản Free chặn xoá qua API → hướng dẫn xoá tay (sync sẽ tự prune khỏi pool).
        throw new Error(
          /free version/i.test(detail)
            ? 'GemLogin bản Free không xoá được qua API. Xoá profile trực tiếp trong app GemLogin — hệ thống sẽ tự gỡ khỏi pool ở lần đồng bộ kế.'
            : detail,
        );
      }
      await load(); // worker đồng bộ lại sau CRUD; auto-refresh 8s sẽ bắt kịp nếu sync trễ.
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div className="grid">
      <div className="hint-box">
        <b>Pool</b> = bản sao (mirror) profile GemLogin trên máy. Nạp/đăng nhập ở khối trên; danh sách pool ở dưới
        (tự làm mới sau khi nạp). Profile <b>chưa gán nền tảng</b> hiện "chưa gán". Cột <b>Lý do</b> giải thích vì sao
        profile bị COOLDOWN/DEAD + khuyến nghị.
      </div>

      <AccountControls onRegistered={() => void load()} />

      <section className="card">
        <div className="card-head">
          <h2>Profile trong pool</h2>
          <div className="row">
            <select value={sid} onChange={(e) => setSid(e.target.value)} style={{ width: 'auto' }}>
              {stations.length === 0 && <option value="">— chưa có station —</option>}
              {stations.map((s) => (
                <option key={s.station_id} value={s.station_id}>
                  {s.name ?? s.station_id.slice(0, 8)}
                </option>
              ))}
            </select>
            <button className="sm" onClick={() => void load()} disabled={loading || !sid}>
              {loading ? <span className="spinner" /> : '↻'} Làm mới
            </button>
          </div>
        </div>

        {err && <div className="alert critical">Lỗi tải profile: {err}</div>}

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Nền tảng</th>
                <th>GemLogin id</th>
                <th>Nhãn</th>
                <th>Trạng thái</th>
                <th>Health</th>
                <th>Cooldown</th>
                <th>Cookie</th>
                <th>Lý do / khuyến nghị</th>
                <th>Thao tác</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && !loading && (
                <tr>
                  <td colSpan={9} className="empty">
                    {sid ? 'Pool trống — vào Tài khoản để nạp profile.' : 'Chọn một station.'}
                  </td>
                </tr>
              )}
              {rows.map((p) => {
                const left = cooldownLeft(p.cooldown_until);
                const rec = recommend(p.status_reason, p.status);
                return (
                  <tr key={p.profile_id}>
                    <td>
                      <PlatformBadge platform={p.platform} />
                    </td>
                    <td className="mono">{p.gemlogin_profile_id ?? '—'}</td>
                    <td>{p.account_label ?? <span className="muted">—</span>}</td>
                    <td>
                      <StatusBadge status={p.status} />
                    </td>
                    <td className="mono">
                      {p.health_score}
                      {p.consecutive_fails > 0 && (
                        <span className="muted"> · {p.consecutive_fails} fail</span>
                      )}
                    </td>
                    <td className="mono">{left ? <span className="badge cooldown">{left}</span> : '—'}</td>
                    <td>{p.has_cookie ? <span className="badge ok">có</span> : <span className="muted">—</span>}</td>
                    <td>
                      {p.status_reason ? (
                        <div className="reason">
                          {p.status_reason}
                          {rec && <span className="rec">→ {rec}</span>}
                        </div>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      <button
                        className="sm danger"
                        onClick={() => void remove(p)}
                        disabled={deleting != null || !p.gemlogin_profile_id}
                        title={p.gemlogin_profile_id ? 'Xoá profile khỏi GemLogin + pool' : 'Không có GemLogin id để xoá'}
                      >
                        {deleting === p.profile_id ? <span className="spinner" /> : '🗑'} Xoá
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
