// Domain enums — the SINGLE source of truth for shared vocabulary across TS services.
// Values are UPPER_SNAKE and MUST match the Postgres enums exactly (docs/data-model.md).
// The Python worker mirrors these values in pydantic (ADR-0006); keep them in sync.

export enum Platform {
  TIKTOK = 'TIKTOK',
  FACEBOOK = 'FACEBOOK',
  TWITTER = 'TWITTER',
  YOUTUBE = 'YOUTUBE',
}

/** Trạng thái của TARGET (link). Ba nhánh bình đẳng — INCONCLUSIVE KHÔNG phải DEAD (INV-1). */
export enum UrlStatus {
  LIVE = 'LIVE',
  DEAD = 'DEAD',
  INCONCLUSIVE = 'INCONCLUSIVE',
}

/** Sức khoẻ của PROFILE lúc check. Enum TÁCH BIỆT với UrlStatus — không gộp (INV-3). */
export enum ProfileHealth {
  OK = 'OK',
  CHALLENGED = 'CHALLENGED',
  BLOCKED = 'BLOCKED',
  THROTTLED = 'THROTTLED',
}

export enum ProfileStatus {
  AVAILABLE = 'AVAILABLE',
  IN_USE = 'IN_USE',
  COOLDOWN = 'COOLDOWN',
  DEAD = 'DEAD',
  BLOCKED = 'BLOCKED',
}

export enum JobStatus {
  PENDING = 'PENDING',
  RUNNING = 'RUNNING',
  DONE = 'DONE',
  FAILED = 'FAILED',
  DEAD_LETTER = 'DEAD_LETTER',
}

export enum StationStatus {
  ONLINE = 'ONLINE',
  OFFLINE = 'OFFLINE',
  DRAINING = 'DRAINING',
}

export enum ProxyType {
  RESIDENTIAL = 'RESIDENTIAL',
  MOBILE = 'MOBILE',
  DATACENTER = 'DATACENTER',
}

export enum ProxyStatus {
  ACTIVE = 'ACTIVE',
  BANNED = 'BANNED',
  COOLDOWN = 'COOLDOWN',
}
