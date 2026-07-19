import { Inject, Injectable } from '@nestjs/common';
import type { WebSocket } from 'ws';
import { StationStatus, type Logger } from '@fastcheck/shared';
import { profileRepo, stationRepo, type DB } from '@fastcheck/db';
import type { ProfileSyncMessage, StationInfo } from '@fastcheck/contracts';
import { DB_CONN, LOGGER } from '../tokens.js';

interface RegistryEntry {
  info: StationInfo;
  socket: WebSocket;
  current_load: number; // báo cáo từ heartbeat (lag ~10s)
  inflight: number; // slot đã cấp nhưng chưa có kết quả (chính xác realtime, chống cấp quá slot)
  last_ping_at: string;
  status: StationStatus;
  ram_mb: number | null; // tài nguyên máy trạm (từ heartbeat) — cho metric/dashboard
  cpu_percent: number | null;
}

export interface PickedStation {
  station_id: string;
  socket: WebSocket;
}

/**
 * Registry station realtime (in-memory) + đọc/ghi Postgres (nguồn sự thật, INV-5).
 * Đăng ký = mở station management (docs/station-management-design.md §1).
 */
@Injectable()
export class StationRegistryService {
  private readonly stations = new Map<string, RegistryEntry>();

  constructor(
    @Inject(DB_CONN) private readonly db: DB,
    @Inject(LOGGER) private readonly logger: Logger,
  ) {}

  async register(info: StationInfo, socket: WebSocket): Promise<void> {
    this.stations.set(info.station_id, {
      info,
      socket,
      current_load: 0,
      inflight: 0,
      last_ping_at: new Date().toISOString(),
      status: StationStatus.ONLINE,
      ram_mb: null,
      cpu_percent: null,
    });
    await stationRepo.upsertStationOnline(this.db, info);
    this.logger.info({ station_id: info.station_id, name: info.name }, 'station đã đăng ký (ONLINE)');
  }

  async heartbeat(
    stationId: string,
    currentLoad: number,
    ramMb?: number | null,
    cpuPercent?: number | null,
  ): Promise<void> {
    const entry = this.stations.get(stationId);
    if (entry) {
      entry.current_load = currentLoad;
      entry.last_ping_at = new Date().toISOString();
      entry.status = StationStatus.ONLINE;
      if (ramMb != null) entry.ram_mb = ramMb;
      if (cpuPercent != null) entry.cpu_percent = cpuPercent;
    }
    await stationRepo.touchHeartbeat(this.db, stationId, currentLoad);
  }

  async markOffline(stationId: string): Promise<void> {
    const entry = this.stations.get(stationId);
    if (entry) entry.status = StationStatus.OFFLINE;
    await stationRepo.setStationStatus(this.db, stationId, StationStatus.OFFLINE);
    this.logger.warn({ station_id: stationId }, 'station OFFLINE (mất heartbeat/đóng kết nối)');
    // Thu hồi + re-queue job RUNNING (INV-15) do StationMonitor/WS gateway gọi DispatchService.recoverStationJobs.
  }

  /**
   * Phát hiện station chết bằng NGƯỠNG heartbeat (INV-15): trả về id các station ONLINE quá `timeoutMs`
   * không ping, đồng thời chuyển in-memory sang OFFLINE để chỉ thu hồi MỘT lần. Caller (StationMonitor)
   * ghi OFFLINE xuống DB + gọi recover. Không dựa socket-close để bắt cả trường hợp worker treo nhưng
   * socket vẫn mở.
   */
  takeStale(timeoutMs: number): string[] {
    const now = Date.now();
    const stale: string[] = [];
    for (const entry of this.stations.values()) {
      if (entry.status !== StationStatus.ONLINE) continue;
      if (now - Date.parse(entry.last_ping_at) > timeoutMs) {
        entry.status = StationStatus.OFFLINE;
        stale.push(entry.info.station_id);
      }
    }
    return stale;
  }

  /**
   * Socket đang đóng có PHẢI socket hiện hành của station không? Chống race lúc reconnect: worker mở
   * kết nối mới + register (entry mới) TRƯỚC khi 'close' của socket cũ kịp bắn → không được đánh nhầm
   * entry mới là OFFLINE.
   */
  isActiveSocket(stationId: string, socket: WebSocket): boolean {
    return this.stations.get(stationId)?.socket === socket;
  }

  /**
   * Chọn một station ONLINE còn slot (`inflight < max_concurrency`) và RESERVE slot đó ngay
   * (INV-10: chống cấp quá năng lực máy giữa hai nhịp heartbeat). Trả `null` nếu không còn máy rảnh.
   * Nhớ gọi `releaseSlot` khi job xong hoặc dispatch thất bại.
   */
  pickAvailableStation(): PickedStation | null {
    for (const entry of this.stations.values()) {
      if (entry.status === StationStatus.ONLINE && entry.inflight < entry.info.max_concurrency) {
        entry.inflight += 1;
        return { station_id: entry.info.station_id, socket: entry.socket };
      }
    }
    return null;
  }

  releaseSlot(stationId: string): void {
    const entry = this.stations.get(stationId);
    if (entry && entry.inflight > 0) entry.inflight -= 1;
  }

  /**
   * Đồng bộ danh sách profile GemLogin của station vào bảng `profiles` (§3): Server biết profile nào ở
   * máy nào để cấp job đúng chỗ. Không đụng status/health pool, không lưu cookie (INV-11/INV-12).
   */
  async syncProfiles(msg: ProfileSyncMessage): Promise<void> {
    const n = await profileRepo.upsertStationProfiles(
      this.db,
      msg.station_id,
      msg.profiles.map((p) => ({
        gemlogin_profile_id: p.gemlogin_profile_id,
        platform: p.platform,
        name: p.name,
      })),
    );
    // ĐỒNG BỘ XOÁ: profile trong DB không còn ở GemLogin → gỡ khỏi pool (chỉ khi client gửi all_gemlogin_ids
    // — từ list THÀNH CÔNG; client cũ không gửi → bỏ qua, không prune oan).
    let pruned: string[] = [];
    if (msg.all_gemlogin_ids != null) {
      pruned = await profileRepo.pruneDeletedProfiles(this.db, msg.station_id, msg.all_gemlogin_ids);
      if (pruned.length > 0) {
        this.logger.warn(
          { station_id: msg.station_id, pruned },
          'đồng bộ XOÁ: gỡ profile đã xoá bên GemLogin khỏi pool',
        );
      }
    }
    this.logger.info(
      { station_id: msg.station_id, count: n, pruned: pruned.length },
      'đồng bộ danh sách profile GemLogin',
    );
  }

  /** Gửi một message (JSON) tới socket của station. Trả `false` nếu station không còn kết nối. */
  send(stationId: string, message: unknown): boolean {
    const entry = this.stations.get(stationId);
    if (!entry || entry.socket.readyState !== entry.socket.OPEN) return false;
    entry.socket.send(JSON.stringify(message));
    return true;
  }

  list() {
    return Array.from(this.stations.values()).map((e) => ({
      station_id: e.info.station_id,
      name: e.info.name ?? null,
      agent_version: e.info.agent_version ?? null,
      max_concurrency: e.info.max_concurrency,
      current_load: e.current_load,
      status: e.status,
      last_ping_at: e.last_ping_at,
      ram_mb: e.ram_mb,
      cpu_percent: e.cpu_percent,
    }));
  }
}
