import { zodToJsonSchema } from 'zod-to-json-schema';
import { checkRequestSchema } from './api.dto.js';
import { checkJobMessageSchema } from './queue.payload.js';
import { wsClientMessageSchema, wsServerMessageSchema } from './ws.protocol.js';

// zod là NGUỒN SỰ THẬT của contract. Ta xuất JSON Schema từ zod để phía Python (pydantic)
// đối chiếu — chống drift giữa hai ngôn ngữ (ADR-0006, review P1).
const SOURCES = {
  'ws-client-message.schema.json': wsClientMessageSchema,
  'ws-server-message.schema.json': wsServerMessageSchema,
  'check-job-message.schema.json': checkJobMessageSchema,
  'check-request.schema.json': checkRequestSchema,
} as const;

export type SchemaFileName = keyof typeof SOURCES;

/** Sinh map {tên file → JSON Schema (draft-07, inline, không $ref)} từ các zod schema. */
export function buildSchemas(): Record<SchemaFileName, unknown> {
  const out = {} as Record<SchemaFileName, unknown>;
  for (const [file, schema] of Object.entries(SOURCES) as [SchemaFileName, (typeof SOURCES)[SchemaFileName]][]) {
    out[file] = zodToJsonSchema(schema, { target: 'jsonSchema7', $refStrategy: 'none' });
  }
  return out;
}
