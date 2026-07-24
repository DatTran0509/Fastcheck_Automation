import { sql } from 'kysely';
import { ProfileStatus, type Platform } from '@fastcheck/shared';
import type { DB } from '../client.js';
import type { Profile } from '../types.js';

/**
 * Claim profile ATOMIC (INV-11): một câu UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *.
 * SKIP LOCKED cho tới 50 worker lấy song song không dẫm chân nhau; set lease chống kẹt IN_USE (docs/data-model.md §claim).
 * Ưu tiên khoẻ nhất + nghỉ lâu nhất (health_score DESC, last_used_at ASC) để xoay vòng cả pool (skill §4).
 * Trả `null` nếu pool cạn (không còn AVAILABLE cùng platform, ngoài cooldown).
 */
export async function claimProfile(
  db: DB,
  platform: Platform,
  stationId: string,
  leaseMinutes = 5,
): Promise<Profile | null> {
  const result = await sql<Profile>`
    UPDATE profiles
    SET status = 'IN_USE',
        lease_expires_at = now() + make_interval(mins => ${leaseMinutes}),
        assigned_station_id = ${stationId},
        last_used_at = now()
    WHERE id = (
      SELECT id FROM profiles
      WHERE platform = ${platform}
        AND status = 'AVAILABLE'
        AND (cooldown_until IS NULL OR cooldown_until < now())
      ORDER BY health_score DESC, last_used_at ASC NULLS FIRST
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    RETURNING *;
  `.execute(db);
  return result.rows[0] ?? null;
}

/**
 * Trả profile về pool (AVAILABLE), xoá lease. Dùng khi kết quả KHÔNG phải lỗi profile.
 * GIỮ `assigned_station_id` (station SỞ HỮU profile — cố định, KHÔNG xoá): lease tạm thời thể hiện bằng
 * `status`/`lease_expires_at`, còn assigned_station_id là "profile này nằm ở station nào" (để list/route đúng).
 */
export async function releaseProfile(db: DB, profileId: string): Promise<void> {
  await db
    .updateTable('profiles')
    .set({ status: ProfileStatus.AVAILABLE, lease_expires_at: null, last_error: null, last_error_at: null })
    .where('id', '=', profileId)
    .execute();
}

/**
 * Refresh cookie sau phiên đăng nhập thành công (spec §4.4): lưu cookie MỚI đã mã hoá + keyId hiện hành.
 * Chỉ đụng cột cookie (KHÔNG động status/health pool — INV-3/INV-11). Không log giá trị (INV-12).
 */
export async function updateCookie(
  db: DB,
  profileId: string,
  ciphertext: Buffer,
  keyId: string,
): Promise<void> {
  await db
    .updateTable('profiles')
    .set({ cookie_ciphertext: ciphertext, cookie_key_id: keyId })
    .where('id', '=', profileId)
    .execute();
}

/**
 * Lưu cookie mới theo **gemlogin_profile_id** (KHOÁ pool thật — 1 GemLogin profile = 1 dòng, INV-6). Dùng cho
 * cookie_refresh sau phiên login thành công: lệnh login do dashboard tạo mang `profile_id` là uuid TẠM (không
 * khớp dòng nào), nhưng `gemlogin_profile_id` thì khớp dòng đã nạp/mirror → lưu đúng chỗ. Trả số dòng đã cập
 * nhật (0 = profile chưa vào pool → caller cảnh báo, KHÔNG nuốt). KHÔNG log giá trị cookie (INV-12).
 */
export async function updateCookieByGemlogin(
  db: DB,
  gemloginProfileId: string,
  ciphertext: Buffer,
  keyId: string,
): Promise<number> {
  const res = await db
    .updateTable('profiles')
    .set({ cookie_ciphertext: ciphertext, cookie_key_id: keyId })
    .where('gemlogin_profile_id', '=', gemloginProfileId)
    .executeTakeFirst();
  return Number(res?.numUpdatedRows ?? 0n);
}

/**
 * Phiên THÀNH CÔNG: trả về AVAILABLE, hồi `health_score` (cap 100), reset `consecutive_fails`.
 * health_score hồi dần khi các phiên thành công liên tiếp (skill §Health).
 */
