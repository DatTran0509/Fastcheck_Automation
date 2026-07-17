import { describe, it, expect } from 'vitest';
import { normalizeUrl, urlHash, detectPlatform } from './url.js';
import { Platform } from './enums.js';

describe('normalizeUrl', () => {
  it('gỡ utm_*/fbclid và sort query → hai URL khác tracking param cho cùng url_hash (INV-13)', () => {
    const a = 'https://www.tiktok.com/@u/video/123?utm_source=x&fbclid=abc&lang=en';
    const b = 'https://www.tiktok.com/@u/video/123?lang=en&utm_medium=y';
    expect(urlHash(a)).toBe(urlHash(b));
  });

  it('lowercase host + gỡ fragment', () => {
    expect(normalizeUrl('https://WWW.YouTube.com/watch?v=1#frag')).toBe(
      'https://www.youtube.com/watch?v=1',
    );
  });

  it('bỏ trailing slash (trừ root) và collapse slash trùng', () => {
    expect(normalizeUrl('https://x.com/abc/')).toBe('https://x.com/abc');
    expect(normalizeUrl('https://x.com//a//b/')).toBe('https://x.com/a/b');
  });

  it('idempotent: normalize(normalize(x)) == normalize(x)', () => {
    const once = normalizeUrl('https://www.tiktok.com/@u/video/123?b=2&a=1&utm_source=x#f');
    expect(normalizeUrl(once)).toBe(once);
  });

  it('url_hash là sha256 hex (64 ký tự)', () => {
    expect(urlHash('https://x.com/abc')).toMatch(/^[0-9a-f]{64}$/);
  });
});

describe('detectPlatform', () => {
  it('nhận diện đúng 4 platform + null cho ngoài phạm vi', () => {
    expect(detectPlatform('https://www.tiktok.com/@u/video/1')).toBe(Platform.TIKTOK);
    expect(detectPlatform('https://x.com/u/status/1')).toBe(Platform.TWITTER);
    expect(detectPlatform('https://twitter.com/u/status/1')).toBe(Platform.TWITTER);
    expect(detectPlatform('https://youtu.be/abc')).toBe(Platform.YOUTUBE);
    expect(detectPlatform('https://www.facebook.com/u')).toBe(Platform.FACEBOOK);
    expect(detectPlatform('https://example.com/whatever')).toBeNull();
    expect(detectPlatform('not-a-url')).toBeNull();
  });
});
