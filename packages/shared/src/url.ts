import { createHash } from 'node:crypto';
import { Platform } from './enums.js';

// Tracking params to strip before hashing (INV-13). `utm_*` matched by prefix; the rest by exact name.
const TRACKING_PARAMS = new Set([
  'fbclid',
  'gclid',
  'gclsrc',
  'dclid',
  'yclid',
  'msclkid',
  'igshid',
  'igsh',
  'mc_eid',
  'mc_cid',
  '_ga',
  'ref',
  'ref_src',
  'ref_url',
  'source',
  's', // twitter/x share tag e.g. ?s=20
  't', // x share timestamp tag
  'feature', // youtube ?feature=share
  'si', // youtube/spotify share id
]);

function isTrackingParam(key: string): boolean {
  const k = key.toLowerCase();
  return k.startsWith('utm_') || TRACKING_PARAMS.has(k);
}

/**
 * Chuẩn hoá URL trước khi hash (INV-13): lowercase scheme + host, bỏ fragment,
 * gỡ tracking param, sort query còn lại (để hai URL cùng nội dung ra cùng hash),
 * bỏ trailing slash, gỡ port mặc định. Idempotent.
 * @throws nếu `raw` không phải URL hợp lệ.
 */
export function normalizeUrl(raw: string): string {
  const u = new URL(raw.trim());

  u.protocol = u.protocol.toLowerCase();
  u.hostname = u.hostname.toLowerCase();
  u.hash = '';

  const kept: Array<[string, string]> = [];
  for (const [key, value] of u.searchParams.entries()) {
    if (!isTrackingParam(key)) kept.push([key, value]);
  }
  // Sort for a stable hash regardless of original param order.
  kept.sort((a, b) => (a[0] === b[0] ? compare(a[1], b[1]) : compare(a[0], b[0])));
  const sp = new URLSearchParams();
  for (const [key, value] of kept) sp.append(key, value);
  const search = sp.toString();
  u.search = search ? `?${search}` : '';

  // Collapse duplicate slashes, drop trailing slash (except root).
  let path = u.pathname.replace(/\/{2,}/g, '/');
  if (path.length > 1 && path.endsWith('/')) path = path.slice(0, -1);
  u.pathname = path;

  return u.toString();
}

function compare(a: string, b: string): number {
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

/** `url_hash = sha256(normalizeUrl(url))` — key của cache Redis + dedupe (INV-13). */
export function urlHash(raw: string): string {
  return createHash('sha256').update(normalizeUrl(raw)).digest('hex');
}

/** Nhận diện platform từ URL theo host. Trả `null` nếu không thuộc 4 platform hỗ trợ. */
export function detectPlatform(raw: string): Platform | null {
  let host: string;
  try {
    host = new URL(raw.trim()).hostname.toLowerCase();
  } catch {
    return null;
  }
  const h = host.startsWith('www.') ? host.slice(4) : host;

  if (h === 'tiktok.com' || h.endsWith('.tiktok.com')) return Platform.TIKTOK;
  if (h === 'facebook.com' || h.endsWith('.facebook.com') || h === 'fb.com' || h === 'fb.watch') {
    return Platform.FACEBOOK;
  }
  if (
    h === 'twitter.com' ||
    h.endsWith('.twitter.com') ||
    h === 'x.com' ||
    h.endsWith('.x.com') ||
    h === 't.co'
  ) {
    return Platform.TWITTER;
  }
  if (h === 'youtube.com' || h.endsWith('.youtube.com') || h === 'youtu.be') {
    return Platform.YOUTUBE;
  }
  return null;
}