export async function recordSuccess(db: DB, profileId: string, healthBump: number): Promise<void> {
  await sql`
    UPDATE profiles
    SET status = 'AVAILABLE',
        lease_expires_at = NULL,
        consecutive_fails = 0,
        health_score = LEAST(100, health_score + ${healthBump}),
        last_used_at = now(),
        last_error = NULL,
        last_error_at = NULL
    WHERE id = ${profileId};
  `.execute(db);
}

export interface RecordFailureInput {
  profileId: string;
  healthPenalty: number; // trừ health_score mỗi lần challenge/block
  cooldownSeconds: number; // thời gian nghỉ khi COOLDOWN
  deadThreshold: number; // consecutive_fails >= ngưỡng → DEAD (loại khỏi pool)
  reason?: string | null; // lý do (vd "CHALLENGED: guard đăng nhập thất bại") — hiển thị cho operator
}

/**
 * Phiên LỖI PROFILE (BLOCKED/CHALLENGED/THROTTLED — auto-switch §4.6):
 * giảm `health_score`, tăng `consecutive_fails`; nếu vượt ngưỡng → `DEAD` (loại), ngược lại → `COOLDOWN`
 * (nghỉ thay vì giết ngay — giữ tuổi thọ pool, skill §3). Trả profile SAU cập nhật để biết đã DEAD hay COOLDOWN.
 */
export async function recordFailure(db: DB, input: RecordFailureInput): Promise<Profile | null> {
  const reason = input.reason ?? null;
  const result = await sql<Profile>`
    UPDATE profiles
    SET consecutive_fails = consecutive_fails + 1,
        health_score = GREATEST(0, health_score - ${input.healthPenalty}),
        lease_expires_at = NULL,
        status = CASE
          WHEN consecutive_fails + 1 >= ${input.deadThreshold} THEN 'DEAD'::profile_status
          ELSE 'COOLDOWN'::profile_status
        END,
        cooldown_until = CASE
          WHEN consecutive_fails + 1 >= ${input.deadThreshold} THEN NULL
          ELSE now() + make_interval(secs => ${input.cooldownSeconds})
        END,
        last_error = COALESCE(${reason}, last_error),
        last_error_at = CASE WHEN ${reason}::text IS NULL THEN last_error_at ELSE now() END
    WHERE id = ${input.profileId}
    RETURNING *;
  `.execute(db);
  return result.rows[0] ?? null;
}

/**
 * Rest một profile TẠM THỜI (COOLDOWN) do lỗi HẠ TẦNG — browser không mở được / GemLogin kẹt "being opened" —
 * KHÔNG phải lỗi tài khoản. Đặt cooldown để NGỪNG cấp job (cho GemLogin hồi, cắt vòng hammer) NHƯNG **không**
 * tăng `consecutive_fails` / **không** giảm `health_score`: tài khoản vẫn tốt, không được DEAD vì GemLogin
 * hiccup. Tự AVAILABLE lại sau cooldown (claimProfile bỏ qua profile còn trong cooldown). Khác `recordFailure`
 * (dành cho lỗi profile thật: CHALLENGED/BLOCKED — có phạt health + ngưỡng DEAD).
 */
export async function cooldownProfile(
  db: DB,
  profileId: string,
  cooldownSeconds: number,
  reason?: string | null,
): Promise<void> {
  const r = reason ?? null;
  await sql`
    UPDATE profiles
    SET status = 'COOLDOWN',
        lease_expires_at = NULL,
        cooldown_until = now() + make_interval(secs => ${cooldownSeconds}),
        last_error = COALESCE(${r}, last_error),
        last_error_at = CASE WHEN ${r}::text IS NULL THEN last_error_at ELSE now() END
    WHERE id = ${profileId};
  `.execute(db);
}

/**
 * Cron dọn lease (spec §6.4): profile `IN_USE` quá `lease_expires_at` (worker treo, không kịp trả) →
 * `AVAILABLE`. Job không kẹt vĩnh viễn. Trả số profile đã dọn để log/metric.
 */
export async function reapExpiredLeases(db: DB): Promise<number> {
  const result = await sql<{ id: string }>`
    UPDATE profiles
    SET status = 'AVAILABLE',
        lease_expires_at = NULL
    WHERE status = 'IN_USE'
      AND lease_expires_at IS NOT NULL
      AND lease_expires_at < now()
    RETURNING id;
  `.execute(db);
  return result.rows.length;
}

