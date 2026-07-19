import { sql } from 'kysely';
import type { DB } from '../client.js';
import type { Proxy } from '../types.js';

export async function listProxies(db: DB): Promise<Proxy[]> {
  return db.selectFrom('proxies').selectAll().execute();
}

/**
 * Tăng `fail_count` của proxy (proxy chết biểu hiện là INCONCLUSIVE/BLOCKED hàng loạt — anti-patterns §3).
 * Vượt ngưỡng → `COOLDOWN` (nghỉ để theo dõi + cảnh báo). Trả proxy sau cập nhật.
 */
export async function noteProxyFailure(
  db: DB,
  proxyId: string,
  banThreshold: number,
): Promise<Proxy | null> {
  const result = await sql<Proxy>`
    UPDATE proxies
    SET fail_count = fail_count + 1,
        status = CASE WHEN fail_count + 1 >= ${banThreshold} THEN 'COOLDOWN'::proxy_status ELSE status END
    WHERE id = ${proxyId}
    RETURNING *;
  `.execute(db);
  return result.rows[0] ?? null;
}

/**
 * Xoay proxy cho profile ở tầng "cấp IP mới cho phiên SAU" (INV-7: KHÔNG xoay giữa phiên đang chạy).
 * Gán một proxy `ACTIVE` khác proxy hiện tại. Trả id proxy mới, hoặc `null` nếu không có proxy thay thế.
 */
export async function rotateProfileProxy(db: DB, profileId: string): Promise<string | null> {
  const result = await sql<{ proxy_id: string }>`
    UPDATE profiles
    SET proxy_id = (
      SELECT p.id FROM proxies p
      WHERE p.status = 'ACTIVE'
        AND p.id IS DISTINCT FROM profiles.proxy_id
      ORDER BY p.fail_count ASC
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    WHERE id = ${profileId}
      AND EXISTS (SELECT 1 FROM proxies p WHERE p.status = 'ACTIVE' AND p.id IS DISTINCT FROM profiles.proxy_id)
    RETURNING proxy_id;
  `.execute(db);
  return result.rows[0]?.proxy_id ?? null;
}
