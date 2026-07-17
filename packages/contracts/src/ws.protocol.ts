import { z } from 'zod';
import { Platform, ProfileHealth, UrlStatus } from '@fastcheck/shared';

// Giao thức WS Orchestrator ↔ Client App (station). WSS + token (INV-12).
// Lệnh Server→Client mang command_id để idempotent (INV-14). Message gắn job mang trace_id.

// ── Station info (khớp bảng stations) ────────────────────────────────────────
export const stationInfoSchema = z.object({
  station_id: z.string().uuid(),
  name: z.string().min(1),
  // .nullish(): worker Python (pydantic) phát JSON `null` cho field vắng — chấp nhận cả null lẫn undefined (ADR-0006).
  mac_address: z.string().nullish(),
  ip_address: z.string().nullish(),
  agent_version: z.string().min(1),
  max_concurrency: z.number().int().positive(),
});
export type StationInfo = z.infer<typeof stationInfoSchema>;

// ── Client → Server ───────────────────────────────────────────────────────────
export const registerMessageSchema = z.object({
  type: z.literal('register'),
  // Token KHÔNG nằm trong message này — xác thực ở HTTP upgrade qua header Authorization (INV-12).
  station: stationInfoSchema,
});

export const heartbeatMessageSchema = z.object({
  type: z.literal('heartbeat'),
  station_id: z.string().uuid(),
  current_load: z.number().int().nonnegative(),
  ts: z.string(),
});

export const commandAckMessageSchema = z.object({
  type: z.literal('command_ack'),
  command_id: z.string().uuid(),
  station_id: z.string().uuid(),
});

export const jobResultMessageSchema = z.object({
  type: z.literal('job_result'),
  command_id: z.string().uuid(),
  trace_id: z.string().uuid(),
  job_id: z.string().uuid(),
  // url_status TÁCH BIỆT profile_health (INV-3)
  url_status: z.nativeEnum(UrlStatus),
  profile_health: z.nativeEnum(ProfileHealth),
  block_reason: z.string().nullable().optional(),
  response_time_ms: z.number().int().nonnegative().optional(),
});

export const wsClientMessageSchema = z.discriminatedUnion('type', [
  registerMessageSchema,
  heartbeatMessageSchema,
  commandAckMessageSchema,
  jobResultMessageSchema,
]);
export type WsClientMessage = z.infer<typeof wsClientMessageSchema>;

// ── Server → Client (lệnh, idempotent + command_id — INV-14) ────────────────────
export const runCommandSchema = z.object({
  name: z.literal('script.run'),
  trace_id: z.string().uuid(),
  job_id: z.string().uuid(),
  target_url: z.string(),
  platform: z.nativeEnum(Platform),
  profile_id: z.string().uuid(),
  // cookie đã giải mã (orchestrator giải mã qua packages/crypto) — gửi qua kênh WSS (ADR-0006).
  cookie: z.string(),
});

export const browserOpenCommandSchema = z.object({
  name: z.literal('browser.open'),
  profile_id: z.string().uuid(),
});

export const browserCloseCommandSchema = z.object({
  name: z.literal('browser.close'),
  profile_id: z.string().uuid(),
});

export const commandPayloadSchema = z.discriminatedUnion('name', [
  runCommandSchema,
  browserOpenCommandSchema,
  browserCloseCommandSchema,
]);
export type CommandPayload = z.infer<typeof commandPayloadSchema>;

export const serverCommandSchema = z.object({
  type: z.literal('command'),
  command_id: z.string().uuid(), // idempotent (INV-14)
  command: commandPayloadSchema,
});

export const registeredMessageSchema = z.object({
  type: z.literal('registered'),
  station_id: z.string().uuid(),
});

export const wsServerMessageSchema = z.discriminatedUnion('type', [
  serverCommandSchema,
  registeredMessageSchema,
]);
export type WsServerMessage = z.infer<typeof wsServerMessageSchema>;
