import { describe, it, expect } from 'vitest';
import { wsClientMessageSchema, serverCommandSchema } from './ws.protocol.js';

describe('WS protocol', () => {
  it('parse register hợp lệ', () => {
    const msg = wsClientMessageSchema.parse({
      type: 'register',
      station: {
        station_id: '00000000-0000-4000-8000-000000000001',
        name: 'dev-station',
        agent_version: '0.0.1',
        max_concurrency: 4,
      },
    });
    expect(msg.type).toBe('register');
  });

  it('từ chối message type lạ', () => {
    expect(() => wsClientMessageSchema.parse({ type: 'nope' })).toThrow();
  });

  it('lệnh Server→Client bắt buộc có command_id (INV-14)', () => {
    expect(() =>
      serverCommandSchema.parse({
        type: 'command',
        command: { name: 'browser.close', profile_id: '00000000-0000-4000-8000-000000000009' },
      }),
    ).toThrow();
  });
});
