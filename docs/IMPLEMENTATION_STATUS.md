# Implementation status

This file prevents architecture intent from being mistaken for shipped functionality.

| Area | Implemented now | Required before complete |
| --- | --- | --- |
| Web | Responsive RAG-first prototype in `apps/web`, OIDC/PKCE gate, TanStack Query, generated OpenAPI client | IndexedDB/Workbox and operational views |
| API foundation | FastAPI, OIDC with database-owned permissions/grants, security headers, Problem Details, command headers, platform/inventory repositories | environment-backed exit test and administration command surfaces beyond current reads |
| PostgreSQL | Tenant/platform/inventory/job/file/export/outbox/audit/proposal migrations, forced RLS and immutable history | execute CI integration/rollback suite and first backup/PITR drill |
| Inventory | Balanced decimal postings, PostgreSQL row lock/ledger/projection/audit/outbox transaction, idempotency, versions, negative-stock checks | real-PostgreSQL adversarial tests, reservations, transfers, counts, lots/serials, FIFO/WAC/landed cost |
| AI actions | Version-bound proposal state machine with approval revalidation | persistence, impact validation, authorization endpoints, command executor, full audit |
| Forecasting | Naive/SeasonalNaive/mean baselines, censoring, WAPE/coverage, promotion gate | point-in-time facts, rolling folds, statistical/intermittent/LightGBM portfolio, MLflow, replenishment |
| RAG | Architecture and security contracts only | ingestion, hybrid retrieval, typed tools, SSE, model routing, eval and red-team gates |
| Jobs/files | Celery queues, signed job envelopes, outbox dispatcher, Redis/S3 adapters, MinIO/RabbitMQ services and isolation tests | domain task implementations and document quarantine pipeline |
| Integrations/WMS/wholesale | Contracts and delivery scope only | complete workflows and certified connectors |
| AWS/operations | Terraform data-plane foundation, Prometheus/Grafana/Loki/Tempo, CI contract/security/integration gates, restore runbooks | environment deployment layer and load/soak/security/restore execution |

No production deployment or beta-completeness claim is permitted while any Phase 7 exit gate remains unmet.
