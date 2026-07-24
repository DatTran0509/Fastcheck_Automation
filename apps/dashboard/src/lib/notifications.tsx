// Hệ thống TOAST thông báo realtime cho operator (mốc chính: gửi lệnh → phản hồi/kết quả). Dựng trên palette
// alert sẵn có. Bốn mức: info/success/warn/error. KHÔNG hiển thị cookie/credential (INV-12) — chỉ nhãn + detail
// kết luận (LOGGED_IN, blocked:captcha_or_challenge, otp_required:email_code_unavailable…).
import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from 'react';

export type NoticeKind = 'info' | 'success' | 'warn' | 'error';

interface Notice {
  id: number;
  kind: NoticeKind;
  message: string;
}

interface NotifyApi {
  notify: (kind: NoticeKind, message: string) => void;
}

const ICON: Record<NoticeKind, string> = {
  info: 'ℹ️',
  success: '✅',
  warn: '⚠️',
  error: '❌',
};
const TTL_MS: Record<NoticeKind, number> = { info: 4000, success: 5000, warn: 7000, error: 9000 };
const MAX_VISIBLE = 5;

const NotifyContext = createContext<NotifyApi | null>(null);

export function NotificationProvider({ children }: { children: ReactNode }): JSX.Element {
  const [notices, setNotices] = useState<Notice[]>([]);
  const idRef = useRef(0);

  const dismiss = useCallback((id: number) => {
    setNotices((xs) => xs.filter((n) => n.id !== id));
  }, []);

  const notify = useCallback(
    (kind: NoticeKind, message: string) => {
      const id = (idRef.current += 1);
      // Giữ tối đa MAX_VISIBLE toast gần nhất (tránh tràn màn hình khi thao tác dồn dập).
      setNotices((xs) => [...xs, { id, kind, message }].slice(-MAX_VISIBLE));
      window.setTimeout(() => dismiss(id), TTL_MS[kind]);
    },
    [dismiss],
  );

  return (
    <NotifyContext.Provider value={{ notify }}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {notices.map((n) => (
          <button key={n.id} type="button" className={`toast ${n.kind}`} onClick={() => dismiss(n.id)}>
            <span className="toast-icon">{ICON[n.kind]}</span>
            <span className="toast-msg">{n.message}</span>
          </button>
        ))}
      </div>
    </NotifyContext.Provider>
  );
}

export function useNotify(): NotifyApi {
  const ctx = useContext(NotifyContext);
  if (!ctx) throw new Error('useNotify phải nằm trong <NotificationProvider>');
  return ctx;
}
