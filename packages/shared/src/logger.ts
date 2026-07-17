import { pino, type Logger } from 'pino';

export type { Logger };

// INV-12: NEVER log cookie/credential. These paths are redacted before anything reaches a transport.
const REDACT_PATHS = [
  'cookie',
  'cookies',
  'cookie_ciphertext',
  'cookieCiphertext',
  'credential',
  'credentials',
  'password',
  'proxy_url_enc',
  'authorization',
  '*.cookie',
  '*.cookies',
  '*.cookie_ciphertext',
  '*.credential',
  '*.password',
  '*.authorization',
  'req.headers.authorization',
  'req.headers.cookie',
];

export interface LoggerOptions {
  name?: string;
  level?: string;
}

/** Tạo pino logger JSON có redaction cookie/credential (INV-12). Gắn trace_id bằng `withTrace`. */
export function createLogger(opts: LoggerOptions = {}): Logger {
  return pino({
    name: opts.name,
    level: opts.level ?? process.env.LOG_LEVEL ?? 'info',
    redact: { paths: REDACT_PATHS, censor: '[REDACTED]' },
    timestamp: pino.stdTimeFunctions.isoTime,
  });
}

/** Logger con luôn đính `trace_id` — truy vết một job xuyên toàn hệ. */
export function withTrace(logger: Logger, traceId: string): Logger {
  return logger.child({ trace_id: traceId });
}
