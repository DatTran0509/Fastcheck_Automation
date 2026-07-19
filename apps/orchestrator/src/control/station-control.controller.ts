import {
  BadRequestException,
  Body,
  Controller,
  Delete,
  Get,
  HttpCode,
  Param,
  Patch,
  Post,
  ServiceUnavailableException,
} from '@nestjs/common';
import { ApiBody, ApiOperation, ApiParam, ApiTags } from '@nestjs/swagger';
import { z } from 'zod';
import {
  browserActionRequestSchema,
  createProfileRequestSchema,
  registerAccountRequestSchema,
  runLoginRequestSchema,
  updateProfileRequestSchema,
} from '@fastcheck/contracts';
import { ControlError, StationControlService } from './station-control.service.js';

/** Parse body qua zod; ZodError → 400 (fail loud, không nuốt lỗi validate). */
function parse<T>(schema: z.ZodType<T>, body: unknown): T {
  const r = schema.safeParse(body);
  if (!r.success) throw new BadRequestException(r.error.issues);
  return r.data;
}

/**
 * BỀ MẶT ĐIỀU KHIỂN Station Management (Server → Client) qua REST — phơi ở Swagger `/docs`.
 * Đây là nơi CON NGƯỜI (operator/dashboard) vận hành: liệt kê station, CRUD profile GemLogin, mở/tắt browser,
 * gọi station chạy kịch bản login, nạp tài khoản thật vào pool. Mọi lệnh CHỜ command_ack (INV-14).
 */
@ApiTags('station-control')
@Controller()
export class StationControlController {
  constructor(private readonly control: StationControlService) {}

  private async run<T>(fn: () => Promise<T>): Promise<T> {
    try {
      return await fn();
    } catch (err) {
      if (err instanceof ControlError) throw new ServiceUnavailableException(err.message);
      throw err;
    }
  }

  @Get('stations')
  @ApiOperation({ summary: 'Danh sách station đang kết nối + trạng thái/tải/RAM/CPU' })
  listStations() {
    return this.control.listStations();
  }

  @Get('stations/:id/profiles')
  @ApiOperation({ summary: 'Danh sách profile của station (từ bảng profiles — KHÔNG cookie)' })
  @ApiParam({ name: 'id', description: 'station_id (uuid)' })
  listProfiles(@Param('id') id: string) {
    return this.control.listProfiles(id);
  }

  @Post('stations/:id/profiles')
  @ApiOperation({ summary: 'Tạo profile GemLogin trên station (forward profile.create)' })
  @ApiParam({ name: 'id', description: 'station_id (uuid)' })
  @ApiBody({
    schema: {
      type: 'object',
      required: ['platform'],
      properties: {
        platform: { type: 'string', enum: ['TIKTOK', 'FACEBOOK', 'TWITTER', 'YOUTUBE'] },
        account_label: { type: 'string' },
        proxy: { type: 'string', example: 'http://user:pass@host:port' },
      },
    },
  })
  createProfile(@Param('id') id: string, @Body() body: unknown) {
    return this.run(() => this.control.createProfile(id, parse(createProfileRequestSchema, body)));
  }

  @Patch('stations/:id/profiles/:gemId')
  @ApiOperation({ summary: 'Sửa profile GemLogin (forward profile.update)' })
  @ApiParam({ name: 'id', description: 'station_id (uuid)' })
  @ApiParam({ name: 'gemId', description: 'gemlogin_profile_id (id phía GemLogin)' })
  @ApiBody({
    schema: {
      type: 'object',
      properties: { account_label: { type: 'string' }, proxy: { type: 'string' } },
    },
  })
  updateProfile(@Param('id') id: string, @Param('gemId') gemId: string, @Body() body: unknown) {
    return this.run(() =>
      this.control.updateProfile(id, gemId, parse(updateProfileRequestSchema, body)),
    );
  }

  @Delete('stations/:id/profiles/:gemId')
  @ApiOperation({ summary: 'Xoá profile GemLogin (forward profile.delete — bản Free sẽ báo lỗi rõ ràng)' })
  @ApiParam({ name: 'id', description: 'station_id (uuid)' })
  @ApiParam({ name: 'gemId', description: 'gemlogin_profile_id' })
  deleteProfile(@Param('id') id: string, @Param('gemId') gemId: string) {
    return this.run(() => this.control.deleteProfile(id, gemId));
  }

