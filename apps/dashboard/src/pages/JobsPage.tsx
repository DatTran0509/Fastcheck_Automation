import { useCallback, useEffect, useRef, useState } from 'react';
import * as XLSX from 'xlsx';
import type { JobHistoryItem } from '@fastcheck/contracts';
import { API_BASE, JOB_STATUSES, PLATFORMS, fetchJobs, sendJson } from '../lib/api.js';
import { useSnapshot } from '../lib/snapshot.js';
import { PlatformBadge, StatusBadge, fmtDateTime, fmtMs, fmtTime, jobsToRows } from '../lib/format.js';

const PAGE = 50;
const EXPORT_MAX = 5000;

export function JobsPage(): JSX.Element {
  const { snap } = useSnapshot();
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [platform, setPlatform] = useState('');
  const [status, setStatus] = useState('');
  const [items, setItems] = useState<JobHistoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  // Ref số dòng đang tải (tránh stale closure) — refresh làm mới ĐÚNG cửa sổ đang xem, không reset về 1 trang.
  const itemsRef = useRef<JobHistoryItem[]>([]);
  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  // Gửi check nhanh (cùng trang kết quả) — gửi tới API service (:3001).
  const [url, setUrl] = useState('');
  const [checking, setChecking] = useState(false);
  const [checkMsg, setCheckMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q.trim()), 350);
    return () => clearTimeout(t);
  }, [q]);

  const loadPage = useCallback(
    async (offset: number, replace: boolean, limit: number = PAGE) => {
      setLoading(true);
      setErr(null);
      try {
        const res = await fetchJobs({
          q: debouncedQ || undefined,
          platform: platform || undefined,
          status: status || undefined,
          limit,
          offset,
        });
        setTotal(res.total);
        setItems((prev) => (replace ? res.items : [...prev, ...res.items]));
        setUpdatedAt(new Date().toISOString());
      } catch (e) {
        setErr((e as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [debouncedQ, platform, status],
  );

  // Làm mới TẠI CHỖ: tải lại từ đầu ĐÚNG số dòng đang xem (giữ filter + vị trí cuộn) — dùng cho realtime + nút Tải lại.
  const refresh = useCallback(() => {
    const count = Math.max(PAGE, itemsRef.current.length);
    void loadPage(0, true, count);
  }, [loadPage]);

  // Đổi filter → reset danh sách + tải trang đầu (loadPage đổi identity khi filter đổi).
  useEffect(() => {
    setItems([]);
    setTotal(0);
    void loadPage(0, true);
  }, [loadPage]);

  // Realtime: orchestrator đẩy snapshot (SSE) chứa recent_jobs. Khi có job MỚI hoặc ĐỔI TRẠNG THÁI
  // (RUNNING→DONE, result LIVE/DEAD, retry…) → làm mới bảng tại chỗ, không cần load lại trang.
  const jobsSig = (snap?.recent_jobs ?? [])
    .map((j) => `${j.trace_id}:${j.status}:${j.result ?? ''}:${j.retry_count}`)
    .join('|');
  const prevSig = useRef<string | null>(null);
  useEffect(() => {
    if (prevSig.current !== null && prevSig.current !== jobsSig) refresh();
    prevSig.current = jobsSig;
  }, [jobsSig, refresh]);

  // Lazy-load: cuộn tới sentinel → tải trang kế (IntersectionObserver).
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && !loading && items.length < total) {
          void loadPage(items.length, false);
        }
      },
      { rootMargin: '250px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [loading, items.length, total, loadPage]);

  const exportExcel = async (): Promise<void> => {
    setExporting(true);
    setErr(null);
    try {
      const res = await fetchJobs({
        q: debouncedQ || undefined,
        platform: platform || undefined,
        status: status || undefined,
        limit: EXPORT_MAX,
        offset: 0,
      });
      const ws = XLSX.utils.json_to_sheet(jobsToRows(res.items));
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, 'FastCheck');
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
      XLSX.writeFile(wb, `fastcheck-ketqua-${stamp}.xlsx`);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setExporting(false);
    }
  };

  const sendCheck = async (): Promise<void> => {
    setChecking(true);
    setCheckMsg(null);
    try {
      const r = await sendJson('POST', `${API_BASE}/check`, { url });
      setCheckMsg({ ok: r.ok, text: `HTTP ${r.status} — ${JSON.stringify(r.data)}` });
      if (r.ok) {
        setUrl('');
        setTimeout(() => void loadPage(0, true), 1200); // job vừa tạo → làm mới bảng
      }
    } catch (e) {
      setCheckMsg({ ok: false, text: (e as Error).message });
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="grid">
      <section className="card">
        <div className="card-head">
          <h2>Gửi check</h2>
          <span className="card-hint">→ API service (:3001). Kết quả hiện ở bảng dưới.</span>
        </div>
        <div className="row">
          <input
            style={{ flex: 1, minWidth: 260 }}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://www.tiktok.com/@user/video/123…"
          />
          <button className="primary" disabled={checking || !url} onClick={() => void sendCheck()}>
            {checking ? <span className="spinner" /> : '▶'} Gửi check
          </button>
        </div>
        {checkMsg && (
          <div className={`alert ${checkMsg.ok ? 'warn' : 'critical'}`} style={{ marginTop: 12 }}>
            {checkMsg.ok ? '✅' : '❌'} {checkMsg.text}
          </div>
        )}
      </section>

      <section className="card">
        <div className="toolbar">
        <input
          className="search"
          placeholder="🔎 Tìm theo link hoặc ID (trace_id)…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
          <option value="">Mọi nền tảng</option>
          {PLATFORMS.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Mọi trạng thái</option>
          {JOB_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <button className="sm" onClick={() => refresh()} disabled={loading} title="Tải lại kết quả mới nhất">
          {loading ? <span className="spinner" /> : '↻'} Tải lại
        </button>
        <button className="primary" onClick={() => void exportExcel()} disabled={exporting || total === 0}>
          {exporting ? <span className="spinner" /> : '⬇'} Xuất Excel
        </button>
      </div>

      {err && <div className="alert critical">Lỗi tải kết quả: {err}</div>}

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Nền tảng</th>
              <th>Link đã check</th>
              <th>Job</th>
              <th>Kết quả</th>
              <th>Profile</th>
              <th>Thời gian</th>
              <th>Retry</th>
              <th>Tạo lúc</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && !loading && (
              <tr>
                <td colSpan={9} className="empty">
                  Chưa có job nào khớp bộ lọc.
                </td>
              </tr>
            )}
            {items.map((j) => (
              <tr key={j.trace_id}>
                <td
                  className="mono"
                  title={`trace_id: ${j.trace_id} (bấm để copy)`}
                  style={{ cursor: 'pointer' }}
                  onClick={() => void navigator.clipboard?.writeText(j.trace_id)}
                >
                  {j.trace_id.slice(0, 8)}
                </td>
                <td>
                  <PlatformBadge platform={j.platform} />
                </td>
                <td className="link-cell" title={j.target_url}>
                  <a href={j.target_url} target="_blank" rel="noreferrer">
                    {j.target_url}
                  </a>
                </td>
                <td>
                  <StatusBadge status={j.status} />
                </td>
                <td>{j.result ? <StatusBadge status={j.result} /> : <span className="muted">—</span>}</td>
                <td>
                  {j.profile_health ? <StatusBadge status={j.profile_health} /> : <span className="muted">—</span>}
                  {j.block_reason && <div className="muted mono" style={{ fontSize: 11 }}>{j.block_reason}</div>}
                </td>
                <td className="mono">{fmtMs(j.response_time_ms)}</td>
                <td className="mono">{j.retry_count > 0 ? j.retry_count : '—'}</td>
                <td className="muted">{fmtDateTime(j.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

        <div ref={sentinel} className="sentinel" />
        <div className="pageinfo">
          {loading ? (
            <>
              <span className="spinner" /> đang tải…
            </>
          ) : (
            <>
              Hiển thị {items.length} / {total} job
              <span className="live-dot" title="Tự cập nhật realtime khi job đổi trạng thái">
                ● realtime
              </span>
              {updatedAt && <span className="muted"> · cập nhật {fmtTime(updatedAt)}</span>}
            </>
          )}
        </div>
      </section>
    </div>
  );
}
