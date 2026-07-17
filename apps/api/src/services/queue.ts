import type { ChannelWrapper } from 'amqp-connection-manager';
import { EXCHANGE, ROUTING_PENDING, type CheckJobMessage } from '@fastcheck/contracts';

/**
 * Đẩy job lên RabbitMQ (persistent). Dùng ChannelWrapper (amqp-connection-manager):
 * nếu broker đang mất kết nối, publish được buffer và gửi lại khi reconnect (resilience — review P3).
 * Queue chỉ vận chuyển — trạng thái ở check_jobs (INV-4).
 */
export async function publishJob(channel: ChannelWrapper, msg: CheckJobMessage): Promise<void> {
  await channel.publish(EXCHANGE, ROUTING_PENDING, Buffer.from(JSON.stringify(msg)), {
    persistent: true,
    contentType: 'application/json',
  });
}
