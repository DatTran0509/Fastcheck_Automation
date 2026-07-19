// DI tokens cho các provider không phải class (env, db, logger, redis, cookie cipher).
export const ENV = Symbol('ENV');
export const DB_CONN = Symbol('DB_CONN');
export const LOGGER = Symbol('LOGGER');
export const REDIS = Symbol('REDIS');
export const COOKIE_CIPHER = Symbol('COOKIE_CIPHER');
