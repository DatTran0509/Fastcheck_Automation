/**
 * CdpRelayGateway (§5, INV-12): relay CDP ghép cầu worker↔controller theo session, WSS + token.
 * Test THẬT bằng http server + ws client (không cần DB/Redis). Xác minh: (1) bơm gói hai chiều đúng session,
 * (2) fail-fast khi bật forward mà thiếu token, (3) từ chối token sai ở handshake.
 */
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { WebSocket } from 'ws';
import { afterEach, describe, expect, it } from 'vitest';
import { CdpRelayGateway } from '../src/cdp/cdp-relay.gateway.js';

const logger = { info() {}, warn() {}, error() {}, debug() {} } as never;

function once(ws: WebSocket, event: string): Promise<unknown> {
  return new Promise((resolve, reject) => {
    ws.once(event, resolve);
    ws.once('error', reject);
  });
}
function onceMessage(ws: WebSocket): Promise<string> {
  return new Promise((resolve) => ws.once('message', (d: Buffer) => resolve(d.toString())));
}

let server: Server | undefined;
afterEach(async () => {
  if (server) await new Promise<void>((r) => server!.close(() => r()));
  server = undefined;
});

async function startRelay(env: Record<string, unknown>): Promise<number> {
  server = createServer();
  await new Promise<void>((r) => server!.listen(0, r));
  new CdpRelayGateway(env as never, logger).attach(server);
  return (server.address() as AddressInfo).port;
}

describe('CdpRelayGateway', () => {
  it('bắc cầu hai chiều worker↔controller theo session (WSS+token)', async () => {
    const port = await startRelay({ CDP_FORWARD_ENABLED: true, CDP_FORWARD_TOKEN: 'tok' });
    const headers = { Authorization: 'Bearer tok' };
    const worker = new WebSocket(`ws://127.0.0.1:${port}/cdp?role=worker&session=S1`, { headers });
    const controller = new WebSocket(`ws://127.0.0.1:${port}/cdp?role=controller&session=S1`, { headers });
    await Promise.all([once(worker, 'open'), once(controller, 'open')]);

    // controller → worker (lệnh CDP đi vào browser)
    const atWorker = onceMessage(worker);
    controller.send('{"id":1,"method":"Page.navigate"}');
    expect(await atWorker).toBe('{"id":1,"method":"Page.navigate"}');

    // worker → controller (phản hồi CDP ra ngoài)
    const atController = onceMessage(controller);
    worker.send('{"id":1,"result":{}}');
    expect(await atController).toBe('{"id":1,"result":{}}');

    worker.close();
    controller.close();
  });

  it('KHÔNG rò chéo session: gói của S1 không sang S2', async () => {
    const port = await startRelay({ CDP_FORWARD_ENABLED: true, CDP_FORWARD_TOKEN: 'tok' });
    const h = { Authorization: 'Bearer tok' };
    const w1 = new WebSocket(`ws://127.0.0.1:${port}/cdp?role=worker&session=S1`, { headers: h });
    const c1 = new WebSocket(`ws://127.0.0.1:${port}/cdp?role=controller&session=S1`, { headers: h });
    const w2 = new WebSocket(`ws://127.0.0.1:${port}/cdp?role=worker&session=S2`, { headers: h });
    await Promise.all([once(w1, 'open'), once(c1, 'open'), once(w2, 'open')]);

    let w2got = false;
    w2.on('message', () => {
      w2got = true;
    });
    const atC1 = onceMessage(c1);
    w1.send('chỉ-cho-S1');
    expect(await atC1).toBe('chỉ-cho-S1');
    expect(w2got).toBe(false);

    w1.close();
    c1.close();
    w2.close();
  });

  it('fail-fast: bật forward mà thiếu token → throw (INV-12)', async () => {
    server = createServer();
    await new Promise<void>((r) => server!.listen(0, r));
    const gw = new CdpRelayGateway({ CDP_FORWARD_ENABLED: true, CDP_FORWARD_TOKEN: undefined } as never, logger);
    expect(() => gw.attach(server!)).toThrow(/CDP_FORWARD_TOKEN/);
  });

  it('từ chối token sai ở handshake (không mở kết nối)', async () => {
    const port = await startRelay({ CDP_FORWARD_ENABLED: true, CDP_FORWARD_TOKEN: 'tok' });
    const bad = new WebSocket(`ws://127.0.0.1:${port}/cdp?role=worker&session=S1`, {
      headers: { Authorization: 'Bearer SAI' },
    });
    const outcome = await new Promise<string>((resolve) => {
      bad.once('open', () => resolve('open'));
      bad.once('unexpected-response', () => resolve('rejected'));
      bad.once('error', () => resolve('rejected'));
    });
    expect(outcome).toBe('rejected');
  });
});
