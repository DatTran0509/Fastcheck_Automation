import { useState, type ReactNode } from 'react';
// CHỈ import TYPE từ @fastcheck/contracts (bị xoá lúc build) — import VALUE sẽ kéo @fastcheck/shared (node:crypto)
// vào bundle browser và vỡ build. Default/enum để cục bộ, MIRROR packages/contracts/src/profile-config.ts.
import type { ProfileConfig } from '@fastcheck/contracts';
import { ORCH_BASE, sendJson } from '../lib/api.js';

// Form Tạo/Sửa profile mô phỏng panel Update GemLogin (4 tab). GIÁ TRỊ option dùng ĐÚNG chuỗi enum schema
// GemLogin (replace/real/disabled, real/noise, custom/random, Windows/macOS/…) để API GemLogin nhận. Khi lưu,
// gửi `config` xuống orchestrator → worker map (create: cờ bool + web_rtc/resolution/webgl_vendor/renderer;
// update: chuỗi web_rtc/webgl/canvas). WebRTC gửi CHUỖI (noise=Replace, disable=Disable) cho cả 2.
// Nhóm GUI-only KHÔNG có trong create/update request → hiển thị + gắn nhãn, không bắn.

type OsType = ProfileConfig['os_type'];

// Enum os.version theo schema GemLogin, phụ thuộc os_type.
const OS_VERSIONS: Record<OsType, string[]> = {
  Windows: ['win7', 'win8', 'win10', 'win11'],
  macOS: ['macos10', 'macos11', 'macos12', 'macos13'],
  Android: ['android9', 'android10', 'android11', 'android12', 'android13', 'android14'],
  IOS: ['ios14', 'ios15'],
  Linux: ['all_linux'],
};
const HW_ENUM = [2, 4, 8, 10, 12, 16, 20, 24];
const REAL_NOISE = [
  { v: 'noise', label: 'Noise' },
  { v: 'real', label: 'Real' },
] as const;

// Danh sách resolution cho Screen Resolution = Custom (khớp GUI GemLogin + phổ biến).
const RESOLUTIONS = [
  '1920x1080',
  '1600x900',
  '1536x864',
  '1440x900',
  '1366x768',
  '1280x1024',
  '1280x960',
  '1280x720',
  '1152x864',
  '1152x648',
  '1024x768',
  '1408x1056',
];

// WebGL metadata = Custom → Unmasked Vendor/Renderer mặc định PHÙ HỢP theo os_type (điền sẵn, user sửa được).
const WEBGL_DEFAULTS: Record<OsType, { vendor: string; renderer: string; vendors: string[] }> = {
  macOS: {
    vendor: 'Apple Inc. (macOS)',
    renderer: 'ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)',
    vendors: ['Apple Inc. (macOS)', 'Google Inc. (Apple)'],
  },
  Windows: {
    vendor: 'Google Inc. (NVIDIA)',
    renderer: 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)',
    vendors: ['Google Inc. (NVIDIA)', 'Google Inc. (Intel)', 'Google Inc. (AMD)'],
  },
  Linux: {
    vendor: 'Google Inc. (NVIDIA)',
    renderer: 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650/PCIe/SSE2, OpenGL 4.5.0)',
    vendors: ['Google Inc. (NVIDIA)', 'Google Inc. (Intel)', 'Mesa/X.org'],
  },
  Android: {
    vendor: 'Qualcomm',
    renderer: 'Adreno (TM) 640',
    vendors: ['Qualcomm', 'ARM'],
  },
  IOS: {
    vendor: 'Apple Inc.',
    renderer: 'Apple GPU',
    vendors: ['Apple Inc.'],
  },
};

// Điền sẵn giá trị "custom" phù hợp: webgl_vendor/renderer theo os_type khi metadata=custom; resolution khi custom.
function seedCustom(c: ProfileConfig): ProfileConfig {
  const d = WEBGL_DEFAULTS[c.os_type];
  return {
    ...c,
    webgl_vendor: c.webgl_metadata === 'custom' ? (c.webgl_vendor ?? d.vendor) : c.webgl_vendor,
    webgl_renderer: c.webgl_metadata === 'custom' ? (c.webgl_renderer ?? d.renderer) : c.webgl_renderer,
    resolution: c.resolution || '1920x1080',
  };
}

