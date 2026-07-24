import { randomUUID } from 'node:crypto';
import { Inject, Injectable } from '@nestjs/common';
import type { Logger } from '@fastcheck/shared';
import type { OrchestratorEnv } from '@fastcheck/config';
import { profileRepo, type DB, type Profile } from '@fastcheck/db';
import { CookieCipher } from '@fastcheck/crypto';
import type {
  AccountResponse,
  BrowserActionRequest,
  CdpForwardRequest,
  CdpForwardResponse,
  CommandPayload,
  CommandResult,
  CreateProfileRequest,
  RegisterAccountRequest,
  RunLoginRequest,
  StationProfileView,
  StationSummary,
  UpdateProfileRequest,
} from '@fastcheck/contracts';
import { COOKIE_CIPHER, DB_CONN, ENV, LOGGER } from '../tokens.js';
import { StationRegistryService } from '../station-registry/station-registry.service.js';
import { PendingCommandsService } from './pending-commands.service.js';

/** Lỗi vận hành bề mặt điều khiển (station offline, timeout...) — map sang HTTP ở controller. */
export class ControlError extends Error {}

/**
 * Nghiệp vụ BỀ MẶT ĐIỀU KHIỂN (operator/dashboard → Station): liệt kê station/profile, CRUD profile GemLogin,
 * mở/tắt browser, GỌI station chạy kịch bản login, và nạp tài khoản thật vào pool. Mọi lệnh gửi xuống station
 * qua WS rồi CHỜ `command_ack` (INV-14). KHÔNG log cookie/credential (INV-12) — chỉ profile_id/gemlogin_id.
 */
@Injectable()
export class StationControlService {
  constructor(
    @Inject(ENV) private readonly env: OrchestratorEnv,
    @Inject(LOGGER) private readonly logger: Logger,
    @Inject(DB_CONN) private readonly db: DB,
    @Inject(COOKIE_CIPHER) private readonly cipher: CookieCipher,
    private readonly registry: StationRegistryService,
    private readonly pending: PendingCommandsService,
  ) {}

  listStations(): StationSummary[] {
    return this.registry.list();
  }

  async listProfiles(stationId: string): Promise<StationProfileView[]> {
    const rows = await profileRepo.listByStation(this.db, stationId);
    return rows.map((p) => this.toView(p));
  }

  private toView(p: Profile): StationProfileView {
    const iso = (d: Date | string | null): string | null =>
      d == null ? null : new Date(d).toISOString();
    return {
      profile_id: p.id,
      platform: p.platform,
      gemlogin_profile_id: p.gemlogin_profile_id,
      account_label: p.account_label,
      status: p.status,
      health_score: p.health_score,
      consecutive_fails: p.consecutive_fails,
      has_cookie: p.cookie_ciphertext != null,
      status_reason: p.last_error,
      status_reason_at: iso(p.last_error_at),
      cooldown_until: iso(p.cooldown_until),
      // Config vân tay đã lưu (pg trả object JSONB đã parse) — để form "Sửa" pre-fill đúng. NULL = chưa đặt.
      config: (p.config_json as StationProfileView['config']) ?? null,
    };
  }

  // ── CRUD profile GemLogin (Server → Client, §4) ────────────────────────────────
  // `config` (nếu có) mang vân tay đầy đủ 4 tab GemLogin → forward nguyên vẹn xuống client; client map nhóm
  // field API-supported sang payload GemLogin (nhóm GUI-only bỏ qua — xem profile-config.ts).
  async createProfile(stationId: string, req: CreateProfileRequest): Promise<CommandResult> {
    const res = await this.dispatch(stationId, {
      name: 'profile.create',
      platform: req.platform,
      account_label: req.account_label,
      // Proxy: ưu tiên tab Network trong config; fallback field proxy phẳng (tương thích ngược).
      proxy: req.config?.proxy ?? req.proxy,
      config: req.config,
    });
    // Lưu config làm nguồn sự thật server (GemLogin không cho đọc lại fingerprint) → form "Sửa" hiển thị đúng.
    // res.profile_id = id GemLogin mới (từ ack). Chỉ lưu khi lệnh OK + có config.
    if (res.ok && req.config && res.profile_id) {
      await this.persistConfig(stationId, res.profile_id, req.config);
    }
    return res;
  }

  async updateProfile(
    stationId: string,
    gemloginProfileId: string,
    req: UpdateProfileRequest,
  ): Promise<CommandResult> {
    const res = await this.dispatch(stationId, {
      name: 'profile.update',
      gemlogin_profile_id: gemloginProfileId,
      account_label: req.account_label,
      proxy: req.config?.proxy ?? req.proxy,
      config: req.config,
    });
    if (res.ok && req.config) {
      await this.persistConfig(stationId, gemloginProfileId, req.config);
    }
    return res;
  }

