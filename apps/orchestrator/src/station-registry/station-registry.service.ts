import { Inject, Injectable } from '@nestjs/common';
import type { WebSocket } from 'ws';
import { StationStatus, type Logger } from '@fastcheck/shared';
import { stationRepo, type DB } from '@fastcheck/db';
import type { StationInfo } from '@fastcheck/contracts';
import { DB_CONN, LOGGER } from '../tokens.js';

interface RegistryEntry {
  info: StationInfo;
  socket: WebSocket;
  current_load: number;
  last_ping_at: string;
  status: StationStatus;
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
      last_ping_at: new Date().toISOString(),
      status: StationStatus.ONLINE,
    });
    await stationRepo.upsertStationOnline(this.db, info);
    this.logger.info({ station_id: info.station_id, name: info.name }, 'station đã đăng ký (ONLINE)');
  }

  async heartbeat(stationId: string, currentLoad: number): Promise<void> {
    const entry = this.stations.get(stationId);
    if (entry) {
      entry.current_load = currentLoad;
      entry.last_ping_at = new Date().toISOString();
      entry.status = StationStatus.ONLINE;
    }
    await stationRepo.touchHeartbeat(this.db, stationId, currentLoad);
  }

  async markOffline(stationId: string): Promise<void> {
    const entry = this.stations.get(stationId);
    if (entry) entry.status = StationStatus.OFFLINE;
    await stationRepo.setStationStatus(this.db, stationId, StationStatus.OFFLINE);
    this.logger.warn({ station_id: stationId }, 'station OFFLINE (mất heartbeat/đóng kết nối)');
    // INV-15 (Phase 4): thu hồi + re-queue job RUNNING của station này. Phase 0 chỉ đánh OFFLINE.
  }

  list() {
    return Array.from(this.stations.values()).map((e) => ({
      station_id: e.info.station_id,
      name: e.info.name,
      agent_version: e.info.agent_version,
      max_concurrency: e.info.max_concurrency,
      current_load: e.current_load,
      status: e.status,
      last_ping_at: e.last_ping_at,
    }));
  }
}
