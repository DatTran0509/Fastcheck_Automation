import { Inject, Injectable, type OnApplicationBootstrap, type OnModuleDestroy } from '@nestjs/common';
import amqp, { type AmqpConnectionManager, type ChannelWrapper } from 'amqp-connection-manager';
import type { Channel, ConsumeMessage } from 'amqplib';
import {
  EXCHANGE,
  QUEUE_DLQ,
  QUEUE_PENDING,
  QUEUE_RETRY,
  ROUTING_DLQ,
  ROUTING_PENDING,
  ROUTING_RETRY,
  checkJobMessageSchema,
} from '@fastcheck/contracts';
import type { Logger } from '@fastcheck/shared';
import type { OrchestratorEnv } from '@fastcheck/config';
import { ENV, LOGGER } from '../tokens.js';
import { DispatchService } from '../dispatch/dispatch.service.js';
import { MetricsService } from '../metrics/metrics.service.js';

// Chờ trước khi requeue khi thiếu tài nguyên (station/profile) — chống hot-loop nack. Phase 3: retry+backoff thật.
const REQUEUE_DELAY_MS = 1000;

/**
 * Consumer RabbitMQ qua amqp-connection-manager: TỰ RECONNECT (review P3).
 * `setup` chạy lại sau mỗi lần reconnect → tự khai báo lại topology + prefetch + consume.
 * Manual ack, prefetch = backpressure (INV-10). Queue chỉ vận chuyển; trạng thái ở check_jobs (INV-4).
 * Consume → DispatchService.dispatch (claim profile + gửi RUN); ack xảy ra khi có job_result.
 */
@Injectable()
export class RabbitConsumerService implements OnApplicationBootstrap, OnModuleDestroy {
  private connection?: AmqpConnectionManager;
  private channelWrapper?: ChannelWrapper;
  private queueMetricsTimer?: NodeJS.Timeout;

  constructor(
    @Inject(ENV) private readonly env: OrchestratorEnv,
    @Inject(LOGGER) private readonly logger: Logger,
    private readonly dispatch: DispatchService,
    private readonly metrics: MetricsService,
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

        // job.retry: KHÔNG có consumer. Message nằm chờ hết `expiration` (backoff) rồi được
        // dead-letter QUAY LẠI pending → retry có backoff (INV: queue chỉ vận chuyển).
        await channel.assertQueue(QUEUE_RETRY, {
          durable: true,
          deadLetterExchange: EXCHANGE,
          deadLetterRoutingKey: ROUTING_PENDING,
        });
        await channel.bindQueue(QUEUE_RETRY, EXCHANGE, ROUTING_RETRY);

        // job.dlq: job vượt max_retries — giữ lại để điều tra + alert (không tự xử lý).
        await channel.assertQueue(QUEUE_DLQ, { durable: true });
        await channel.bindQueue(QUEUE_DLQ, EXCHANGE, ROUTING_DLQ);

        await channel.prefetch(this.env.ORCHESTRATOR_PREFETCH); // backpressure (INV-10)
        await channel.consume(
          QUEUE_PENDING,
          (msg: ConsumeMessage | null) => {
            if (!msg) return;
            void this.onMessage(channel, msg);
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

    // INV-15: sau khi queue đã bind, thu hồi mọi job RUNNING mồ côi (orchestrator restart giữa chừng).
    // Chạy sau consumer.setup để message re-queue có nơi để tới; publisher tự chờ kết nối bên trong.
    const recovered = await this.dispatch.recoverOrphanRunning();
    if (recovered > 0) {
      this.logger.warn({ recovered }, 'startup sweep hoàn tất: đã re-queue job RUNNING mồ côi');
    }

    // Phơi độ sâu queue ra /metrics (backpressure — §10.4). Poll passive checkQueue, bọc lỗi.
    this.queueMetricsTimer = setInterval(
      () => void this.collectQueueDepth(),
      this.env.QUEUE_METRICS_INTERVAL_MS,
    );
  }

  private async collectQueueDepth(): Promise<void> {
    const wrapper = this.channelWrapper;
    if (!wrapper) return;
    for (const queue of [QUEUE_PENDING, QUEUE_RETRY, QUEUE_DLQ]) {
      try {
        const info = await wrapper.checkQueue(queue);
        this.metrics.setQueueDepth(queue, info.messageCount);
      } catch (err) {
        this.logger.debug({ queue, err: (err as Error).message }, 'checkQueue lỗi (bỏ qua vòng metric)');
      }
    }
  }

  private async onMessage(channel: Channel, msg: ConsumeMessage): Promise<void> {
    let job;
    try {
      job = checkJobMessageSchema.parse(JSON.parse(msg.content.toString()));
    } catch (err) {
      // Message hỏng shape → không thể xử lý, nack KHÔNG requeue (tránh lặp vô ích). Không nuốt lỗi.
      this.logger.error({ err: (err as Error).message }, 'job message sai shape; nack (drop)');
      channel.nack(msg, false, false);
      return;
    }

    try {
      // dispatch trả true = đã gửi RUN, ack sẽ xảy ra khi có job_result (INV-4).
      const dispatched = await this.dispatch.dispatch(job, { channel, msg });
      if (!dispatched) {
        // Thiếu station/profile: requeue có trễ để không dập liên tục (Phase 3: retry+backoff qua job.retry).
        setTimeout(() => {
          try {
            channel.nack(msg, false, true);
          } catch (err) {
            this.logger.warn({ err: (err as Error).message }, 'nack requeue lỗi (kênh đã đóng?)');
          }
        }, REQUEUE_DELAY_MS);
      }
    } catch (err) {
      this.logger.error(
        { trace_id: job.trace_id, err: (err as Error).message },
        'lỗi dispatch không lường trước; requeue',
      );
      channel.nack(msg, false, true);
    }
  }

  async onModuleDestroy(): Promise<void> {
    if (this.queueMetricsTimer) clearInterval(this.queueMetricsTimer);
    await this.channelWrapper?.close();
    await this.connection?.close();
  }
}
