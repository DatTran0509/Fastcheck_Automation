// Tách 1 DÒNG tài khoản (dán vào ô trên cookie) thành các trường để auto-fill cho kịch bản login user chọn.
// Dùng cho MỌI nền tảng đăng nhập bằng tài khoản. Trường có thể KHÔNG theo thứ tự / KHÔNG đủ → PHÂN LOẠI theo
// ĐẶC TÍNH của từng token (email? cookie? secret 2FA base32? token Microsoft? mật khẩu?), không dựa vị trí cứng.
// KHÔNG log giá trị (INV-12) — chỉ phân loại tại client rồi điền vào form để operator review lại.

export type FieldKind =
  | 'username'
  | 'password'
  | 'otp_secret'
  | 'hotmail_email'
  | 'hotmail_password'
  | 'hotmail_token'
  | 'cookie'
  | 'uuid'
  | 'auth_token_short'
  | 'unknown';

export interface ParsedAccount {
  username?: string;
  password?: string;
  otpSecret?: string;
  hotmailEmail?: string;
  hotmailPassword?: string;
  hotmailToken?: string;
  cookie?: string;
  uuid?: string;
  /** Phân loại từng token (để hiển thị cho operator kiểm lại "đâu là tk/mk/secret…"). */
  breakdown: { value: string; kind: FieldKind }[];
}

// Email khôi phục Hotmail → hotmail_email; email khác (gmail/…) coi là username đăng nhập (Google/native).
const HOTMAIL_DOMAINS = ['hotmail.', 'outlook.', 'live.', 'msn.'];
const COOKIE_KEYS = ['auth_token=', 'ct0=', 'sessionid=', 'guest_id', 'datr=', 'c_user=', 'sid=', 'lang='];

const isEmail = (t: string): boolean => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(t);
const isUuid = (t: string): boolean =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(t);
const isHex40 = (t: string): boolean => /^[0-9a-f]{40}$/i.test(t);
// Microsoft/RPS token: bắt đầu 'M.' + kết thúc '$$', hoặc chứa 'MsaArtifacts' (token đăng nhập Outlook).
const isMsToken = (t: string): boolean =>
  (/^M\.[A-Za-z]/.test(t) && t.endsWith('$$')) || t.includes('MsaArtifacts');
// Secret TOTP base32: chỉ A-Z2-7, dài ≥16, và có CHỮ ngoài dải hex (G-Z) để không nhầm với chuỗi hex.
const isBase32Secret = (t: string): boolean => /^[A-Z2-7]{16,64}$/.test(t) && /[G-Z]/.test(t);
// Cookie: JSON array, hoặc nhiều cặp k=v ngăn bởi ';', hoặc chứa key cookie quen thuộc.
const isCookie = (t: string): boolean => {
  const s = t.trim();
  if (s.startsWith('[') && s.includes('{')) return true;
  const low = s.toLowerCase();
  if (COOKIE_KEYS.some((k) => low.includes(k))) return true;
  return s.includes(';') && (s.match(/=/g)?.length ?? 0) >= 2;
};
// Handle "sạch" (chữ/số/_/.): dùng để đoán username khi không có email đăng nhập. Mật khẩu thường có ký tự đặc
// biệt hoặc không phải handle sạch — nhưng KHÔNG chắc chắn (vd 'ZTVRUSfgl7586'), nên vẫn cần vị trí + review.
const isHandle = (t: string): boolean => /^[A-Za-z0-9_.]{3,30}$/.test(t);

const emailDomain = (t: string): string => t.slice(t.indexOf('@') + 1).toLowerCase();

function decodeCookie(raw: string): string {
  // Cookie copy từ web thường bị escape HTML entity (&quot; trong _twitter_sess/g_state) → trả lại ký tự thật.
  return raw
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/&#39;|&apos;/g, "'")
    .trim();
}

function classify(token: string): FieldKind {
  if (isEmail(token)) {
    const dom = emailDomain(token);
    if (HOTMAIL_DOMAINS.some((d) => dom.startsWith(d))) return 'hotmail_email';
    return 'username'; // email đăng nhập (gmail/khác) = username của kịch bản Google/native
  }
  if (isUuid(token)) return 'uuid';
  if (isMsToken(token)) return 'hotmail_token';
  if (isCookie(token)) return 'cookie';
  if (isHex40(token)) return 'auth_token_short';
  if (isBase32Secret(token)) return 'otp_secret';
  return 'unknown';
}

/**
 * Tách 1 dòng tài khoản thành các trường. Tách token theo `|`, TAB, xuống dòng (định dạng file mẫu dùng `|`;
 * dòng file còn có tiền tố `UID<TAB>` = trùng username → tự dedupe). Phân loại token bằng ĐẶC TÍNH, rồi suy ra
 * username/password/hotmail_password bằng NGỮ CẢNH (kề email, thứ tự) — mọi thứ để operator review lại.
 */
export function parseAccountLine(line: string): ParsedAccount {
  const rawTokens = line
    .split(/[|\t\n\r]+/)
    .map((t) => t.trim())
    .filter(Boolean);
  // Dedupe token TRÙNG LIỀN NHAU (tiền tố UID lặp lại username ở đầu dòng file mẫu).
  const tokens: string[] = [];
  for (const t of rawTokens) if (tokens[tokens.length - 1] !== t) tokens.push(t);

  const breakdown = tokens.map((value) => ({ value, kind: classify(value) }));
  const out: ParsedAccount = { breakdown };

  // 1. Trường có TÍN HIỆU MẠNH (không phụ thuộc thứ tự).
  const hotmailEmailIdx = breakdown.findIndex((b) => b.kind === 'hotmail_email');
  breakdown.forEach((b, i) => {
    switch (b.kind) {
      case 'hotmail_email':
        out.hotmailEmail ??= b.value;
        break;
      case 'hotmail_token':
        out.hotmailToken ??= b.value;
        break;
      case 'uuid':
        out.uuid ??= b.value;
        break;
      case 'cookie':
        out.cookie ??= decodeCookie(b.value);
        break;
      case 'otp_secret':
        out.otpSecret ??= b.value;
        break;
      case 'username':
        out.username ??= b.value; // email đăng nhập (gmail/khác)
        break;
      default:
        void i;
    }
  });

  // 2. hotmail_password = token 'unknown' NGAY SAU hotmail_email (kề nhau — bền dù dòng bị đảo thứ tự khối).
  if (hotmailEmailIdx >= 0) {
    const next = breakdown[hotmailEmailIdx + 1];
    if (next && next.kind === 'unknown') {
      out.hotmailPassword = next.value;
      next.kind = 'hotmail_password';
    }
  }

  // 3. username + password từ các token 'unknown' còn lại (theo thứ tự xuất hiện). Nếu đã có email đăng nhập
  //    (username set ở bước 1) thì unknown đầu → password; nếu chưa → unknown đầu (handle) = username, kế = password.
  const leftovers = breakdown.filter((b) => b.kind === 'unknown').map((b) => b.value);
  let li = 0;
  if (!out.username) {
    // Ưu tiên token trông giống handle làm username; nếu không có handle rõ thì lấy token đầu.
    const handleIdx = leftovers.findIndex((v) => isHandle(v));
    const usernameIdx = handleIdx >= 0 ? handleIdx : 0;
    if (leftovers.length > 0) {
      out.username = leftovers[usernameIdx];
      leftovers.splice(usernameIdx, 1);
    }
  }
  if (!out.password && li < leftovers.length) {
    out.password = leftovers[li];
    li += 1;
  }

  return out;
}