/**
 * Cron dọn COOLDOWN hết hạn (spec §4.6): profile `COOLDOWN` mà `cooldown_until` đã qua → trả về `AVAILABLE`
 * để claim lại được. KHÔNG có bước này thì COOLDOWN kẹt VĨNH VIỄN (claimProfile chỉ lấy status='AVAILABLE').
 * KHÔNG reset `consecutive_fails` (giữ lịch sử lỗi → vẫn tiến tới DEAD nếu tiếp tục fail). Trả số đã dọn.
 */
export async function reapExpiredCooldowns(db: DB): Promise<number> {
  // Reap khi cooldown_until đã qua HOẶC NULL: COOLDOWN mà không có mốc hết hạn là trạng thái vô nghĩa/kẹt
  // (không gì giữ nó trong cooldown) → trả AVAILABLE để không kẹt vĩnh viễn.
  const result = await sql<{ id: string }>`
    UPDATE profiles
    SET status = 'AVAILABLE',
        cooldown_until = NULL
    WHERE status = 'COOLDOWN'
      AND (cooldown_until IS NULL OR cooldown_until < now())
    RETURNING id;
  `.execute(db);
  return result.rows.length;
}

/** Đếm profile còn khả dụng cho một platform (AVAILABLE, ngoài cooldown) — để cảnh báo pool thấp (§4.6). */
export async function countAvailable(db: DB, platform: Platform): Promise<number> {
  const row = await db
    .selectFrom('profiles')
    .select((eb) => eb.fn.countAll<string>().as('n'))
    .where('platform', '=', platform)
    .where('status', '=', ProfileStatus.AVAILABLE)
    .where((eb) =>
      eb.or([eb('cooldown_until', 'is', null), eb('cooldown_until', '<', sql<Date>`now()`)]),
    )
    .executeTakeFirst();
  return row ? Number(row.n) : 0;
}

/** Lấy profile theo id (đọc, không khoá). Dùng để lấy proxy_id khi cần xoay proxy. */
export async function getProfile(db: DB, profileId: string): Promise<Profile | undefined> {
  return db.selectFrom('profiles').selectAll().where('id', '=', profileId).executeTakeFirst();
}

/**
 * Lấy profile theo **gemlogin_profile_id** (KHOÁ pool — 1 GemLogin profile = 1 dòng, INV-6). Dùng để lấy cookie
 * đã lưu (login thành công vừa refresh theo gemlogin id) mà verify khi "Nạp tài khoản vào pool" cần inject —
 * KHÔNG lệ thuộc GemLogin có giữ session qua đóng/mở browser. Đọc, không khoá.
 */
export async function getByGemlogin(
  db: DB,
  gemloginProfileId: string,
): Promise<Profile | undefined> {
  return db
    .selectFrom('profiles')
    .selectAll()
    .where('gemlogin_profile_id', '=', gemloginProfileId)
    .executeTakeFirst();
}

/**
 * LƯU CẤU HÌNH VÂN TAY (ProfileConfig) do dashboard đặt cho profile GemLogin — UPSERT theo (station, gemlogin_id).
 * GemLogin không cho đọc lại fingerprint → server giữ config làm nguồn sự thật để form "Sửa" hiển thị đúng
 * (sync), không luôn hiện mặc định. Nếu row chưa tồn tại (create trước khi sync mirror kịp) → tạo dòng tối
 * thiểu (platform NULL, AVAILABLE) mang config; sync sau chỉ cập nhật metadata, KHÔNG chạm config_json.
 * `configJson` = chuỗi JSON.stringify(ProfileConfig). KHÔNG chứa cookie (INV-12).
 */
export async function setProfileConfigByGemlogin(
  db: DB,
  stationId: string,
  gemloginProfileId: string,
  configJson: string,
): Promise<void> {
  await db
    .insertInto('profiles')
    .values({
      gemlogin_profile_id: gemloginProfileId,
      assigned_station_id: stationId,
      status: ProfileStatus.AVAILABLE,
      config_json: configJson,
    })
    .onConflict((oc) =>
      oc
        .columns(['assigned_station_id', 'gemlogin_profile_id'])
        .where('gemlogin_profile_id', 'is not', null)
        .where('assigned_station_id', 'is not', null)
        .doUpdateSet({ config_json: configJson }),
    )
    .execute();
}

