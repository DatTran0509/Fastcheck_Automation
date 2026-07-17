// Topology RabbitMQ — dùng chung giữa api (publish) và orchestrator (consume).
// Đổi ở đây, cả hai service khớp nhau. Queue chỉ vận chuyển (INV-4).
export const EXCHANGE = 'fastcheck.direct';

export const QUEUE_PENDING = 'job.pending';
export const QUEUE_RETRY = 'job.retry';
export const QUEUE_DLQ = 'job.dlq';

export const ROUTING_PENDING = 'job.pending';
export const ROUTING_RETRY = 'job.retry';
export const ROUTING_DLQ = 'job.dlq';
