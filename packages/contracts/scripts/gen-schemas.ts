// Sinh JSON Schema (từ zod) ra packages/contracts/schema/*.json. Chạy: `pnpm --filter @fastcheck/contracts gen:schema`.
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSchemas } from '../src/schemas.js';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const outDir = join(scriptDir, '..', 'schema');
mkdirSync(outDir, { recursive: true });

const schemas = buildSchemas();
for (const [file, schema] of Object.entries(schemas)) {
  writeFileSync(join(outDir, file), `${JSON.stringify(schema, null, 2)}\n`, 'utf8');
  console.log('wrote', file);
}