/**
 * ĐỒNG BỘ XOÁ (§3): profile của station mà `gemlogin_profile_id` KHÔNG còn trong danh sách GemLogin hiện tại
 * (`existingIds`) = đã bị XOÁ bên GemLogin → gỡ khỏi pool (DELETE — FK check_logs/check_jobs là ON DELETE SET
 * NULL nên an toàn, giữ lịch sử). Chỉ đụng profile CÓ gemlogin_profile_id (không xoá dòng chưa gán id). Trả id
 * đã gỡ để log. `existingIds` rỗng = GemLogin không còn profile nào → gỡ hết (danh sách đến từ list THÀNH CÔNG).
 */
export async function pruneDeletedProfiles(
  db: DB,
  stationId: string,
  existingIds: string[],
): Promise<string[]> {
  let q = db
    .deleteFrom('profiles')
    .where('assigned_station_id', '=', stationId)
    .where('gemlogin_profile_id', 'is not', null);
  if (existingIds.length > 0) {
    q = q.where('gemlogin_profile_id', 'not in', existingIds);
  }
  const rows = await q.returning('gemlogin_profile_id').execute();
  return rows.map((r) => r.gemlogin_profile_id).filter((x): x is string => x != null);
}

/** Liệt kê profile của một station (bề mặt điều khiển — GET /stations/:id/profiles). Đọc thuần. */
export async function listByStation(db: DB, stationId: string): Promise<Profile[]> {
  return db
    .selectFrom('profiles')
    .selectAll()
    .where('assigned_station_id', '=', stationId)
    .orderBy('platform')
    .orderBy('gemlogin_profile_id')
    .execute();
}

export interface RegisterAccountInput {
  platform: Platform;
  gemlogin_profile_id: string;
  stationId?: string | null;
  account_label?: string | null;
  proxyId?: string | null;
  // Cookie ĐÃ MÃ HOÁ (orchestrator mã hoá qua packages/crypto — INV-12). Bỏ trống nếu đăng nhập tay trong GemLogin.
  cookieCiphertext?: Buffer | null;
  cookieKeyId?: string | null;
}

/**
 * Nạp/ cập nhật một tài khoản thật vào pool (bề mặt điều khiển — POST /accounts): tạo/cập nhật dòng
 * `profiles` (AVAILABLE) để POST /check dùng được. Cookie lưu ĐÃ MÃ HOÁ (INV-12). KHÔNG log giá trị cookie.
 *
 * Dedup theo **gemlogin_profile_id** (mô hình MIRROR — INV-6: 1 GemLogin profile = 1 dòng = 1 nền tảng, đã ràng
 * buộc bởi unique index (station, gid)). "Nạp tài khoản" = GÁN nền tảng cho profile GemLogin đã mirror về: tìm
 * dòng theo id GemLogin → set platform + label + cookie. Chưa mirror (register trước sync) → tạo dòng mới. Nạp lại
 * = làm sạch để cấp phát được ngay. KHÔNG tạo nhiều dòng cùng một profile cho nhiều nền tảng (một vân tay/một
 * bộ cookie chỉ phục vụ một nền tảng — muốn nền tảng khác thì dùng profile GemLogin khác).
 */
export async function registerAccount(db: DB, input: RegisterAccountInput): Promise<Profile> {
  const existing = await db
    .selectFrom('profiles')
    .selectAll()
    .where('gemlogin_profile_id', '=', input.gemlogin_profile_id)
    .executeTakeFirst();

  if (existing) {
    const updated = await db
      .updateTable('profiles')
      .set({
        platform: input.platform,
        account_label: input.account_label ?? existing.account_label,
        assigned_station_id: input.stationId ?? existing.assigned_station_id,
        proxy_id: input.proxyId ?? existing.proxy_id,
        // Chỉ ghi đè cookie khi có cookie mới; giữ cookie cũ nếu không truyền.
        ...(input.cookieCiphertext
          ? { cookie_ciphertext: input.cookieCiphertext, cookie_key_id: input.cookieKeyId }
          : {}),
        // Nạp lại = làm sạch để pool CẤP PHÁT ĐƯỢC NGAY: AVAILABLE + xoá cooldown/lease + reset fails.
        // (Nếu chỉ set AVAILABLE mà còn cooldown_until tương lai → claimProfile vẫn bỏ qua → job kẹt PENDING.)
        status: ProfileStatus.AVAILABLE,
        cooldown_until: null,
        lease_expires_at: null,
        consecutive_fails: 0,
        last_error: null,
        last_error_at: null,
      })
      .where('id', '=', existing.id)
      .returningAll()
      .executeTakeFirstOrThrow();
    return updated;
  }

  return db
    .insertInto('profiles')
    .values({
      platform: input.platform,
      account_label: input.account_label ?? null,
      gemlogin_profile_id: input.gemlogin_profile_id,
      assigned_station_id: input.stationId ?? null,
      proxy_id: input.proxyId ?? null,
      cookie_ciphertext: input.cookieCiphertext ?? null,
      cookie_key_id: input.cookieKeyId ?? null,
      status: ProfileStatus.AVAILABLE,
    })
    .returningAll()
    .executeTakeFirstOrThrow();
}

