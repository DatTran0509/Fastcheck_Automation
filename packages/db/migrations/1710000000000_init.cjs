/* eslint-disable */
// Migration khởi tạo: 5 bảng đúng docs/data-model.md + 3 cột dispatch (INV-15) + index claim (INV-11)
// + UNIQUE(url_hash) partial (INV-13) + index (status, assigned_station_id) (INV-15).
// gen_random_uuid() có sẵn trong Postgres 13+ (không cần extension).
// Lưu ý Phase 0: check_logs để bảng thường; PARTITION BY RANGE(checked_at) là mục tiêu spec, thêm ở phase sau.

exports.shorthands = undefined;

exports.up = (pgm) => {
  pgm.createType('platform', ['TIKTOK', 'FACEBOOK', 'TWITTER', 'YOUTUBE']);
  pgm.createType('url_status', ['LIVE', 'DEAD', 'INCONCLUSIVE']);
  pgm.createType('profile_health', ['OK', 'CHALLENGED', 'BLOCKED', 'THROTTLED']);
  pgm.createType('profile_status', ['AVAILABLE', 'IN_USE', 'COOLDOWN', 'DEAD', 'BLOCKED']);
  pgm.createType('job_status', ['PENDING', 'RUNNING', 'DONE', 'FAILED', 'DEAD_LETTER']);
  pgm.createType('station_status', ['ONLINE', 'OFFLINE', 'DRAINING']);
  pgm.createType('proxy_type', ['RESIDENTIAL', 'MOBILE', 'DATACENTER']);
  pgm.createType('proxy_status', ['ACTIVE', 'BANNED', 'COOLDOWN']);

  pgm.createTable('stations', {
    id: { type: 'uuid', primaryKey: true, default: pgm.func('gen_random_uuid()') },
    name: { type: 'varchar(100)' },
    mac_address: { type: 'varchar(255)' },
    ip_address: { type: 'inet' },
    status: { type: 'station_status', notNull: true, default: 'OFFLINE' },
    max_concurrency: { type: 'integer', notNull: true, default: 1 },
    current_load: { type: 'integer', notNull: true, default: 0 },
    agent_version: { type: 'varchar(50)' },
    last_ping_at: { type: 'timestamptz' },
  });

  pgm.createTable('proxies', {
    id: { type: 'uuid', primaryKey: true, default: pgm.func('gen_random_uuid()') },
    proxy_url_enc: { type: 'bytea' },
    type: { type: 'proxy_type', notNull: true },
    region: { type: 'varchar(50)' },
    status: { type: 'proxy_status', notNull: true, default: 'ACTIVE' },
    fail_count: { type: 'integer', notNull: true, default: 0 },
  });

  pgm.createTable('profiles', {
    id: { type: 'uuid', primaryKey: true, default: pgm.func('gen_random_uuid()') },
    platform: { type: 'platform', notNull: true },
    account_label: { type: 'varchar(100)' },
    cookie_ciphertext: { type: 'bytea' },
    cookie_key_id: { type: 'varchar(50)' },
    proxy_id: { type: 'uuid', references: 'proxies', onDelete: 'SET NULL' },
    assigned_station_id: { type: 'uuid', references: 'stations', onDelete: 'SET NULL' },
    status: { type: 'profile_status', notNull: true, default: 'AVAILABLE' },
    health_score: { type: 'smallint', notNull: true, default: 100 },
    lease_expires_at: { type: 'timestamptz' },
    cooldown_until: { type: 'timestamptz' },
    consecutive_fails: { type: 'smallint', notNull: true, default: 0 },
    last_used_at: { type: 'timestamptz' },
  });

  pgm.createTable('check_jobs', {
    id: { type: 'uuid', primaryKey: true, default: pgm.func('gen_random_uuid()') },
    trace_id: { type: 'uuid', notNull: true },
    target_url: { type: 'text', notNull: true },
    url_hash: { type: 'varchar(64)', notNull: true },
    platform: { type: 'platform', notNull: true },
    status: { type: 'job_status', notNull: true, default: 'PENDING' },
    retry_count: { type: 'smallint', notNull: true, default: 0 },
    result: { type: 'url_status' },
    // 3 cột dispatch (INV-15): biết job nào đang ở station nào để thu hồi khi station chết.
    assigned_station_id: { type: 'uuid', references: 'stations', onDelete: 'SET NULL' },
    assigned_profile_id: { type: 'uuid', references: 'profiles', onDelete: 'SET NULL' },
    dispatched_at: { type: 'timestamptz' },
    created_at: { type: 'timestamptz', notNull: true, default: pgm.func('now()') },
    finished_at: { type: 'timestamptz' },
  });

  // check_logs — PARTITION BY RANGE(checked_at) (spec §6.4). PK phải gồm cột phân vùng → (id, checked_at).
  // Dùng raw SQL vì node-pg-migrate không mô tả partitioning qua createTable.
  pgm.sql(`
    CREATE SEQUENCE check_logs_id_seq;
    CREATE TABLE check_logs (
      id               bigint NOT NULL DEFAULT nextval('check_logs_id_seq'),
      trace_id         uuid NOT NULL,
      job_id           uuid REFERENCES check_jobs(id) ON DELETE SET NULL,
      profile_id       uuid REFERENCES profiles(id) ON DELETE SET NULL,
      target_url       text NOT NULL,
      url_status       url_status NOT NULL,        -- TARGET (INV-3)
      profile_health   profile_health NOT NULL,    -- PROFILE — tách biệt (INV-3)
      block_reason     text,
      response_time_ms integer,
      checked_at       timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (id, checked_at)
    ) PARTITION BY RANGE (checked_at);
    ALTER SEQUENCE check_logs_id_seq OWNED BY check_logs.id;
    -- Partition mặc định cho Phase 0; partition theo tháng do maintenance job tạo ở phase sau.
    CREATE TABLE check_logs_default PARTITION OF check_logs DEFAULT;
    CREATE INDEX idx_check_logs_profile ON check_logs (profile_id, checked_at);
    CREATE INDEX idx_check_logs_trace ON check_logs (trace_id);
  `);

  // Index claim profile (INV-11)
  pgm.createIndex('profiles', ['platform', 'status', 'cooldown_until'], {
    name: 'idx_profiles_claim',
    where: "status = 'AVAILABLE'",
  });

  // Dedupe job: UNIQUE(url_hash) chỉ khi đang active (INV-13)
  pgm.createIndex('check_jobs', ['url_hash'], {
    name: 'uq_check_jobs_active_url_hash',
    unique: true,
    where: "status IN ('PENDING','RUNNING')",
  });

  // Thu hồi nhanh job của station chết (INV-15)
  pgm.createIndex('check_jobs', ['status', 'assigned_station_id'], {
    name: 'idx_check_jobs_status_station',
  });
  pgm.createIndex('check_jobs', ['trace_id'], { name: 'idx_check_jobs_trace' });
  // (index của check_logs tạo cùng bảng ở khối raw SQL bên trên)
};

exports.down = (pgm) => {
  pgm.dropTable('check_logs');
  pgm.dropTable('check_jobs');
  pgm.dropTable('profiles');
  pgm.dropTable('proxies');
  pgm.dropTable('stations');
  pgm.dropType('proxy_status');
  pgm.dropType('proxy_type');
  pgm.dropType('station_status');
  pgm.dropType('job_status');
  pgm.dropType('profile_status');
  pgm.dropType('profile_health');
  pgm.dropType('url_status');
  pgm.dropType('platform');
};