// Bộ mặc định — MIRROR profileConfigSchema defaults (server re-validate + điền .default() nếu thiếu).
const DEFAULT_CONFIG: ProfileConfig = {
  os_type: 'macOS',
  os_version: 'macos13',
  browser_version: '141',
  startup_url: '',
  user_agent_mode: 'auto',
  user_agent: null,
  country: 'Vietnam',
  language: 'vi,en',
  time_zone: 'Asia/Ho_Chi_Minh',
  proxy_type: 'none',
  proxy: null,
  web_rtc: 'disabled',
  screen_resolution: 'random',
  resolution: '1920x1080',
  canvas: 'noise',
  webgl_image: 'noise',
  webgl_metadata: 'custom',
  webgl_vendor: null,
  webgl_renderer: null,
  audio_context: 'noise',
  media_device: 'noise',
  client_rects: 'noise',
  fonts: 'default',
  speech_voices: 'noise',
  ssl: 'real',
  plugins: 'noise',
  hardware_concurrency: 10,
  device_memory: 10,
  device_name_mode: 'custom',
  device_name: null,
  mac_address_mode: 'custom',
  mac_address: null,
  do_not_track: 'open',
  flash: 'accept',
  port_scan_protection: 'accept',
  hardware_acceleration: 'accept',
};

// Field GUI-only (không có trong create/update request GemLogin) — hiển thị + nhãn, không bắn.
const GUI_ONLY = new Set<string>([
  'fonts',
  'speech_voices',
  'ssl',
  'plugins',
  'hardware_concurrency',
  'device_memory',
  'device_name_mode',
  'device_name',
  'mac_address_mode',
  'mac_address',
  'do_not_track',
  'flash',
  'port_scan_protection',
  'hardware_acceleration',
]);

interface Props {
  mode: 'create' | 'edit';
  stationId: string;
  gemloginProfileId?: string; // bắt buộc khi edit
  initialName?: string;
  initialConfig?: ProfileConfig;
  onClose: () => void;
  onSaved: () => void;
}

