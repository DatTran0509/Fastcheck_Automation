import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSchemas } from './schemas.js';

// Bảo đảm file JSON Schema đã commit KHỚP với zod hiện tại. Nếu fail → chạy:
//   pnpm --filter @fastcheck/contracts gen:schema
const schemaDir = join(dirname(fileURLToPath(import.meta.url)), '..', 'schema');

describe('JSON Schema xuất từ zod khớp file đã commit (chống drift TS↔Python)', () => {
  const generated = buildSchemas();
  for (const [file, schema] of Object.entries(generated)) {
    it(`${file} không bị stale`, () => {
      const committed = JSON.parse(readFileSync(join(schemaDir, file), 'utf8')) as unknown;
      expect(committed).toEqual(schema);
    });
  }
});