  @Post('stations/:id/browser/open')
  @HttpCode(200)
  @ApiOperation({ summary: 'Mở browser GemLogin (inject cookie đã lưu nếu có profile_id — INV-2)' })
  @ApiParam({ name: 'id', description: 'station_id (uuid)' })
  @ApiBody({
    schema: {
      type: 'object',
      required: ['gemlogin_profile_id'],
      properties: {
        gemlogin_profile_id: { type: 'string', example: '1' },
        profile_id: { type: 'string', description: 'uuid profile nội bộ (để lấy cookie đã lưu)' },
      },
    },
  })
  openBrowser(@Param('id') id: string, @Body() body: unknown) {
    return this.run(() => this.control.openBrowser(id, parse(browserActionRequestSchema, body)));
  }

  @Post('stations/:id/browser/close')
  @HttpCode(200)
  @ApiOperation({ summary: 'Tắt browser GemLogin (forward browser.close)' })
  @ApiParam({ name: 'id', description: 'station_id (uuid)' })
  @ApiBody({
    schema: {
      type: 'object',
      required: ['gemlogin_profile_id'],
      properties: { gemlogin_profile_id: { type: 'string', example: '1' }, profile_id: { type: 'string' } },
    },
  })
  closeBrowser(@Param('id') id: string, @Body() body: unknown) {
    return this.run(() => this.control.closeBrowser(id, parse(browserActionRequestSchema, body)));
  }

  @Post('stations/:id/login')
  @HttpCode(200)
  @ApiOperation({
    summary: 'GỌI station chạy KỊCH BẢN ĐĂNG NHẬP (§7) — cookie (×4) hoặc info (TikTok/X). KHÔNG log credential.',
  })
  @ApiParam({ name: 'id', description: 'station_id (uuid)' })
  @ApiBody({
    schema: {
      type: 'object',
      required: ['gemlogin_profile_id', 'platform', 'method'],
      properties: {
        gemlogin_profile_id: { type: 'string', example: '1' },
        platform: { type: 'string', enum: ['TIKTOK', 'FACEBOOK', 'TWITTER', 'YOUTUBE'] },
        method: { type: 'string', enum: ['COOKIE', 'INFO'] },
        profile_id: { type: 'string', description: 'uuid — để dùng cookie đã lưu (method COOKIE)' },
        cookie: { type: 'string' },
        username: { type: 'string' },
        password: { type: 'string' },
        otp_secret: { type: 'string', description: 'TOTP base32 để tự sinh mã 2FA (info-login)' },
      },
    },
  })
  runLogin(@Param('id') id: string, @Body() body: unknown) {
    return this.run(() => this.control.runLogin(id, parse(runLoginRequestSchema, body)));
  }

  @Post('accounts')
  @ApiOperation({
    summary: 'Nạp TÀI KHOẢN THẬT vào pool để POST /check dùng (cookie mã hoá at-rest — INV-12)',
  })
  @ApiBody({
    schema: {
      type: 'object',
      required: ['platform', 'gemlogin_profile_id'],
      properties: {
        platform: { type: 'string', enum: ['TIKTOK', 'FACEBOOK', 'TWITTER', 'YOUTUBE'] },
        gemlogin_profile_id: { type: 'string', example: '1' },
        station_id: { type: 'string', description: 'uuid station giữ profile này' },
        account_label: { type: 'string' },
        cookie: { type: 'string', description: 'Bỏ trống nếu đã đăng nhập tay trong GemLogin' },
        proxy: { type: 'string' },
        verify: {
          type: 'boolean',
          description:
            'Mặc định true: mở profile + kiểm đã đăng nhập ĐÚNG platform trước khi nạp (chống nạp sai → cooldown ' +
            'loạn). Cần station_id + worker online, chậm ~10-20s. false = nạp thẳng không kiểm.',
        },
      },
    },
  })
  registerAccount(@Body() body: unknown) {
    return this.control.registerAccount(parse(registerAccountRequestSchema, body));
  }
}