export interface ProfileStatusCount {
  platform: Platform;
  status: ProfileStatus;
  count: number;
}

/**
 * Đếm profile theo (platform, status) — cho metric `fastcheck_profiles` + pool health dashboard.
 * CHỈ tính profile ĐÃ gán nền tảng (platform IS NOT NULL): metric này phản ánh POOL CẤP PHÁT ĐƯỢC theo platform
 * + cảnh báo pool thấp. Profile mới mirror chưa gán (platform NULL) là "kho tồn", không thuộc pool → loại khỏi metric.
 */
export async function countByStatusAll(db: DB): Promise<ProfileStatusCount[]> {
  const rows = await db
    .selectFrom('profiles')
    .select((eb) => ['platform', 'status', eb.fn.countAll<string>().as('count')])
    .where('platform', 'is not', null)
    .groupBy(['platform', 'status'])
    .execute();
  return rows.map((r) => ({
    platform: r.platform as Platform,
    status: r.status,
    count: Number(r.count),
  }));
}

export interface StationProfileInput {
  gemlogin_profile_id: string;
  // NULL = profile GemLogin chưa gán nền tảng (không nhãn note) — vẫn mirror để "Xem profile" khớp GemLogin (§3).
  platform: Platform | null;
  name?: string | null;
}

/**
 * Đồng bộ (MIRROR) danh sách profile GemLogin của một station vào bảng `profiles` (§3 station-management-design,
 * tiêu chí "danh sách profile trên máy KHỚP bảng profiles"). Mirror TOÀN BỘ profile GemLogin, kể cả profile chưa
 * gán nền tảng (platform NULL) — để "Xem profile" phản ánh đúng GemLogin. Upsert theo (assigned_station_id,
 * gemlogin_profile_id): profile mới → thêm dòng AVAILABLE; đã có → cập nhật account_label.
 *
 * KHÔNG chạm status/health/lease (tránh ghi đè job đang chạy — INV-11), KHÔNG lưu cookie (INV-12), và KHÔNG
 * ghi đè platform đã gán bằng NULL: sync chỉ set platform khi có nhãn (p.platform != null) → "Nạp tài khoản"
 * (registerAccount) gán nền tảng rồi thì vòng sync sau (note trống → null) KHÔNG xoá gán đó. Trả số profile đã upsert.
 */
export async function upsertStationProfiles(
  db: DB,
  stationId: string,
  profiles: StationProfileInput[],
): Promise<number> {
  if (profiles.length === 0) return 0;
  for (const p of profiles) {
    await db
      .insertInto('profiles')
      .values({
        platform: p.platform,
        account_label: p.name ?? null,
        gemlogin_profile_id: p.gemlogin_profile_id,
        assigned_station_id: stationId,
        status: ProfileStatus.AVAILABLE,
      })
      .onConflict((oc) =>
        oc
          .columns(['assigned_station_id', 'gemlogin_profile_id'])
          .where('gemlogin_profile_id', 'is not', null)
          .where('assigned_station_id', 'is not', null)
          // Chỉ cập nhật metadata; KHÔNG chạm status/health/lease (không cắt ngang job đang chạy).
          // platform: chỉ ghi khi có nhãn (không clobber gán thủ công bằng NULL — xem docstring).
          .doUpdateSet({
            account_label: p.name ?? null,
            ...(p.platform != null ? { platform: p.platform } : {}),
          }),
      )
      .execute();
  }
  return profiles.length;
}
