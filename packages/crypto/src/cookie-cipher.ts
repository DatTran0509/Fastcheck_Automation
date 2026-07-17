import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';

// INV-12: MỘT nơi duy nhất mã hoá cookie. AES-256-GCM (mã hoá + xác thực toàn vẹn).
const ALGO = 'aes-256-gcm';
const IV_LEN = 12; // GCM nonce chuẩn 96-bit
const TAG_LEN = 16; // GCM auth tag 128-bit
const KEY_LEN = 32; // AES-256

export class CookieCipherError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'CookieCipherError';
  }
}

export interface EncryptedCookie {
  /** Blob `IV(12) || authTag(16) || ciphertext` — lưu vào cột BYTEA `cookie_ciphertext`. */
  ciphertext: Buffer;
  /** Id khoá đã dùng để mã hoá — lưu vào cột `cookie_key_id`. Cho phép xoay khoá. */
  keyId: string;
}

/**
 * Mã hoá/giải mã cookie bằng AES-256-GCM với một keyring (nhiều khoá theo id).
 * Xoay khoá: mã hoá luôn dùng `activeKeyId`; giải mã dùng đúng khoá theo `keyId`
 * đính kèm ciphertext → ciphertext cũ vẫn giải mã được bằng khoá cũ sau khi đổi khoá (INV-12).
 */
export class CookieCipher {
  private readonly keyring: Map<string, Buffer>;
  private readonly activeKeyId: string;

  constructor(keyring: Map<string, Buffer>, activeKeyId: string) {
    for (const [id, key] of keyring) {
      if (key.length !== KEY_LEN) {
        throw new CookieCipherError(`Khoá "${id}" phải dài ${KEY_LEN} byte (AES-256), nhận ${key.length}.`);
      }
    }
    if (!keyring.has(activeKeyId)) {
      throw new CookieCipherError(`activeKeyId "${activeKeyId}" không có trong keyring.`);
    }
    this.keyring = keyring;
    this.activeKeyId = activeKeyId;
  }

  encrypt(plaintext: string | Buffer): EncryptedCookie {
    const key = this.keyring.get(this.activeKeyId)!;
    const iv = randomBytes(IV_LEN);
    const cipher = createCipheriv(ALGO, key, iv);
    const data = typeof plaintext === 'string' ? Buffer.from(plaintext, 'utf8') : plaintext;
    const enc = Buffer.concat([cipher.update(data), cipher.final()]);
    const tag = cipher.getAuthTag();
    return { ciphertext: Buffer.concat([iv, tag, enc]), keyId: this.activeKeyId };
  }

  decrypt(enc: EncryptedCookie): string {
    const key = this.keyring.get(enc.keyId);
    if (!key) {
      throw new CookieCipherError(
        `Không có khoá cho keyId "${enc.keyId}" trong keyring (cần giữ khoá cũ khi xoay khoá).`,
      );
    }
    const blob = enc.ciphertext;
    if (blob.length < IV_LEN + TAG_LEN) {
      throw new CookieCipherError('Ciphertext quá ngắn hoặc hỏng.');
    }
    const iv = blob.subarray(0, IV_LEN);
    const tag = blob.subarray(IV_LEN, IV_LEN + TAG_LEN);
    const data = blob.subarray(IV_LEN + TAG_LEN);
    const decipher = createDecipheriv(ALGO, key, iv);
    decipher.setAuthTag(tag);
    try {
      return Buffer.concat([decipher.update(data), decipher.final()]).toString('utf8');
    } catch {
      // GCM auth tag không khớp: khoá sai hoặc dữ liệu bị sửa. Không nuốt lỗi — báo rõ.
      throw new CookieCipherError('Giải mã thất bại: sai khoá hoặc ciphertext đã bị sửa đổi.');
    }
  }

  encryptJson(value: unknown): EncryptedCookie {
    return this.encrypt(JSON.stringify(value));
  }

  decryptJson<T = unknown>(enc: EncryptedCookie): T {
    return JSON.parse(this.decrypt(enc)) as T;
  }
}

/** Dựng keyring từ map `keyId -> khoá base64 (32 byte)`. */
export function createKeyring(entries: Record<string, string>): Map<string, Buffer> {
  const keyring = new Map<string, Buffer>();
  for (const [id, base64] of Object.entries(entries)) {
    const key = Buffer.from(base64, 'base64');
    if (key.length !== KEY_LEN) {
      throw new CookieCipherError(`Khoá "${id}" phải là base64 của đúng ${KEY_LEN} byte.`);
    }
    keyring.set(id, key);
  }
  return keyring;
}

/**
 * Tiện dụng cho orchestrator: dựng cipher từ khoá active (env `COOKIE_ENC_KEY`/`COOKIE_KEY_ID`),
 * kèm các khoá cũ (nếu có) để giải mã ciphertext đã tạo trước khi xoay khoá.
 */
export function createCookieCipher(
  activeKeyBase64: string,
  activeKeyId: string,
  olderKeys: Record<string, string> = {},
): CookieCipher {
  const keyring = createKeyring({ ...olderKeys, [activeKeyId]: activeKeyBase64 });
  return new CookieCipher(keyring, activeKeyId);
}
