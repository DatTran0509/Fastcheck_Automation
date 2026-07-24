import { useEffect, useState } from 'react';
import { ORCH_BASE, PLATFORMS } from '../lib/api.js';
import { postJson } from '../lib/http.js';
import { parseAccountLine } from '../lib/account-parser.js';
import { useNotify } from '../lib/notifications.js';
import { useSnapshot } from '../lib/snapshot.js';

// Đoán nền tảng từ DOMAIN cookie (chống chọn nhầm nền tảng → chạy sai kịch bản login). Chỉ CẢNH BÁO.
function detectPlatformFromCookie(raw: string): string | null {
  if (!raw.trim()) return null;
  let text = raw;
  try {
    const arr: unknown = JSON.parse(raw);
    if (Array.isArray(arr)) {
      text = arr
        .map((c) => (c && typeof c === 'object' ? String((c as { domain?: unknown }).domain ?? '') : ''))
        .join(' ');
    }
  } catch {
    /* chuỗi k=v thô: dùng nguyên văn */
  }
  const t = text.toLowerCase();
  if (t.includes('x.com') || t.includes('twitter.com')) return 'TWITTER';
  if (t.includes('tiktok.com')) return 'TIKTOK';
  if (t.includes('facebook.com')) return 'FACEBOOK';
  if (t.includes('youtube.com') || t.includes('google.com')) return 'YOUTUBE';
  return null;
}

// login-by-info: X (user/pass gốc trên x.com), TikTok & YouTube (qua tài khoản GOOGLE). Facebook chỉ cookie.
const INFO_PLATFORMS = new Set(['TIKTOK', 'TWITTER', 'YOUTUBE']);
const GOOGLE_INFO_PLATFORMS = new Set(['TIKTOK', 'YOUTUBE']); // INFO = đăng nhập bằng tài khoản Google

// Nhãn thao tác + nhãn field cho BẢNG kết quả (thay vì JSON thô — dễ đọc cho operator).
const ACTION_LABEL: Record<string, string> = {
  'create-profile': 'Tạo profile',
  'open-browser': 'Mở browser',
  'close-browser': 'Tắt browser',
  'run-login': 'Chạy login',
  'register-account': 'Nạp tài khoản vào pool',
};
const FIELD_LABEL: Record<string, string> = {
  detail: 'Chi tiết',
  profile_id: 'Profile',
  gemlogin_profile_id: 'GemLogin id',
  command_id: 'Command',
  station_id: 'Station',
  platform: 'Nền tảng',
  status: 'Trạng thái',
  has_cookie: 'Có cookie',
};

interface ActResult {
  ok: boolean;
  status: number;
  name: string;
  data: unknown;
}

function renderValue(key: string, v: unknown): JSX.Element | string {
  if (v == null || v === '') return '—';
  if (typeof v === 'boolean')
    return <span className={`badge ${v ? 'ok' : 'blocked'}`}>{v ? 'có' : 'không'}</span>;
  // `detail` là kết luận có nghĩa của lệnh (LOGGED_IN, cookie_dead, gemlogin_error…) → làm nổi bật.
  if (key === 'detail') return <span className="badge inconclusive">{String(v)}</span>;
  if (key.endsWith('_id')) return <span className="mono">{String(v)}</span>;
  return String(v);
}

