import { Kysely, PostgresDialect } from 'kysely';
import pg from 'pg';
import type { Database } from './types.js';

const { Pool } = pg;

export type DB = Kysely<Database>;

/** Tạo kết nối Kysely tới Postgres (nguồn sự thật — INV-5). */
export function createDb(databaseUrl: string, maxConnections = 10): DB {
  return new Kysely<Database>({
    dialect: new PostgresDialect({
      pool: new Pool({ connectionString: databaseUrl, max: maxConnections }),
    }),
  });
}
