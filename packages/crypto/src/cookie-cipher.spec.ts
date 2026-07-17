import { describe, it, expect } from 'vitest';
import {
  CookieCipher,
  CookieCipherError,
  createCookieCipher,
  createKeyring,
} from './cookie-cipher.js';

const k1 = Buffer.alloc(32, 1).toString('base64');
const k2 = Buffer.alloc(32, 2).toString('base64');

describe('CookieCipher round-trip', () => {
  it('encrypt → decrypt trả đúng plaintext', () => {
    const c = createCookieCipher(k1, 'k1');
    const enc = c.encrypt('secret-cookie-value');
    expect(enc.keyId).toBe('k1');
    expect(Buffer.isBuffer(enc.ciphertext)).toBe(true);
    expect(c.decrypt(enc)).toBe('secret-cookie-value');
  });

  it('encryptJson → decryptJson giữ nguyên cấu trúc cookie', () => {
    const c = createCookieCipher(k1, 'k1');
    const cookies = [{ name: 'sessionid', value: 'abc', domain: '.tiktok.com', path: '/' }];
    expect(c.decryptJson(c.encryptJson(cookies))).toEqual(cookies);
  });

  it('IV ngẫu nhiên: hai lần encrypt cùng plaintext ra ciphertext khác nhau', () => {
    const c = createCookieCipher(k1, 'k1');
    expect(c.encrypt('x').ciphertext.equals(c.encrypt('x').ciphertext)).toBe(false);
  });

  it('sửa ciphertext → giải mã ném CookieCipherError (auth tag GCM bắt được)', () => {
    const c = createCookieCipher(k1, 'k1');
    const enc = c.encrypt('x');
    enc.ciphertext[enc.ciphertext.length - 1] ^= 0xff;
    expect(() => c.decrypt(enc)).toThrow(CookieCipherError);
  });

  it('xoay khoá: ciphertext tạo bằng k1 vẫn giải mã được khi active là k2 (keyring giữ cả hai)', () => {
    const encOld = createCookieCipher(k1, 'k1').encrypt('old-value');
    const rotated = new CookieCipher(createKeyring({ k1, k2 }), 'k2');
    expect(rotated.decrypt(encOld)).toBe('old-value'); // giải mã cookie cũ bằng khoá cũ theo keyId
    expect(rotated.encrypt('new-value').keyId).toBe('k2'); // cookie mới dùng khoá active
  });

  it('thiếu khoá cho keyId → ném lỗi rõ ràng (không nuốt)', () => {
    const encFromK1 = createCookieCipher(k1, 'k1').encrypt('x');
    const onlyK2 = createCookieCipher(k2, 'k2');
    expect(() => onlyK2.decrypt(encFromK1)).toThrow(CookieCipherError);
  });

  it('khoá sai độ dài → constructor ném', () => {
    expect(() => createKeyring({ bad: Buffer.alloc(16).toString('base64') })).toThrow(
      CookieCipherError,
    );
  });
});
