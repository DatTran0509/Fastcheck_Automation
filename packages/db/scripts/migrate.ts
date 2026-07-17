// Runner migration: nạp .env (tìm ngược lên gốc repo) rồi chạy node-pg-migrate theo hướng up|down.
// Chạy: `pnpm db:migrate` (up) / `pnpm db:migrate:down` (down 1 bước).
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';
import runner from 'node-pg-migrate';

function findUpwards(startDir: string, filename: string): string | undefined {
  let dir = startDir;
  for (;;) {
    const candidate = join(dir, filename);
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir) return undefined;
    dir = parent;
  }
}

const envPath = findUpwards(process.cwd(), '.env');
if (envPath) dotenv.config({ path: envPath });

const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) {
  console.error('DATABASE_URL chưa được đặt (kiểm tra .env). Fail-fast.');
  process.exit(1);
}

const direction = process.argv[2] === 'down' ? 'down' : 'up';
const scriptDir = dirname(fileURLToPath(import.meta.url));
const migrationsDir = resolve(scriptDir, '../migrations');

await runner({
  databaseUrl,
  dir: migrationsDir,
  migrationsTable: 'pgmigrations',
  direction,
  count: direction === 'down' ? 1 : Infinity,
});

console.log(`Migration "${direction}" hoàn tất.`);
process.exit(0);
