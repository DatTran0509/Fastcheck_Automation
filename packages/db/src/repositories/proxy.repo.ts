import type { DB } from '../client.js';
import type { Proxy } from '../types.js';

export async function listProxies(db: DB): Promise<Proxy[]> {
  return db.selectFrom('proxies').selectAll().execute();
}
