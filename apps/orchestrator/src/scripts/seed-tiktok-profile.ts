/**
 * Seed một profile TikTok AVAILABLE với cookie mã hoá (AES-GCM qua packages/crypto) để chạy E2E
 * Phase 1. Idempotent: xoá profile seed cũ (theo account_label) rồi tạo lại.
 *
 * Chạy:  node apps/orchestrator/dist/scripts/seed-tiktok-profile.js
 */
import { loadOrchestratorEnv, cookieKeyringFromEnv } from '@fastcheck/config';
import { createDb } from '@fastcheck/db';
import { createCookieCipher } from '@fastcheck/crypto';
import { Platform, ProfileStatus } from '@fastcheck/shared';

const SEED_LABEL = 'seed-tiktok-e2e';

async function main(): Promise<void> {
  const env = loadOrchestratorEnv();
  const db = createDb(env.DATABASE_URL);
  const ring = cookieKeyringFromEnv(env);
  const cipher = createCookieCipher(ring.activeKeyBase64, ring.activeKeyId, ring.olderKeys);

  // Cookie giả (fake mode không nạp thật) nhưng vẫn mã hoá thật để exercise đường giải mã ở orchestrator.
  const enc = cipher.encryptJson([
    { name: 'sessionid', value: 'fake-session', domain: '.tiktok.com', path: '/' },
  ]);

  await db.deleteFrom('profiles').where('account_label', '=', SEED_LABEL).execute();
  const row = await db
    .insertInto('profiles')
    .values({
      platform: Platform.TIKTOK,
      account_label: SEED_LABEL,
      cookie_ciphertext: enc.ciphertext,
      cookie_key_id: enc.keyId,
      status: ProfileStatus.AVAILABLE,
      health_score: 100,
    })
    .returning(['id'])
    .executeTakeFirstOrThrow();

  // eslint-disable-next-line no-console
  console.log(`seeded TikTok profile ${row.id} (label=${SEED_LABEL})`);
  await db.destroy();
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error('seed failed:', err);
  process.exit(1);
});
