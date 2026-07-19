import { Inject, Injectable, type OnModuleDestroy } from '@nestjs/common';
import amqp, { type AmqpConnectionManager, type ChannelWrapper } from 'amqp-connection-manager';
import type { Channel } from 'amqplib';
import {
  EXCHANGE,
  ROUTING_DLQ,
  ROUTING_PENDING,
  ROUTING_RETRY,
  type CheckJobMessage,
} from '@fastcheck/contracts';
import type { Logger } from '@fastcheck/shared';
import type { OrchestratorEnv } from '@fastcheck/config';
import { ENV, LOGGER } from '../tokens.js';

/**
 * Kênh PUBLISH job xuống RabbitMQ, tách khỏi consumer để tránh phụ thuộc vòng (consumer → dispatch →
 * publisher). amqp-connection-manager: tự reconnect + buffer publish khi broker rớt (review P3).
 *
 * Dùng cho: re-queue backoff (job.retry), DLQ (job.dlq), và THU HỒI job khi station chết (job.pending,
 * INV-15) — nhánh thu hồi cần publish kể cả khi KHÔNG còn giữ message gốc (orchestrator vừa restart).
 * Queue chỉ vận chuyển; trạng thái ở check_jobs (INV-4).
 */
@Injectable()
export class JobPublisher implements OnModuleDestroy {
  private readonly connection: AmqpConnectionManager;
  private readonly channel: ChannelWrapper;

  constructor(
    @Inject(ENV) env: OrchestratorEnv,
    @Inject(LOGGER) private readonly logger: Logger,
  ) {
    // Khởi tạo ngay trong constructor để kênh sẵn sàng bất kể thứ tự lifecycle hook (recover startup sweep).
    this.connection = amqp.connect([env.RABBITMQ_URL]);
    this.channel = this.connection.createChannel({
      // Chỉ cần exchange để publish; consumer lo assert/bind queue. Assert idempotent nên trùng cũng an toàn.
      setup: async (ch: Channel) => {
        await ch.assertExchange(EXCHANGE, 'direct', { durable: true });
      },
    });
  }

  /** Chờ kênh publish sẵn sàng (dùng trước startup sweep để không publish vào hư không). */
  async waitReady(): Promise<void> {
    await this.channel.waitForConnect();
  }

  private async publish(
    routingKey: string,
    msg: CheckJobMessage,
    options?: { expiration?: string },
  ): Promise<void> {
    await this.channel.publish(EXCHANGE, routingKey, Buffer.from(JSON.stringify(msg)), {
      persistent: true,
      contentType: 'application/json',
      ...options,
    });
  }

  /** Đẩy job vào hàng chờ chính (dispatch lại). Dùng khi thu hồi job của station chết (INV-15). */
  publishPending(msg: CheckJobMessage): Promise<void> {
    return this.publish(ROUTING_PENDING, msg);
  }

  /** Re-queue với backoff: message chờ hết `expiration` ở job.retry rồi dead-letter về job.pending. */
  publishRetry(msg: CheckJobMessage, expirationMs: number): Promise<void> {
    return this.publish(ROUTING_RETRY, msg, { expiration: String(expirationMs) });
  }

  /** Vượt max_retries: giữ ở DLQ để điều tra + alert (không tự xử lý). */
  publishDlq(msg: CheckJobMessage): Promise<void> {
    return this.publish(ROUTING_DLQ, msg);
  }

  async onModuleDestroy(): Promise<void> {
    await this.channel.close().catch(() => undefined);
    await this.connection.close().catch(() => undefined);
    this.logger.info('JobPublisher đã đóng kênh publish');
  }
}
