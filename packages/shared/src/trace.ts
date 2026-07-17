import { randomUUID } from 'node:crypto';

/** trace_id đi kèm một job xuyên API → queue → worker → kết quả (INV: mọi thao tác mang trace_id). */
export type TraceId = string;
/** command_id để lệnh điều khiển station idempotent (INV-14). */
export type CommandId = string;

export function newTraceId(): TraceId {
  return randomUUID();
}

export function newCommandId(): CommandId {
  return randomUUID();
}