  /** Lưu ProfileConfig vào DB (JSONB) theo (station, gemlogin_id) — nguồn sự thật để form pre-fill (sync). */
  private async persistConfig(
    stationId: string,
    gemloginProfileId: string,
    config: CreateProfileRequest['config'],
  ): Promise<void> {
    try {
      await profileRepo.setProfileConfigByGemlogin(
        this.db,
        stationId,
        gemloginProfileId,
        JSON.stringify(config),
      );
    } catch (err) {
      // Không nuốt: lệnh GemLogin ĐÃ thành công; lỗi lưu config chỉ ảnh hưởng hiển thị form → log, không ném.
      this.logger.warn(
        { station_id: stationId, gemlogin_profile_id: gemloginProfileId, err: (err as Error).message },
        'lưu config profile vào DB thất bại (lệnh GemLogin vẫn OK) — form Sửa có thể hiện mặc định',
      );
    }
  }

  deleteProfile(stationId: string, gemloginProfileId: string): Promise<CommandResult> {
    return this.dispatch(stationId, {
      name: 'profile.delete',
      gemlogin_profile_id: gemloginProfileId,
    });
  }

  // ── Mở / tắt browser GemLogin ─────────────────────────────────────────────────
  async openBrowser(stationId: string, req: BrowserActionRequest): Promise<CommandResult> {
    // Nếu chỉ định profile nội bộ có cookie đã lưu → giải mã để inject TRƯỚC điều hướng (INV-2/INV-12).
    const cookie = req.profile_id ? await this.loadCookie(req.profile_id) : undefined;
    return this.dispatch(stationId, {
      name: 'browser.open',
      profile_id: req.profile_id ?? randomUUID(),
      gemlogin_profile_id: req.gemlogin_profile_id,
      cookie,
    });
  }

  closeBrowser(stationId: string, req: BrowserActionRequest): Promise<CommandResult> {
    return this.dispatch(stationId, {
      name: 'browser.close',
      profile_id: req.profile_id ?? randomUUID(),
      gemlogin_profile_id: req.gemlogin_profile_id,
    });
  }

  // ── Forward CDP điều khiển browser (§5 — station bắc cầu CDP về relay, WSS+token, INV-12) ───────
  async startCdpForward(stationId: string, req: CdpForwardRequest): Promise<CdpForwardResponse> {
    const sessionId = randomUUID();
    const res = await this.dispatch(stationId, {
      name: 'cdp.forward',
      action: 'START',
      session_id: sessionId,
      gemlogin_profile_id: req.gemlogin_profile_id,
      profile_id: req.profile_id,
    });
    // attach_path: controller (automation phía server) nối vào relay cùng session để điều khiển browser.
    // Token đi kèm qua header Authorization hoặc ?token= (INV-12) — KHÔNG nhúng token vào path trả về.
    return { ...res, session_id: sessionId, attach_path: `/cdp?role=controller&session=${sessionId}` };
  }

  stopCdpForward(stationId: string, sessionId: string): Promise<CommandResult> {
    return this.dispatch(stationId, { name: 'cdp.forward', action: 'STOP', session_id: sessionId });
  }

  // ── Server GỌI station chạy kịch bản login (§7 — kịch bản lưu phía client) ──────
  async runLogin(stationId: string, req: RunLoginRequest): Promise<CommandResult> {
    // method COOKIE: dùng cookie truyền vào, hoặc cookie đã lưu theo profile_id (giải mã — INV-12).
    let cookie = req.cookie;
    if (req.method === 'COOKIE' && !cookie && req.profile_id) {
      cookie = await this.loadCookie(req.profile_id);
    }
    return this.dispatch(stationId, {
      name: 'login.run',
      profile_id: req.profile_id ?? randomUUID(),
      gemlogin_profile_id: req.gemlogin_profile_id,
      platform: req.platform,
      method: req.method,
      cookie,
      username: req.username,
      password: req.password,
      otp_secret: req.otp_secret,
      confirm_username: req.confirm_username,
    });
  }