/** Hiển thị phản hồi lệnh dưới dạng BẢNG (không JSON thô). KHÔNG chứa cookie/credential (INV-12). */
function ResultPanel({ ok, status, name, data }: ActResult): JSX.Element {
  const label = ACTION_LABEL[name] ?? name;
  const isObj = data !== null && typeof data === 'object';
  const entries = isObj
    ? Object.entries(data as Record<string, unknown>).filter(([k]) => k !== 'ok')
    : [];
  return (
    <div className={`result-panel ${ok ? 'ok' : 'err'}`}>
      <div className="result-panel-head">
        <span className="result-icon">{ok ? '✅' : '❌'}</span>
        <b>{label}</b>
        <span className={`badge ${ok ? 'done' : 'blocked'}`}>{ok ? 'Thành công' : 'Thất bại'}</span>
        <span className="muted mono">HTTP {status || '—'}</span>
      </div>
      {isObj ? (
        <dl className="result-fields">
          {entries.map(([k, v]) => (
            <div className="result-row" key={k}>
              <dt>{FIELD_LABEL[k] ?? k}</dt>
              <dd>{renderValue(k, v)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <div className="result-message">{String(data)}</div>
      )}
    </div>
  );
}

/** Bảng điều khiển: tạo/mở/tắt profile, chạy login (cookie/info), nạp tài khoản vào pool (Station Mgmt — mục 2). */
export function AccountControls({ onRegistered }: { onRegistered?: () => void }): JSX.Element {
  const { snap } = useSnapshot();
  const { notify } = useNotify();
  const stations = snap?.stations ?? [];

  const [sid, setSid] = useState('');
  const [platform, setPlatform] = useState<string>('TIKTOK');
  const [gemId, setGemId] = useState('');
  // loginKind = lựa chọn cấp cao (cookie / đăng nhập bằng tài khoản). Với X, "tài khoản" tách 2 nhánh qua xVariant.
  const [loginKind, setLoginKind] = useState<'COOKIE' | 'INFO'>('COOKIE');
  // X: GMAIL (qua Google — method INFO) hoặc USERPASS (native user/pass/2FA + mã email Hotmail — method USERPASS).
  const [xVariant, setXVariant] = useState<'GMAIL' | 'USERPASS'>('GMAIL');
  const [cookie, setCookie] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [otp, setOtp] = useState('');
  // @username của X cho bước "Confirm your account" (khác `username` = email/định danh đăng nhập). Chỉ X.
  const [confirmUsername, setConfirmUsername] = useState('');
  // USERPASS (X native): hộp thư khôi phục lấy mã 6 số khi X đòi (LoginAcid). Token ưu tiên, fallback email+pass.
  const [hotmailEmail, setHotmailEmail] = useState('');
  const [hotmailPassword, setHotmailPassword] = useState('');
  const [hotmailToken, setHotmailToken] = useState('');
  // Ô DÁN 1 dòng tài khoản (nằm trên cookie) → tự tách & điền các trường cho kịch bản đang chọn.
  const [pasted, setPasted] = useState('');
  const [label, setLabel] = useState('');
  const [proxy, setProxy] = useState('');
  const [verify, setVerify] = useState(true);
  const [showPw, setShowPw] = useState(false);

  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<ActResult | null>(null);

  useEffect(() => {
    if (!sid && stations.length > 0) setSid(stations[0].station_id);
  }, [stations, sid]);

  const act = async (
    name: string,
    fn: () => Promise<{ ok: boolean; status: number; data: unknown }>,
    after?: () => void,
  ) => {
    const label = ACTION_LABEL[name] ?? name;
    setBusy(name);
    setResult(null);
    notify('info', `${label}: đang gửi lệnh…`); // mốc realtime: bắt đầu
    try {
      const r = await fn();
      // Lệnh THẤT BẠI vẫn trả HTTP 200 với body {ok:false} (vd login sai, GemLogin lỗi) → hiệu lực ok phải
      // xét CẢ ok cấp-lệnh trong body, không chỉ HTTP status (nếu không sẽ hiện "Thành công" oan).
      const bodyOk = r.data && typeof r.data === 'object' ? (r.data as { ok?: unknown }).ok : undefined;
      const detail =
        r.data && typeof r.data === 'object'
          ? String((r.data as { detail?: unknown }).detail ?? '')
          : '';
      const ok = r.ok && bodyOk !== false;
      setResult({ ok, status: r.status, name, data: r.data });
      // Mốc realtime: kết quả (detail = kết luận có nghĩa: LOGGED_IN / blocked:captcha / otp_required:… — INV-1).
      if (ok) {
        notify('success', `${label}: ${detail || 'thành công'}`);
        after?.();
      } else {
        notify('error', `${label}: ${detail || `thất bại (HTTP ${r.status || '—'})`}`);
      }
    } catch (e) {
      setResult({ ok: false, status: 0, name, data: (e as Error).message });
      notify('error', `${label}: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  // Tách 1 dòng tài khoản đã dán → điền các trường cho kịch bản đang chọn (chỉ ghi đè trường TÁCH ĐƯỢC).
  const handleParse = () => {
    const line = pasted.trim();
    if (!line) {
      notify('warn', 'Dán 1 dòng tài khoản vào ô trước đã.');
      return;
    }
    const p = parseAccountLine(line);
    const filled: string[] = [];
    if (p.username) {
      setUsername(p.username);
      filled.push('tài khoản');
    }
    if (p.password) {
      setPassword(p.password);
      filled.push('mật khẩu');
    }
    if (p.otpSecret) {
      setOtp(p.otpSecret);
      filled.push('2FA secret');
    }
    // @handle X cho bước "Confirm your account" = chính username (định danh) — chỉ dùng cho X.
    if (p.username && platform === 'TWITTER') setConfirmUsername(p.username);
    if (p.hotmailEmail) {
      setHotmailEmail(p.hotmailEmail);
      filled.push('email Hotmail');
    }
    if (p.hotmailPassword) {
      setHotmailPassword(p.hotmailPassword);
      filled.push('mật khẩu Hotmail');
    }
    if (p.hotmailToken) {
      setHotmailToken(p.hotmailToken);
      filled.push('token Microsoft');
    }
    if (p.cookie) {
      setCookie(p.cookie);
      filled.push('cookie');
    }
    notify(
      filled.length ? 'success' : 'warn',
      filled.length ? `Đã tách & điền: ${filled.join(', ')}.` : 'Không nhận ra trường nào từ dòng đã dán.',
    );
  };

  const infoMode = loginKind === 'INFO';
  const isX = platform === 'TWITTER';
  // Nhánh USERPASS (X native user/pass/2FA + mã email Hotmail) — chỉ khi chọn X + "Tài khoản + mật khẩu".
  const xUserpass = infoMode && isX && xVariant === 'USERPASS';
  // Đăng nhập QUA GOOGLE: TikTok/YouTube (INFO) + X khi chọn nhánh Gmail.
  const usesGoogle = infoMode && (GOOGLE_INFO_PLATFORMS.has(platform) || (isX && xVariant === 'GMAIL'));
  // method GỬI xuống server (contract): COOKIE | INFO (Google) | USERPASS (X native).
  const method: 'COOKIE' | 'INFO' | 'USERPASS' = loginKind === 'COOKIE' ? 'COOKIE' : xUserpass ? 'USERPASS' : 'INFO';
  const disabled = !!busy;
  const cookiePlatform = detectPlatformFromCookie(cookie);
  const cookieMismatch = cookiePlatform != null && cookiePlatform !== platform;
  const infoSupported = INFO_PLATFORMS.has(platform);

  return (
    <section className="card">
      <div className="card-head">
        <h2>Nạp tài khoản & đăng nhập</h2>
        <span className="card-hint">Tạo/mở/tắt profile · chạy login (cookie/info) · nạp vào pool</span>
      </div>

      <div className="form-grid">
        <div className="field">
          <label>Station</label>
          <select value={sid} onChange={(e) => setSid(e.target.value)}>
            {stations.length === 0 && <option value="">— chưa có station —</option>}
            {stations.map((s) => (
              <option key={s.station_id} value={s.station_id}>
                {s.name ?? s.station_id.slice(0, 8)} · {s.status}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Nền tảng</label>
          <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
            {PLATFORMS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>GemLogin profile id</label>
          <input value={gemId} onChange={(e) => setGemId(e.target.value)} placeholder="vd 1, 4, 12…" />
        </div>
        <div className="field">
          <label>Phương thức login</label>
          <select value={loginKind} onChange={(e) => setLoginKind(e.target.value as 'COOKIE' | 'INFO')}>
            <option value="COOKIE">COOKIE (cả 4 nền tảng)</option>
            <option value="INFO">Đăng nhập bằng tài khoản (X · TikTok · YouTube)</option>
          </select>
        </div>
      </div>

      {/* X có 2 cách đăng nhập bằng tài khoản: qua Gmail (Google) HOẶC tài khoản + mật khẩu X (native + 2FA). */}
      {infoMode && isX && (
        <div className="field">
          <label>Đăng nhập X bằng</label>
          <div className="seg-toggle" role="tablist" aria-label="Cách đăng nhập X">
            <button
              type="button"
              className={xVariant === 'GMAIL' ? 'active' : ''}
              aria-selected={xVariant === 'GMAIL'}
              onClick={() => setXVariant('GMAIL')}
            >
              Gmail (Google)
            </button>
            <button
              type="button"
              className={xVariant === 'USERPASS' ? 'active' : ''}
              aria-selected={xVariant === 'USERPASS'}
              onClick={() => setXVariant('USERPASS')}
            >
              Tài khoản + mật khẩu (2FA)
            </button>
          </div>
        </div>
      )}

      {/* DÁN 1 dòng tài khoản (trên cookie) → tự PHÂN LOẠI token (email/cookie/secret 2FA/token MS/mật khẩu…)
          và điền các trường cho kịch bản đang chọn. Chịu được thiếu trường / không đúng thứ tự. */}
      <div className="field">
        <label>Dán 1 dòng tài khoản (tự tách &amp; điền) — mỗi dòng = 1 tài khoản, ngăn cách bởi “|”</label>
        <textarea
          value={pasted}
          onChange={(e) => setPasted(e.target.value)}
          placeholder="vd: username | password | 2FA_SECRET | email@hotmail.com | hotmail_pass | M.C…$$ | uuid | cookie…"
          style={{ minHeight: 64 }}
        />
        <div className="row" style={{ marginTop: 8 }}>
          <button type="button" onClick={handleParse} disabled={disabled}>
            Tách &amp; điền
          </button>
          <span className="card-hint">
            Hệ thống tự nhận diện đâu là tài khoản/mật khẩu/secret 2FA/email+mật khẩu Hotmail/token/cookie, rồi
            điền vào các ô bên dưới để bạn kiểm lại trước khi chạy.
          </span>
        </div>
      </div>

      <div className="field">
        <label>Cookie (JSON hoặc chuỗi k=v) — để nạp/đăng nhập bằng cookie</label>
        <textarea
          value={cookie}
          onChange={(e) => setCookie(e.target.value)}
          placeholder='[{"name":"sessionid","value":"..."}, ...]'
        />
        {cookieMismatch && (
          <div className="alert warn" style={{ marginTop: 10 }}>
            ⚠️ Cookie có vẻ của <b>{cookiePlatform}</b> nhưng bạn đang chọn nền tảng <b>{platform}</b> — sẽ chạy
            SAI kịch bản login.
            <button className="sm" style={{ marginLeft: 12 }} onClick={() => cookiePlatform && setPlatform(cookiePlatform)}>
              Đổi sang {cookiePlatform}
            </button>
          </div>
        )}
      </div>

      {/* Đăng nhập bằng TÀI KHOẢN — LUÔN hiện để dễ thấy; chỉ bật khi Phương thức login = "Đăng nhập bằng tài khoản". */}
      <div className="field">
        <label>
          Đăng nhập bằng tài khoản — X: Gmail hoặc user/pass · TikTok &amp; YouTube: <b>tài khoản Google</b>
        </label>
        {!infoMode && (
          <div className="card-hint" style={{ marginBottom: 8 }}>
            Muốn đăng nhập bằng tài khoản? Đổi <b>Phương thức login = Đăng nhập bằng tài khoản</b> ở trên để bật các ô này.
          </div>
        )}
        {usesGoogle && (
          <div className="alert warn" style={{ marginBottom: 8 }}>
            ℹ️ <b>{platform}</b> đăng nhập qua <b>Google</b>: nhập <b>email + mật khẩu TÀI KHOẢN GOOGLE</b> (không phải
            mật khẩu {platform}). OTP secret chỉ cần nếu <b>tài khoản Google</b> bật 2FA. Lưu ý Google chặn browser
            tự động khá mạnh — cookie vẫn là cách ổn định nhất.
          </div>
        )}
        {xUserpass && (
          <div className="alert warn" style={{ marginBottom: 8 }}>
            ℹ️ <b>X</b> đăng nhập bằng <b>username + mật khẩu + 2FA</b> trên x.com. Nếu X đòi <b>mã 6 số qua email</b>{' '}
            ở bất kỳ bước nào, worker tự mở tab <b>Outlook</b> lấy mã (ưu tiên <b>token Microsoft</b>, fallback{' '}
            <b>email + mật khẩu Hotmail</b>). Vượt 2FA mà không bị đòi mã email thì bỏ qua Hotmail.
          </div>
        )}
        {infoMode && !infoSupported && (
          <div className="alert warn" style={{ marginBottom: 8 }}>
            ⚠️ <b>{platform}</b> chỉ hỗ trợ đăng nhập bằng <b>cookie</b>. Facebook không hỗ trợ user/mật khẩu.
          </div>
        )}
        <div className="form-grid">
          <div>
            <label>
              {usesGoogle
                ? 'Username (email tài khoản Google)'
                : xUserpass
                  ? 'Username X (định danh đăng nhập)'
                  : 'Tài khoản (email/SĐT/username đăng nhập)'}
            </label>
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" disabled={!infoMode} />
          </div>
          <div>
            <label>{usesGoogle ? 'Mật khẩu Google' : 'Password'}</label>
            <div className="pw-wrap">
              <input
                type={showPw ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="off"
                disabled={!infoMode}
              />
              <button
                type="button"
                className="pw-toggle"
                onClick={() => setShowPw((v) => !v)}
                disabled={!infoMode}
                aria-label={showPw ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
                title={showPw ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
              >
                {showPw ? '🙈' : '👁'}
              </button>
            </div>
          </div>
          <div>
            <label>OTP secret (TOTP base32{xUserpass ? ' — mã 2FA X' : ''})</label>
            <input
              value={otp}
              onChange={(e) => setOtp(e.target.value)}
              placeholder={xUserpass ? 'bắt buộc nếu tài khoản bật 2FA' : 'tuỳ chọn'}
              autoComplete="off"
              disabled={!infoMode}
            />
          </div>
          {/* @username của X cho bước "Confirm your account" — CHỈ X native (khác ô username = định danh đăng nhập). */}
          {xUserpass && (
            <div>
              <label>@username của X (bước "Confirm your account")</label>
              <input
                value={confirmUsername}
                onChange={(e) => setConfirmUsername(e.target.value)}
                placeholder="tuỳ chọn — vd my_x_handle (không cần @)"
                autoComplete="off"
                disabled={!infoMode}
              />
            </div>
          )}
        </div>
      </div>

      {/* Hộp thư khôi phục (USERPASS/X) — lấy mã 6 số khi X đòi (LoginAcid). Token ưu tiên; fallback email+mật khẩu. */}
      {xUserpass && (
        <div className="field">
          <label>
            Hộp thư Hotmail để lấy mã xác minh email của X — <b>token ưu tiên</b>, fallback email + mật khẩu
          </label>
          <div className="form-grid">
            <div>
              <label>Email Hotmail</label>
              <input
                value={hotmailEmail}
                onChange={(e) => setHotmailEmail(e.target.value)}
                placeholder="vd ...@hotmail.com"
                autoComplete="off"
              />
            </div>
            <div>
              <label>Mật khẩu Hotmail</label>
              <input
                type={showPw ? 'text' : 'password'}
                value={hotmailPassword}
                onChange={(e) => setHotmailPassword(e.target.value)}
                placeholder="fallback nếu token chết"
                autoComplete="off"
              />
            </div>
            <div>
              <label>Microsoft auth token (M.C…$$)</label>
              <input
                value={hotmailToken}
                onChange={(e) => setHotmailToken(e.target.value)}
                placeholder="ưu tiên — inject để vào thẳng hộp thư"
                autoComplete="off"
              />
            </div>
          </div>
        </div>
      )}

      <div className="form-grid">
        <div className="field">
          <label>Nhãn tài khoản (account_label)</label>
          <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="vd tt-01" />
        </div>
        <div className="field">
          <label>Proxy (tuỳ chọn)</label>
          <input value={proxy} onChange={(e) => setProxy(e.target.value)} placeholder="http://user:pass@host:port" />
        </div>
        <div className="field">
          <label>Verify trước khi nạp</label>
          <label className="row" style={{ marginTop: 4, fontWeight: 500, color: 'var(--text)' }}>
            <input type="checkbox" checked={verify} onChange={(e) => setVerify(e.target.checked)} style={{ width: 'auto' }} />
            Kiểm tra đã đăng nhập đúng nền tảng (chống nạp sai → cooldown loạn)
          </label>
        </div>
      </div>

      <div className="row" style={{ marginTop: 6 }}>
        <button
          disabled={disabled || !sid}
          onClick={() =>
            void act('create-profile', () =>
              postJson(`${ORCH_BASE}/stations/${sid}/profiles`, {
                platform,
                account_label: label || `fastcheck-${platform}`,
                proxy: proxy || undefined,
              }),
            )
          }
        >
          Tạo profile
        </button>
        <button
          disabled={disabled || !sid || !gemId}
          onClick={() =>
            void act('open-browser', () =>
              postJson(`${ORCH_BASE}/stations/${sid}/browser/open`, { gemlogin_profile_id: gemId }),
            )
          }
        >
          Mở browser
        </button>
        <button
          disabled={disabled || !sid || !gemId}
          onClick={() =>
            void act('close-browser', () =>
              postJson(`${ORCH_BASE}/stations/${sid}/browser/close`, { gemlogin_profile_id: gemId }),
            )
          }
        >
          Tắt browser
        </button>
        <button
          disabled={disabled || !sid || !gemId}
          onClick={() =>
            void act('run-login', () =>
              postJson(`${ORCH_BASE}/stations/${sid}/login`, {
                gemlogin_profile_id: gemId,
                platform,
                method,
                cookie: cookie || undefined,
                username: username || undefined,
                password: password || undefined,
                otp_secret: otp || undefined,
                confirm_username: confirmUsername || undefined,
                // USERPASS (X): hộp thư khôi phục lấy mã email khi X đòi (LoginAcid). KHÔNG log (INV-12).
                hotmail_email: hotmailEmail || undefined,
                hotmail_password: hotmailPassword || undefined,
                hotmail_token: hotmailToken || undefined,
              }),
            )
          }
        >
          Chạy login
        </button>
        <button
          className="primary"
          disabled={disabled || !gemId}
          onClick={() =>
            void act(
              'register-account',
              () =>
                postJson(`${ORCH_BASE}/accounts`, {
                  platform,
                  gemlogin_profile_id: gemId,
                  station_id: sid || undefined,
                  account_label: label || undefined,
                  cookie: cookie || undefined,
                  proxy: proxy || undefined,
                  verify,
                }),
              onRegistered,
            )
          }
        >
          Nạp tài khoản vào pool
        </button>
      </div>

      {result && <ResultPanel {...result} />}
    </section>
  );
}