function Seg<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: readonly { v: T; label: string }[];
  onChange: (v: T) => void;
}): JSX.Element {
  return (
    <div className="seg">
      {options.map((o) => (
        <button key={o.v} type="button" className={value === o.v ? 'on' : ''} onClick={() => onChange(o.v)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Row({ label, field, children }: { label: string; field?: string; children: ReactNode }): JSX.Element {
  const guiOnly = field != null && GUI_ONLY.has(field);
  return (
    <div className="cfg-row">
      <label>
        {label}
        {guiOnly && (
          <span className="cfg-tag" title="Không có trong create/update API GemLogin — chỉ chỉnh trong app GemLogin">
            GUI-ONLY
          </span>
        )}
      </label>
      <div className="inline">{children}</div>
    </div>
  );
}

export function ProfileConfigForm({
  mode,
  stationId,
  gemloginProfileId,
  initialName,
  initialConfig,
  onClose,
  onSaved,
}: Props): JSX.Element {
  const [tab, setTab] = useState<'overview' | 'network' | 'cookies' | 'advanced'>('overview');
  const [name, setName] = useState(initialName ?? '');
  const [cfg, setCfg] = useState<ProfileConfig>(seedCustom(initialConfig ?? { ...DEFAULT_CONFIG }));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function set<K extends keyof ProfileConfig>(key: K, value: ProfileConfig[K]): void {
    setCfg((c) => ({ ...c, [key]: value }));
  }

  const save = async (): Promise<void> => {
    if (mode === 'edit' && !gemloginProfileId) return;
    setBusy(true);
    setErr(null);
    try {
      // KHÔNG gửi platform lúc tạo — pool tự phân loại + gán khi "Nạp tài khoản".
      const body = { account_label: name || undefined, config: cfg };
      const url =
        mode === 'create'
          ? `${ORCH_BASE}/stations/${stationId}/profiles`
          : `${ORCH_BASE}/stations/${stationId}/profiles/${gemloginProfileId}`;
      const r = await sendJson(mode === 'create' ? 'POST' : 'PATCH', url, body);
      const cmd = (r.data ?? {}) as { ok?: boolean; detail?: string | null };
      if (!r.ok || cmd.ok === false) {
        const detail = cmd.detail ?? `HTTP ${r.status}`;
        throw new Error(
          /free version/i.test(detail)
            ? 'GemLogin bản Free chặn thao tác này qua API — nâng lên bản trả phí để tạo/sửa qua dashboard.'
            : detail,
        );
      }
      onSaved();
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{mode === 'create' ? 'Tạo profile GemLogin' : `Sửa profile ${gemloginProfileId ?? ''}`}</h2>
          <button className="modal-close" onClick={onClose} aria-label="Đóng">
            ×
          </button>
        </div>

        <div className="modal-body">
          <div className="field" style={{ marginBottom: 8 }}>
            <label>Name (khuyến khích đặt để dễ truy tìm sau này)</label>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="vd tt-01, fb-main…" />
            {mode === 'create' && (
              <div className="card-hint" style={{ marginTop: 6 }}>
                Nền tảng KHÔNG gán lúc tạo — hệ thống tự phân loại &amp; gán khi bạn “Nạp tài khoản vào pool”.
              </div>
            )}
          </div>

          <div className="tabs">
            {(['overview', 'network', 'cookies', 'advanced'] as const).map((t) => (
              <button key={t} className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
                {t === 'overview' ? 'Overview' : t === 'network' ? 'Network' : t === 'cookies' ? 'Cookies' : 'Advanced settings'}
              </button>
            ))}
          </div>

          {tab === 'overview' && (
            <div>
              <Row label="Operating System">
                <select
                  value={cfg.os_type}
                  onChange={(e) => {
                    const t = e.target.value as OsType;
                    // Đổi OS → version đầu tiên của OS đó; nếu WebGL metadata=custom, cập nhật vendor/renderer cho
                    // KHỚP OS mới (vân tay phải nhất quán với OS — không để renderer Apple trên profile Windows).
                    const d = WEBGL_DEFAULTS[t];
                    setCfg((c) => ({
                      ...c,
                      os_type: t,
                      os_version: OS_VERSIONS[t][0],
                      webgl_vendor: c.webgl_metadata === 'custom' ? d.vendor : c.webgl_vendor,
                      webgl_renderer: c.webgl_metadata === 'custom' ? d.renderer : c.webgl_renderer,
                    }));
                  }}
                  style={{ width: 140 }}
                >
                  <option value="Windows">Windows</option>
                  <option value="macOS">macOS</option>
                  <option value="Linux">Linux</option>
                  <option value="Android">Android</option>
                  <option value="IOS">iOS</option>
                </select>
                <select value={cfg.os_version} onChange={(e) => set('os_version', e.target.value)} style={{ width: 150 }}>
                  {OS_VERSIONS[cfg.os_type].map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              </Row>
              <Row label="Browser / Version">
                <span className="badge plat">Chrome</span>
                <input value={cfg.browser_version} onChange={(e) => set('browser_version', e.target.value)} placeholder="141" style={{ width: 120 }} />
              </Row>
              <Row label="URL Startup">
                <input value={cfg.startup_url} onChange={(e) => set('startup_url', e.target.value)} placeholder="https://example.com (để trống = không mở)" />
              </Row>
              <Row label="User-Agent">
                <Seg
                  value={cfg.user_agent_mode}
                  options={[
                    { v: 'auto', label: 'Auto' },
                    { v: 'custom', label: 'Custom' },
                  ]}
                  onChange={(v) => set('user_agent_mode', v)}
                />
                {cfg.user_agent_mode === 'custom' && (
                  <input value={cfg.user_agent ?? ''} onChange={(e) => set('user_agent', e.target.value)} placeholder="Mozilla/5.0 …" />
                )}
              </Row>
              <Row label="Country">
                <input value={cfg.country} onChange={(e) => set('country', e.target.value)} style={{ width: 200 }} />
              </Row>
              <Row label="Language">
                <input value={cfg.language} onChange={(e) => set('language', e.target.value)} placeholder="vi,en" style={{ width: 160 }} />
              </Row>
              <Row label="Timezone">
                <input value={cfg.time_zone} onChange={(e) => set('time_zone', e.target.value)} placeholder="Asia/Ho_Chi_Minh" style={{ width: 200 }} />
              </Row>
            </div>
          )}

          {tab === 'network' && (
            <div>
              <Row label="Proxy Type">
                <select
                  value={cfg.proxy_type}
                  onChange={(e) => set('proxy_type', e.target.value as ProfileConfig['proxy_type'])}
                  style={{ width: 200 }}
                >
                  <option value="none">Do not use proxies</option>
                  <option value="http">HTTP</option>
                  <option value="https">HTTPS</option>
                  <option value="socks5">SOCKS5</option>
                </select>
              </Row>
              {cfg.proxy_type !== 'none' && (
                <Row label="Proxy">
                  <input value={cfg.proxy ?? ''} onChange={(e) => set('proxy', e.target.value)} placeholder="host:port hoặc user:pass@host:port" />
                </Row>
              )}
            </div>
          )}

          {tab === 'cookies' && (
            <div className="gui-only-note">
              Cookie đăng nhập KHÔNG cấu hình ở đây (INV-12 — cookie mã hoá at-rest). Dùng khối{' '}
              <b>“Nạp tài khoản &amp; đăng nhập”</b> phía trên trang Profiles để nạp/đăng nhập cookie an toàn.
            </div>
          )}

          {tab === 'advanced' && (
            <div>
              <Row label="WebRTC">
                <Seg
                  value={cfg.web_rtc}
                  options={[
                    { v: 'replace', label: 'Replace' },
                    { v: 'disabled', label: 'Disable' },
                  ]}
                  onChange={(v) => set('web_rtc', v)}
                />
              </Row>
              <Row label="Screen Resolution">
                <Seg
                  value={cfg.screen_resolution}
                  options={[
                    { v: 'custom', label: 'Custom' },
                    { v: 'random', label: 'Random' },
                  ]}
                  onChange={(v) => set('screen_resolution', v)}
                />
                {cfg.screen_resolution === 'custom' && (
                  <select value={cfg.resolution} onChange={(e) => set('resolution', e.target.value)} style={{ width: 160 }}>
                    {RESOLUTIONS.map((r) => (
                      <option key={r} value={r}>
                        {r.replace('x', '×')}
                      </option>
                    ))}
                  </select>
                )}
              </Row>
              <Row label="Canvas">
                <Seg value={cfg.canvas} options={REAL_NOISE} onChange={(v) => set('canvas', v)} />
              </Row>
              <Row label="WebGL Image">
                <Seg value={cfg.webgl_image} options={REAL_NOISE} onChange={(v) => set('webgl_image', v)} />
              </Row>
              <Row label="WebGL metadata">
                <Seg
                  value={cfg.webgl_metadata}
                  options={[
                    { v: 'custom', label: 'Custom' },
                    { v: 'real', label: 'Real' },
                    { v: 'random', label: 'Random' },
                  ]}
                  onChange={(v) =>
                    // Chọn Custom → điền sẵn Vendor/Renderer theo OS nếu chưa có (user sửa được). Real/Random: giữ.
                    setCfg((c) => {
                      const d = WEBGL_DEFAULTS[c.os_type];
                      return {
                        ...c,
                        webgl_metadata: v,
                        webgl_vendor: v === 'custom' ? (c.webgl_vendor ?? d.vendor) : c.webgl_vendor,
                        webgl_renderer: v === 'custom' ? (c.webgl_renderer ?? d.renderer) : c.webgl_renderer,
                      };
                    })
                  }
                />
              </Row>
              {cfg.webgl_metadata === 'custom' && (
                <>
                  <Row label="Unmasked Vendor">
                    <select
                      value={cfg.webgl_vendor ?? WEBGL_DEFAULTS[cfg.os_type].vendor}
                      onChange={(e) => set('webgl_vendor', e.target.value)}
                      style={{ width: 240 }}
                    >
                      {WEBGL_DEFAULTS[cfg.os_type].vendors.map((v) => (
                        <option key={v} value={v}>
                          {v}
                        </option>
                      ))}
                    </select>
                  </Row>
                  <Row label="Unmasked Renderer">
                    <input
                      value={cfg.webgl_renderer ?? WEBGL_DEFAULTS[cfg.os_type].renderer}
                      onChange={(e) => set('webgl_renderer', e.target.value)}
                      placeholder="ANGLE (…)"
                    />
                  </Row>
                </>
              )}
              <Row label="Audio Context">
                <Seg value={cfg.audio_context} options={REAL_NOISE} onChange={(v) => set('audio_context', v)} />
              </Row>
              <Row label="Media Device">
                <Seg value={cfg.media_device} options={REAL_NOISE} onChange={(v) => set('media_device', v)} />
              </Row>
              <Row label="Client Rects">
                <Seg value={cfg.client_rects} options={REAL_NOISE} onChange={(v) => set('client_rects', v)} />
              </Row>

              <div className="gui-only-note">
                Nhóm dưới đây <b>không nằm trong create/update API GemLogin</b> (nhãn GUI-ONLY) — hiển thị + lưu
                theo mặc định, muốn đổi phải chỉnh trực tiếp trong app GemLogin.
              </div>
              <Row label="Fonts" field="fonts">
                <Seg
                  value={cfg.fonts}
                  options={[
                    { v: 'default', label: 'Default' },
                    { v: 'custom', label: 'Custom' },
                  ]}
                  onChange={(v) => set('fonts', v)}
                />
              </Row>
              <Row label="Speech Voices" field="speech_voices">
                <Seg value={cfg.speech_voices} options={REAL_NOISE} onChange={(v) => set('speech_voices', v)} />
              </Row>
              <Row label="SSL" field="ssl">
                <Seg value={cfg.ssl} options={REAL_NOISE} onChange={(v) => set('ssl', v)} />
              </Row>
              <Row label="Plugins" field="plugins">
                <Seg value={cfg.plugins} options={REAL_NOISE} onChange={(v) => set('plugins', v)} />
              </Row>
              <Row label="Hardware Concurrency" field="hardware_concurrency">
                <select value={cfg.hardware_concurrency} onChange={(e) => set('hardware_concurrency', Number(e.target.value))} style={{ width: 100 }}>
                  {HW_ENUM.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </Row>
              <Row label="Device Memory" field="device_memory">
                <select value={cfg.device_memory} onChange={(e) => set('device_memory', Number(e.target.value))} style={{ width: 100 }}>
                  {HW_ENUM.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </Row>
              <Row label="Device Name" field="device_name">
                <Seg
                  value={cfg.device_name_mode}
                  options={[
                    { v: 'custom', label: 'Custom' },
                    { v: 'real', label: 'Real' },
                  ]}
                  onChange={(v) => set('device_name_mode', v)}
                />
                {cfg.device_name_mode === 'custom' && (
                  <input value={cfg.device_name ?? ''} onChange={(e) => set('device_name', e.target.value)} placeholder="vd 91G6B-74979" />
                )}
              </Row>
              <Row label="MAC Address" field="mac_address">
                <Seg
                  value={cfg.mac_address_mode}
                  options={[
                    { v: 'custom', label: 'Custom' },
                    { v: 'real', label: 'Real' },
                  ]}
                  onChange={(v) => set('mac_address_mode', v)}
                />
                {cfg.mac_address_mode === 'custom' && (
                  <input value={cfg.mac_address ?? ''} onChange={(e) => set('mac_address', e.target.value)} placeholder="vd 54:52:00:33:84:26" />
                )}
              </Row>
              <Row label="Do Not Track" field="do_not_track">
                <Seg
                  value={cfg.do_not_track}
                  options={[
                    { v: 'default', label: 'Default' },
                    { v: 'open', label: 'Open' },
                    { v: 'close', label: 'Close' },
                  ]}
                  onChange={(v) => set('do_not_track', v)}
                />
              </Row>
              <Row label="Flash" field="flash">
                <Seg
                  value={cfg.flash}
                  options={[
                    { v: 'accept', label: 'Accept' },
                    { v: 'decline', label: 'Decline' },
                  ]}
                  onChange={(v) => set('flash', v)}
                />
              </Row>
              <Row label="Port Scan Protection" field="port_scan_protection">
                <Seg
                  value={cfg.port_scan_protection}
                  options={[
                    { v: 'accept', label: 'Accept' },
                    { v: 'decline', label: 'Decline' },
                  ]}
                  onChange={(v) => set('port_scan_protection', v)}
                />
              </Row>
              <Row label="Hardware Acceleration" field="hardware_acceleration">
                <Seg
                  value={cfg.hardware_acceleration}
                  options={[
                    { v: 'default', label: 'Default' },
                    { v: 'accept', label: 'Accept' },
                    { v: 'decline', label: 'Decline' },
                  ]}
                  onChange={(v) => set('hardware_acceleration', v)}
                />
              </Row>
            </div>
          )}

          {err && (
            <div className="alert critical" style={{ marginTop: 14 }}>
              {err}
            </div>
          )}
        </div>

        <div className="modal-foot">
          <button onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="primary" onClick={() => void save()} disabled={busy || (mode === 'edit' && !gemloginProfileId)}>
            {busy ? <span className="spinner" /> : mode === 'create' ? '+ Create Profile' : 'Update'}
          </button>
        </div>
      </div>
    </div>
  );
}
