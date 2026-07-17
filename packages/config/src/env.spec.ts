import { describe, it, expect } from 'vitest';
import {
  parseEnv,
  apiConfigSchema,
  orchestratorConfigSchema,
  cryptoEnvSchema,
  cookieKeyringFromEnv,
  EnvValidationError,
} from './env.js';

const validInfra = {
  DATABASE_URL: 'postgres://u:p@localhost:5432/db',
  REDIS_URL: 'redis://localhost:6379',
  RABBITMQ_URL: 'amqp://u:p@localhost:5672',
};

describe('parseEnv fail-fast', () => {
  it('thiếu biến bắt buộc → ném EnvValidationError', () => {
    expect(() => parseEnv(apiConfigSchema, {})).toThrow(EnvValidationError);
  });

  it('thông báo lỗi nêu rõ TÊN biến thiếu (không nuốt lỗi âm thầm)', () => {
    let message = '';
    try {
      parseEnv(apiConfigSchema, {});
    } catch (e) {
      message = (e as Error).message;
    }
    expect(message).toContain('DATABASE_URL');
    expect(message).toContain('REDIS_URL');
    expect(message).toContain('RABBITMQ_URL');
  });

  it('env hợp lệ → parse ok + áp default', () => {
    const env = parseEnv(apiConfigSchema, { ...validInfra });
    expect(env.API_PORT).toBe(3001);
    expect(env.NODE_ENV).toBe('development');
    expect(env.RESULT_CACHE_TTL_LIVE_SECONDS).toBeLessThan(env.RESULT_CACHE_TTL_DEAD_SECONDS);
  });

  it('COOKIE_ENC_KEY sai độ dài → orchestrator config fail (INV-12)', () => {
    let message = '';
    try {
      parseEnv(orchestratorConfigSchema, {
        ...validInfra,
        WS_AUTH_TOKEN: 'x',
        COOKIE_ENC_KEY: 'too-short',
        COOKIE_KEY_ID: 'k1',
      });
    } catch (e) {
      message = (e as Error).message;
    }
    expect(message).toContain('COOKIE_ENC_KEY');
  });

  it('COOKIE_ENC_KEY 32 byte base64 hợp lệ → orchestrator config ok', () => {
    const key = Buffer.alloc(32, 7).toString('base64');
    const env = parseEnv(orchestratorConfigSchema, {
      ...validInfra,
      WS_AUTH_TOKEN: 'secret',
      COOKIE_ENC_KEY: key,
      COOKIE_KEY_ID: 'k1',
    });
    expect(env.ORCHESTRATOR_PORT).toBe(3002);
    expect(env.COOKIE_KEY_ID).toBe('k1');
  });
});

describe('cookie keyring — xoay khoá (INV-12)', () => {
  const active = Buffer.alloc(32, 1).toString('base64');
  const old1 = Buffer.alloc(32, 9).toString('base64');

  it('COOKIE_ENC_KEYS hợp lệ → parse + gộp khoá cũ', () => {
    const env = parseEnv(cryptoEnvSchema, {
      COOKIE_ENC_KEY: active,
      COOKIE_KEY_ID: 'k2',
      COOKIE_ENC_KEYS: JSON.stringify({ k1: old1 }),
    });
    const keyring = cookieKeyringFromEnv(env);
    expect(keyring.activeKeyId).toBe('k2');
    expect(keyring.olderKeys.k1).toBe(old1);
  });

  it('không có COOKIE_ENC_KEYS → olderKeys rỗng', () => {
    const env = parseEnv(cryptoEnvSchema, { COOKIE_ENC_KEY: active, COOKIE_KEY_ID: 'k1' });
    expect(cookieKeyringFromEnv(env).olderKeys).toEqual({});
  });

  it('COOKIE_ENC_KEYS khoá sai độ dài → fail-fast', () => {
    let message = '';
    try {
      parseEnv(cryptoEnvSchema, {
        COOKIE_ENC_KEY: active,
        COOKIE_KEY_ID: 'k1',
        COOKIE_ENC_KEYS: JSON.stringify({ kbad: Buffer.alloc(16).toString('base64') }),
      });
    } catch (e) {
      message = (e as Error).message;
    }
    expect(message).toContain('COOKIE_ENC_KEYS');
  });

  it('COOKIE_ENC_KEYS JSON hỏng → fail-fast', () => {
    expect(() =>
      parseEnv(cryptoEnvSchema, {
        COOKIE_ENC_KEY: active,
        COOKIE_KEY_ID: 'k1',
        COOKIE_ENC_KEYS: 'not-json',
      }),
    ).toThrow(EnvValidationError);
  });
});
