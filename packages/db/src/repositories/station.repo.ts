import { StationStatus } from '@fastcheck/shared';
import type { DB } from '../client.js';
import type { Station } from '../types.js';

export interface RegisterStationInput {
  station_id: string;
  name: string;
  mac_address?: string | null;
  ip_address?: string | null;
  agent_version: string;
  max_concurrency: number;
}

/** Đăng ký = mở station management: upsert station, đặt ONLINE (docs/station-management-design.md §1). */
export async function upsertStationOnline(db: DB, s: RegisterStationInput): Promise<void> {
  const common = {
    name: s.name,
    mac_address: s.mac_address ?? null,
    ip_address: s.ip_address ?? null,
    agent_version: s.agent_version,
    max_concurrency: s.max_concurrency,
    status: StationStatus.ONLINE,
    last_ping_at: new Date(),
  };
  await db
    .insertInto('stations')
    .values({ id: s.station_id, current_load: 0, ...common })
    .onConflict((oc) => oc.column('id').doUpdateSet(common))
    .execute();
}

export async function touchHeartbeat(db: DB, stationId: string, currentLoad: number): Promise<void> {
  await db
    .updateTable('stations')
    .set({ last_ping_at: new Date(), current_load: currentLoad, status: StationStatus.ONLINE })
    .where('id', '=', stationId)
    .execute();
}

export async function setStationStatus(
  db: DB,
  stationId: string,
  status: StationStatus,
): Promise<void> {
  await db.updateTable('stations').set({ status }).where('id', '=', stationId).execute();
}

export async function listStations(db: DB): Promise<Station[]> {
  return db.selectFrom('stations').selectAll().orderBy('name', 'asc').execute();
}
