// DI tokens cho các provider không phải class (env, db, logger).
export const ENV = Symbol('ENV');
export const DB_CONN = Symbol('DB_CONN');
export const LOGGER = Symbol('LOGGER');