  // ── Nạp tài khoản thật vào pool để POST /check dùng được ───────────────────────
  async registerAccount(req: RegisterAccountRequest): Promise<AccountResponse> {
    // VERIFY từ đầu (mặc định bật): mở profile + kiểm đã đăng nhập ĐÚNG platform chưa TRƯỚC khi nạp — chống
    // nạp sai (vd profile FB nhưng chọn YOUTUBE) → sau này cooldown loạn. Cần station online. verify=false để bỏ.
    if (req.verify !== false) {
      if (!req.station_id) {
        throw new ControlError('verify cần station_id (profile nằm ở station nào) — truyền station_id hoặc verify=false');
      }
      // Cookie để verify: ưu tiên cookie DÁN TAY (req.cookie); nếu không có, dùng cookie ĐÃ LƯU theo gemlogin id
      // — chính là cookie mà "Chạy login" thành công vừa refresh về (INV-8). Inject cookie THẬT vào browser để
      // kiểm phiên, KHÔNG lệ thuộc GemLogin có giữ session qua đóng/mở (free thường KHÔNG giữ → COOKIE_DEAD oan).
      const verifyCookie = req.cookie ?? (await this.loadCookieByGemlogin(req.gemlogin_profile_id));
      const v = await this.runLogin(req.station_id, {
        gemlogin_profile_id: req.gemlogin_profile_id,
        platform: req.platform,
        method: 'COOKIE',
        cookie: verifyCookie,
      });
      if (!v.ok) {
        const hint = verifyCookie
          ? 'cookie đã lưu có thể đã hết hạn — Chạy login lại để refresh'
          : "chưa có cookie đã lưu cho profile này — bấm 'Chạy login' THÀNH CÔNG trước (để lưu cookie), rồi Nạp lại";
        throw new ControlError(
          `profile ${req.gemlogin_profile_id} CHƯA đăng nhập ${req.platform} (verify: ${v.detail}) — KHÔNG nạp vào pool. ` +
            `${hint}. (đúng profile? đúng platform? — hoặc bỏ verify: verify=false)`,
        );
      }
    }
    // Cookie mã hoá AES-GCM một-nơi-duy-nhất (packages/crypto — INV-12). Không log giá trị.
    const enc = req.cookie ? this.cipher.encrypt(req.cookie) : null;
    const profile = await profileRepo.registerAccount(this.db, {
      platform: req.platform,
      gemlogin_profile_id: req.gemlogin_profile_id,
      stationId: req.station_id ?? null,
      account_label: req.account_label ?? null,
      cookieCiphertext: enc?.ciphertext ?? null,
      cookieKeyId: enc?.keyId ?? null,
    });
    this.logger.info(
      { profile_id: profile.id, platform: profile.platform, has_cookie: enc != null },
      'nạp tài khoản vào pool (cookie mã hoá — INV-12)',
    );
    return {
      profile_id: profile.id,
      // registerAccount vừa GÁN nền tảng → luôn có (fallback req.platform cho typing, không bao giờ null ở đây).
      platform: profile.platform ?? req.platform,
      gemlogin_profile_id: profile.gemlogin_profile_id ?? req.gemlogin_profile_id,
      status: profile.status,
      has_cookie: profile.cookie_ciphertext != null,
    };
  }

  private async loadCookie(profileId: string): Promise<string | undefined> {
    const p = await profileRepo.getProfile(this.db, profileId);
    if (!p?.cookie_ciphertext || !p.cookie_key_id) return undefined;
    return this.cipher.decrypt({
      ciphertext: Buffer.from(p.cookie_ciphertext),
      keyId: p.cookie_key_id,
    });
  }

  /** Giải mã cookie đã lưu theo gemlogin_profile_id (cookie do phiên login thành công refresh về — INV-12). */
  private async loadCookieByGemlogin(gemloginProfileId: string): Promise<string | undefined> {
    const p = await profileRepo.getByGemlogin(this.db, gemloginProfileId);
    if (!p?.cookie_ciphertext || !p.cookie_key_id) return undefined;
    return this.cipher.decrypt({
      ciphertext: Buffer.from(p.cookie_ciphertext),
      keyId: p.cookie_key_id,
    });
  }

  /** Gửi một lệnh xuống station rồi CHỜ command_ack (INV-14). Ném ControlError nếu station không online. */
  private async dispatch(stationId: string, command: CommandPayload): Promise<CommandResult> {
    const commandId = randomUUID();
    const sent = this.registry.send(stationId, {
      type: 'command',
      command_id: commandId,
      command,
    });
    if (!sent) {
      throw new ControlError(`station ${stationId} không online (không gửi được lệnh ${command.name})`);
    }
    // Đăng ký chờ NGAY sau send (cùng tick, không await xen giữa) → không race với ack đến sau round-trip.
    const ack = await this.pending.waitFor(commandId, stationId, this.env.COMMAND_ACK_TIMEOUT_MS);
    this.logger.info(
      { command_id: commandId, station_id: stationId, name: command.name, ok: ack.ok },
      'lệnh điều khiển đã có phản hồi (command_ack)',
    );
    return {
      ok: ack.ok,
      command_id: commandId,
      station_id: stationId,
      detail: ack.detail ?? null,
      profile_id: ack.profile_id ?? null,
    };
  }
}
