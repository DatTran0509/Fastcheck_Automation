import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import dotenv from 'dotenv';
import { z } from 'zod';

/**
 * Ném khi env không hợp lệ. Fail-fast: app chết ngay lúc khởi động với thông báo rõ,
 * thay vì chạy nửa vời rồi lỗi âm thầm lúc runtime (docs/tech-stack.md, packages/config).
 */
export class EnvValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'EnvValidationError';
  }
}

// ── Mảnh schema tái sử dụng ───────────────────────────────────────────────────
const nodeEnv = z.enum(['development', 'production', 'test']).default('development');
const logLevel = z.enum(['trace', 'debug', 'info', 'warn', 'error', 'fatal']).default('info');
const port = () => z.coerce.number().int().positive().max(65535);
const positiveInt = () => z.coerce.number().int().positive();

export const baseEnvSchema = z.object({
  NODE_ENV: nodeEnv,
  LOG_LEVEL: logLevel,
});

export const postgresEnvSchema = z.object({
  DATABASE_URL: z.string().url(),
});

export const redisEnvSchema = z.object({
  REDIS_URL: z.string().url(),
});

export const rabbitmqEnvSchema = z.object({
  RABBITMQ_URL: z.string().url(),
});

/** Cookie encryption key: base64 của đúng 32 byte (AES-256). INV-12. */
export const cryptoEnvSchema = z.object({
  COOKIE_ENC_KEY: z
    .string()
    .refine(
      (s) => decodedByteLength(s) === 32,
      'COOKIE_ENC_KEY phải là base64 của đúng 32 byte (AES-256). Sinh: node -e "console.log(require(\'crypto\').randomBytes(32).toString(\'base64\'))"',
    ),
  COOKIE_KEY_ID: z.string().min(1),
  // Khoá CŨ để xoay khoá (INV-12): JSON {"<keyId>": "<base64 32 byte>", ...}. Optional.
  COOKIE_ENC_KEYS: z
    .string()
    .optional()
    .refine(
      (s) => s === undefined || isValidKeyringJson(s),
      'COOKIE_ENC_KEYS phải là JSON dạng {"keyId": base64-32byte} (chứa khoá cũ để giải mã cookie trước khi xoay khoá)',
    ),
});

/** Keyring gộp: khoá active + các khoá cũ. crypto.createCookieCipher(active, id, olderKeys) tiêu thụ trực tiếp. */
export interface CookieKeyring {
  activeKeyId: string;
  activeKeyBase64: string;
  olderKeys: Record<string, string>;
}

export function cookieKeyringFromEnv(env: {
  COOKIE_ENC_KEY: string;
  COOKIE_KEY_ID: string;
  COOKIE_ENC_KEYS?: string;
}): CookieKeyring {
  const olderKeys: Record<string, string> = env.COOKIE_ENC_KEYS
    ? (JSON.parse(env.COOKIE_ENC_KEYS) as Record<string, string>)
    : {};
  return {
    activeKeyId: env.COOKIE_KEY_ID,
    activeKeyBase64: env.COOKIE_ENC_KEY,
    olderKeys,
  };
}

export const apiEnvSchema = z.object({
  API_HOST: z.string().default('0.0.0.0'),
  API_PORT: port().default(3001),
  RESULT_CACHE_TTL_LIVE_SECONDS: positiveInt().default(300),
  RESULT_CACHE_TTL_DEAD_SECONDS: positiveInt().default(900),
  RATE_LIMIT_PER_MINUTE: positiveInt().default(120),
});

export const orchestratorEnvSchema = z.object({
  ORCHESTRATOR_HOST: z.string().default('0.0.0.0'),
  ORCHESTRATOR_PORT: port().default(3002),
  WS_AUTH_TOKEN: z.string().min(1),
  ORCHESTRATOR_PREFETCH: positiveInt().default(20),
});

// ── Schema tổng hợp theo app ──────────────────────────────────────────────────
// api KHÔNG cần crypto env (không giải mã cookie — orchestrator lo, ADR-0006).
export const apiConfigSchema = baseEnvSchema
  .merge(postgresEnvSchema)
  .merge(redisEnvSchema)
  .merge(rabbitmqEnvSchema)
  .merge(apiEnvSchema);

// orchestrator giải mã cookie qua packages/crypto → cần crypto env.
export const orchestratorConfigSchema = baseEnvSchema
  .merge(postgresEnvSchema)
  .merge(redisEnvSchema)
  .merge(rabbitmqEnvSchema)
  .merge(cryptoEnvSchema)
  .merge(orchestratorEnvSchema);

export type ApiEnv = z.infer<typeof apiConfigSchema>;
export type OrchestratorEnv = z.infer<typeof orchestratorConfigSchema>;

/**
 * Parse + validate env theo `schema`. Fail-fast: throw `EnvValidationError` với danh sách
 * biến sai/thiếu nếu không hợp lệ. Thuần: chỉ đọc `env` truyền vào (mặc định `process.env`),
 * KHÔNG tự nạp .env — dùng `loadApiEnv`/`loadOrchestratorEnv` nếu muốn nạp .env trước.
 */
export function parseEnv<T extends z.ZodTypeAny>(
  schema: T,
  env: NodeJS.ProcessEnv = process.env,
): z.infer<T> {
  const result = schema.safeParse(env);
  if (!result.success) {
    const issues = result.error.issues
      .map((i) => `  - ${i.path.join('.') || '(root)'}: ${i.message}`)
      .join('\n');
    throw new EnvValidationError(`Cấu hình môi trường không hợp lệ:\n${issues}`);
  }
  return result.data;
}

export function loadApiEnv(env?: NodeJS.ProcessEnv): ApiEnv {
  ensureDotenvLoaded();
  return parseEnv(apiConfigSchema, env ?? process.env);
}

export function loadOrchestratorEnv(env?: NodeJS.ProcessEnv): OrchestratorEnv {
  ensureDotenvLoaded();
  return parseEnv(orchestratorConfigSchema, env ?? process.env);
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function decodedByteLength(base64: string): number {
  try {
    return Buffer.from(base64, 'base64').length;
  } catch {
    return -1;
  }
}

function isValidKeyringJson(s: string): boolean {
  let parsed: unknown;
  try {
    parsed = JSON.parse(s);
  } catch {
    return false;
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return false;
  return Object.values(parsed as Record<string, unknown>).every(
    (v) => typeof v === 'string' && decodedByteLength(v) === 32,
  );
}

let dotenvLoaded = false;
/** Nạp .env (tìm ngược lên từ cwd tới gốc repo) đúng một lần. Không ghi đè biến đã có sẵn. */
export function ensureDotenvLoaded(startDir: string = process.cwd()): void {
  if (dotenvLoaded) return;
  dotenvLoaded = true;
  const path = findUpwards(startDir, '.env');
  if (path) dotenv.config({ path });
}

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
