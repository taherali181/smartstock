# ADR 0001: platform boundaries

Status: accepted

Use a FastAPI modular monolith with PostgreSQL 16 as the transactional authority. Separate forecasting; run API workers as separate processes sharing domain code; keep model servers as private infrastructure. Use RabbitMQ/Celery for durable jobs, Redis only for disposable cache/quota/leases, and S3-compatible object storage for durable files.

This maximizes transaction integrity and keeps operational complexity proportional to the beta. Kubernetes, Kafka, Qdrant, and further service extraction require measured scaling or isolation evidence.
