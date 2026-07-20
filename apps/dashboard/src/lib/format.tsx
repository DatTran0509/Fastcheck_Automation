// Helper hiển thị dùng chung: badge trạng thái (LIVE/DEAD/INCONCLUSIVE/BLOCKED tách màu — INV-1/3), nhãn
// platform, định dạng thời gian, và KHUYẾN NGHỊ khắc phục khi profile lỗi (để người dùng hiểu, không đoán).

export function StatusBadge({ status }: { status: string | null | undefined }): JSX.Element {
  const s = (status ?? '—').toString();
  return (
    <span className={`badge ${s.toLowerCase()}`}>
      <span className="dot" />
      {s}
    </span>
  );
}

const PLAT_ICON: Record<string, string> = {
  TIKTOK: '🎵',
  FACEBOOK: '📘',
  TWITTER: '𝕏',
  YOUTUBE: '▶️',
};

export function PlatformBadge({ platform }: { platform: string | null | undefined }): JSX.Element {
  if (!platform) return <span className="badge plat muted">chưa gán</span>;
  return (
    <span className="badge plat">
      {PLAT_ICON[platform] ?? '•'} {platform}
    </span>
  );
}

export function platformIcon(platform: string): string {
  return PLAT_ICON[platform] ?? '•';
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString('vi-VN');
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('vi-VN');
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '—';
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

/** Thời gian còn lại của cooldown (giây/phút) từ cooldown_until. */
export function cooldownLeft(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return null;
  const s = Math.ceil(ms / 1000);
  return s >= 60 ? `${Math.ceil(s / 60)} phút` : `${s}s`;
}

/**
 * Khuyến nghị khắc phục dựa trên lý do (last_error) + trạng thái pool. Giúp operator hiểu NÊN LÀM GÌ
 * thay vì chỉ thấy "COOLDOWN". Trả chuỗi tiếng Việt ngắn gọn (null nếu không cần).
 */
export function recommend(reason: string | null | undefined, status: string): string | null {
  const r = (reason ?? '').toUpperCase();
  if (status === 'DEAD')
    return 'Profile bị loại sau nhiều lần lỗi liên tiếp. Kiểm tra tài khoản, đăng nhập lại thật sự rồi "Nạp lại" vào pool.';
  if (r.startsWith('CHALLENGED'))
    return 'Profile chưa đăng nhập đúng nền tảng hoặc cookie đã chết. Vào Tài khoản → "Chạy login" (cookie sống) rồi nạp lại.';
  if (r.startsWith('BLOCKED'))
    return 'Nền tảng chặn (captcha/challenge). Đổi profile/proxy, giảm nhịp check, để profile nghỉ trước khi dùng lại.';
  if (r.startsWith('THROTTLED'))
    return 'GemLogin mở browser không kịp (hạ tầng, không phải lỗi tài khoản). Tự khôi phục sau nghỉ ngắn; giảm số job đồng thời hoặc thêm profile.';
  return null;
}

export function jobsToRows(
  items: {
    trace_id: string;
    target_url: string;
    platform: string;
    status: string;
    result?: string | null;
    profile_health?: string | null;
    block_reason?: string | null;
    response_time_ms?: number | null;
    retry_count: number;
    created_at: string;
    finished_at?: string | null;
  }[],
): Record<string, string | number>[] {
  // Bảng phẳng cho export Excel (không cookie/credential — INV-12).
  return items.map((j) => ({
    trace_id: j.trace_id,
    platform: j.platform,
    link: j.target_url,
    job_status: j.status,
    result: j.result ?? '',
    profile_health: j.profile_health ?? '',
    block_reason: j.block_reason ?? '',
    response_time_ms: j.response_time_ms ?? '',
    retry_count: j.retry_count,
    created_at: j.created_at,
    finished_at: j.finished_at ?? '',
  }));
}
