import type { ColumnType, Generated, Selectable } from 'kysely';
import type {
  JobStatus,
  Platform,
  ProfileHealth,
  ProfileStatus,
  ProxyStatus,
  ProxyType,
  StationStatus,
  UrlStatus,
} from '@fastcheck/shared';

type NullableTimestamp = ColumnType<Date | null, Date | string | null, Date | string | null>;

export interface StationsTable {
  id: Generated<string>;
  name: string | null;
  mac_address: string | null;
  ip_address: string | null;
  status: Generated<StationStatus>;
  max_concurrency: Generated<number>;
  current_load: Generated<number>;
  agent_version: string | null;
  last_ping_at: NullableTimestamp;
}

export interface ProxiesTable {
  id: Generated<string>;
  proxy_url_enc: Buffer | null;
  type: ProxyType;
  region: string | null;
  status: Generated<ProxyStatus>;
  fail_count: Generated<number>;
}

export interface ProfilesTable {
  id: Generated<string>;
  // NULL = profile GemLogin đã mirror về nhưng CHƯA gán nền tảng (chưa "Nạp tài khoản"/chưa nhãn note).
  // Hiển thị trong "Xem profile", nhưng claimProfile lọc `platform = X` nên không bao giờ được cấp job (§3).
  platform: Platform | null;
  account_label: string | null;
  gemlogin_profile_id: string | null; // id phía GemLogin (§3 sync) — khác id UUID nội bộ
  cookie_ciphertext: Buffer | null;
  cookie_key_id: string | null;
  proxy_id: string | null;
  assigned_station_id: string | null;
  status: Generated<ProfileStatus>;
  health_score: Generated<number>;
  lease_expires_at: NullableTimestamp;
  cooldown_until: NullableTimestamp;
  consecutive_fails: Generated<number>;
  last_used_at: NullableTimestamp;
}

export interface CheckJobsTable {
  id: Generated<string>;
  trace_id: string;
  target_url: string;
  url_hash: string;
  platform: Platform;
  status: Generated<JobStatus>;
  retry_count: Generated<number>;
  result: ColumnType<UrlStatus | null, UrlStatus | null | undefined, UrlStatus | null>;
  assigned_station_id: string | null;
  assigned_profile_id: string | null;
  dispatched_at: NullableTimestamp;
  created_at: Generated<Date>;
  finished_at: NullableTimestamp;
}

export interface CheckLogsTable {
  id: Generated<string>; // bigint → chuỗi (pg trả bigint dạng string)
  trace_id: string;
  job_id: string | null;
  profile_id: string | null;
  target_url: string;
  url_status: UrlStatus; // trạng thái TARGET (INV-3)
  profile_health: ProfileHealth; // sức khoẻ PROFILE — TÁCH BIỆT (INV-3)
  block_reason: string | null;
  response_time_ms: number | null;
  checked_at: Generated<Date>;
}

export interface Database {
  stations: StationsTable;
  proxies: ProxiesTable;
  profiles: ProfilesTable;
  check_jobs: CheckJobsTable;
  check_logs: CheckLogsTable;
}

export type Station = Selectable<StationsTable>;
export type Proxy = Selectable<ProxiesTable>;
export type Profile = Selectable<ProfilesTable>;
export type CheckJob = Selectable<CheckJobsTable>;
export type CheckLog = Selectable<CheckLogsTable>;
