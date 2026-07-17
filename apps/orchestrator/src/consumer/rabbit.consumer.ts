import { Inject, Injectable, type OnApplicationBootstrap, type OnModuleDestroy } from '@nestjs/common';
import amqp, { type AmqpConnectionManager, type ChannelWrapper } from 'amqp-connection-manager';
import type { Channel, ConsumeMessage } from 'amqplib';
import { EXCHANGE, QUEUE_PENDING, ROUTING_PENDING } from '@fastcheck/contracts';
import type { Logger } from '@fastcheck/shared';
import type { OrchestratorEnv } from '@fastcheck/config';
import { ENV, LOGGER } from '../tokens.js';

/**
 * Consumer RabbitMQ (khung Phase 0) qua amqp-connection-manager: TỰ RECONNECT (review P3).
 * `setup` chạy lại sau mỗi lần reconnect → tự khai báo lại topology + prefetch + consume.
 * Manual ack, prefetch = backpressure (INV-10). Queue chỉ vận chuyển; trạng thái ở check_jobs (INV-4).
 */
@Injectable()
export class RabbitConsumerService implements OnApplicationBootstrap, OnModuleDestroy {
  private connection?: AmqpConnectionManager;
  private channelWrapper?: ChannelWrapper;

  constructor(
    @Inject(ENV) private readonly env: OrchestratorEnv,
    @Inject(LOGGER) private readonly logger: Logger,
  ) {}

  async onApplicationBootstrap(): Promise<void> {
    this.connection = amqp.connect([this.env.RABBITMQ_URL]);
    this.connection.on('connect', () => this.logger.info('RabbitMQ đã kết nối'));
    this.connection.on('disconnect', ({ err }) =>
      this.logger.warn({ err: err?.message }, 'RabbitMQ mất kết nối — sẽ tự reconnect'),
    );

    this.channelWrapper = this.connection.createChannel({
      setup: async (channel: Channel) => {
        await channel.assertExchange(EXCHANGE, 'direct', { durable: true });
        await channel.assertQueue(QUEUE_PENDING, { durable: true });
        await channel.bindQueue(QUEUE_PENDING, EXCHANGE, ROUTING_PENDING);
        await channel.prefetch(this.env.ORCHESTRATOR_PREFETCH); // backpressure (INV-10)
        await channel.consume(
          QUEUE_PENDING,
          (msg: ConsumeMessage | null) => {
            if (!msg) return;
            try {
              const payload = JSON.parse(msg.content.toString()) as {
                trace_id?: string;
                job_id?: string;
              };
              this.logger.info(
                { trace_id: payload.trace_id, job_id: payload.job_id },
                'job.pending nhận được (Phase 0: khung — chưa dispatch xuống station)',
              );
              channel.ack(msg);
            } catch (err) {
              this.logger.error({ err: (err as Error).message }, 'lỗi parse message; nack (không requeue)');
              channel.nack(msg, false, false);
            }
          },
          { noAck: false }, // manual ack (INV-4)
        );
      },
    });

    await this.channelWrapper.waitForConnect();
    this.logger.info(
      { prefetch: this.env.ORCHESTRATOR_PREFETCH },
      'RabbitMQ consumer sẵn sàng (auto-reconnect)',
    );
  }

  async onModuleDestroy(): Promise<void> {
    await this.channelWrapper?.close();
    await this.connection?.close();
  }
}
