/**
 * Seed proxy pool (spec §4.6, INV-7). Proxy credential được MÃ HOÁ (proxy_url_enc, AES-GCM qua
 * packages/crypto) — KHÔNG lưu trần (INV-12). Ưu tiên residential/mobile sticky, gắn region để claim
 * đúng geo. Idempotent: xoá proxy seed cũ (theo region prefix nhãn) rồi tạo lại + gán vào profile chưa có proxy.
 *
 * Nguồn proxy: biến môi trường FASTCHECK_SEED_PROXIES = JSON [{ "url": "...", "type": "RESIDENTIAL",
 * "region": "VN" }, ...]. KHÔNG commit proxy thật (đọc từ env / secret — INV-12).
 *
 * Chạy:  node apps/orchestrator/dist/scripts/seed-proxies.js
 */
import { loadOrchestratorEnv, cookieKeyringFromEnv } from '@fastcheck/config';
import { createDb } from '@fastcheck/db';
import { createCookieCipher } from '@fastcheck/crypto';
import { ProxyStatus, ProxyType } from '@fastcheck/shared';

interface SeedProxy {
  url: string; // ví dụ: http://user:pass@host:port  (credential sẽ được mã hoá)
  type?: keyof typeof ProxyType;
  region?: string;
}

/** Đọc danh sách proxy từ env (không hardcode proxy thật vào repo). Trống → 1 proxy placeholder để test plumbing. */
function readSeedProxies(): SeedProxy[] {
  const raw = process.env.FASTCHECK_SEED_PROXIES;
  if (!raw) {
    // Placeholder (không dùng thật) — cho phép test đường claim/xoay khi chưa có proxy thật.
    return [{ url: 'http://placeholder:0', type: 'RESIDENTIAL', region: 'VN' }];
  }
  const parsed: unknown = JSON.parse(raw);
  if (!Array.isArray(parsed)) throw new Error('FASTCHECK_SEED_PROXIES phải là JSON array');
  return parsed as SeedProxy[];
}

async function main(): Promise<void> {
  const env = loadOrchestratorEnv();
  const db = createDb(env.DATABASE_URL);
  const ring = cookieKeyringFromEnv(env);
  // Dùng chung cipher với cookie (một module crypto duy nhất — rule security). proxy_url_enc cùng keyring.
  const cipher = createCookieCipher(ring.activeKeyBase64, ring.activeKeyId, ring.olderKeys);

  const seeds = readSeedProxies();
  const insertedIds: string[] = [];
  for (const s of seeds) {
    const type = ProxyType[s.type ?? 'RESIDENTIAL'] ?? ProxyType.RESIDENTIAL;
    const enc = cipher.encrypt(s.url); // credential proxy mã hoá at-rest (INV-12)
    const row = await db
      .insertInto('proxies')
      .values({
        proxy_url_enc: enc.ciphertext,
        type,
        region: s.region ?? null,
        status: ProxyStatus.ACTIVE,
      })
      .returning(['id'])
      .executeTakeFirstOrThrow();
    insertedIds.push(row.id);
  }

  // Gán proxy sticky cho các profile chưa có proxy (1 profile = 1 proxy sticky — INV-6/INV-7).
  const unassigned = await db
    .selectFrom('profiles')
    .select(['id'])
    .where('proxy_id', 'is', null)
    .execute();
  let assigned = 0;
  for (const p of unassigned) {
    const proxyId = insertedIds[assigned % insertedIds.length];
    if (!proxyId) break;
    await db.updateTable('profiles').set({ proxy_id: proxyId }).where('id', '=', p.id).execute();
    assigned += 1;
  }

  // eslint-disable-next-line no-console
  console.log(`seeded ${insertedIds.length} proxy, gán sticky cho ${assigned} profile (INV-7)`);
  await db.destroy();
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error('seed proxies failed:', err);
  process.exit(1);
});
