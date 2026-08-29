# ADR 0005: API and durable work

Status: accepted

Expose versioned REST/OpenAPI with cursor pagination, RFC 9457 errors, ETags, idempotency keys, correlation IDs, command resources, signed file transfer, and SSE for conversations. RabbitMQ/Celery owns durable asynchronous work; PostgreSQL transactional outbox bridges commits to at-least-once delivery. Redis is limited to cache, quota, short locks, and ephemeral state.
