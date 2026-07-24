import { z } from 'zod';
import { zodToJsonSchema } from 'zod-to-json-schema';
import { checkRequestSchema } from './api.dto.js';
import { profileConfigSchema } from './profile-config.js';
import { checkJobMessageSchema } from './queue.payload.js';
import {
  browserCloseCommandSchema,
  browserOpenCommandSchema,
  cdpForwardCommandSchema,
  commandAckMessageSchema,
  cookieRefreshMessageSchema,
  heartbeatMessageSchema,
  jobProgressMessageSchema,
  jobResultMessageSchema,
  loginRunCommandSchema,
  profileCreateCommandSchema,
  profileDeleteCommandSchema,
  profileSyncMessageSchema,
  profileUpdateCommandSchema,
  registerMessageSchema,
  registeredMessageSchema,
  runCommandSchema,
  serverCommandSchema,
  stationInfoSchema,
  stationProfileSchema,
  wsClientMessageSchema,
  wsServerMessageSchema,
} from './ws.protocol.js';

// zod là NGUỒN SỰ THẬT của contract. Xuất JSON Schema từ zod để worker Python (pydantic) dùng chung —
// chống drift giữa hai ngôn ngữ (ADR-0006, review P1).

// (a) File union/DTO — dùng validate tổng quát + parity test.
const UNION_SOURCES = {
  'ws-client-message.schema.json': wsClientMessageSchema,
  'ws-server-message.schema.json': wsServerMessageSchema,
  'check-job-message.schema.json': checkJobMessageSchema,
  'check-request.schema.json': checkRequestSchema,
} as const;

// (b) File tổng hợp có $defs đặt tên — dùng để SINH model pydantic có tên rõ ràng (datamodel-code-generator).
function buildMessagesSchema(): unknown {
  return zodToJsonSchema(z.object({}).describe('FastCheck WS/queue message definitions'), {
    target: 'jsonSchema7',
    definitions: {
      ProfileConfig: profileConfigSchema,
      StationInfo: stationInfoSchema,
      StationProfile: stationProfileSchema,
      RegisterMessage: registerMessageSchema,
      HeartbeatMessage: heartbeatMessageSchema,
      CommandAckMessage: commandAckMessageSchema,
      ProfileSyncMessage: profileSyncMessageSchema,
      JobResultMessage: jobResultMessageSchema,
      CookieRefreshMessage: cookieRefreshMessageSchema,
      JobProgressMessage: jobProgressMessageSchema,
      RunCommand: runCommandSchema,
      BrowserOpenCommand: browserOpenCommandSchema,
      BrowserCloseCommand: browserCloseCommandSchema,
      ProfileCreateCommand: profileCreateCommandSchema,
      ProfileUpdateCommand: profileUpdateCommandSchema,
      ProfileDeleteCommand: profileDeleteCommandSchema,
      LoginRunCommand: loginRunCommandSchema,
      CdpForwardCommand: cdpForwardCommandSchema,
      ServerCommand: serverCommandSchema,
      RegisteredMessage: registeredMessageSchema,
      CheckJobMessage: checkJobMessageSchema,
    },
  });
}

/** Sinh map {tên file → JSON Schema (draft-07)}. */
export function buildSchemas(): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [file, schema] of Object.entries(UNION_SOURCES)) {
    out[file] = zodToJsonSchema(schema, { target: 'jsonSchema7', $refStrategy: 'none' });
  }
  out['messages.schema.json'] = buildMessagesSchema();
  return out;
}
