import { Controller, Get } from '@nestjs/common';
import { StationRegistryService } from '../station-registry/station-registry.service.js';

@Controller()
export class HealthController {
  constructor(private readonly registry: StationRegistryService) {}

  /** GET /health — trạng thái + registry station (để thấy worker đã đăng ký). */
  @Get('health')
  health() {
    return { status: 'ok', stations: this.registry.list() };
  }
}
