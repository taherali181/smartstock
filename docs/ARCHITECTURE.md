# SmartStock architecture

## System shape

SmartStock starts as a Python modular monolith for authoritative transactions, with forecasting separated because its scaling, dependencies, and failure profile differ. API workers share domain code and run as separate processes. Generation, embedding, and reranking servers are infrastructure behind internal APIs.

```text
React web / warehouse PWA
        │ HTTPS + SSE
        ▼
FastAPI modular monolith ── PostgreSQL 16 + pgvector (system of record)
        │                  ├─ S3/MinIO (files and artifacts)
        │                  ├─ Redis (cache, quota, short leases only)
        │                  └─ transactional outbox
        ├─ RabbitMQ/Celery workers (durable jobs)
        ├─ Forecasting API/workers ── MLflow + Parquet snapshots
        └─ LiteLLM gateway ── vLLM / TEI / llama.cpp fallback
```

Local infrastructure is defined in `compose.yaml`. The AWS reference target is ECS Fargate behind ALB/WAF with RDS PostgreSQL, ElastiCache, Amazon MQ, S3/KMS, Secrets Manager, and private GPU EC2 inference hosts in `us-east-1`.

## Non-negotiable boundaries

1. PostgreSQL is authoritative. Redis, vectors, search indexes, and frontend caches are disposable projections.
2. Every tenant-owned row, event, job, vector, object key, model record, and audit record carries `organization_id`.
3. Service authorization and forced PostgreSQL RLS both apply. Tenant/user settings are transaction-local and pooled connections roll back on return.
4. Workflow state changes use named command endpoints. Generic update endpoints cannot change status.
5. Inventory mutations create immutable balanced ledger lines, update projections, store idempotency outcome, write audit, and enqueue outbox events atomically.
6. Live operational facts come from typed read tools, never vector memory or model arithmetic.
7. Retrieved content is untrusted data. It cannot grant permission, modify instructions, or trigger tools.
8. AI write requests become version-bound proposals. Approval reauthorizes and revalidates before invoking the same command used by the manual UI.
9. Jobs and events are at-least-once; every consumer converges under duplicate and out-of-order delivery.
10. No process relies on a local filesystem for durable state.

## Deployable units

| Unit | Responsibility | Scaling signal |
| --- | --- | --- |
| `apps/web` target | React app, B2B portal, offline warehouse PWA | CDN traffic |
| `apps/api` | Identity context, transactional domains, SSE orchestration | request latency/concurrency |
| API workers | Imports, connectors, documents, notifications, exports | queue depth/oldest age |
| `services/forecasting` | Features, backtests, forecast runs, promotion evidence | scheduled workload/runtime |
| vLLM/TEI | Generation, embedding, reranking | GPU queue/TTFT |

The React frontend, including the conversational workspace and generated API boundary, is located in `apps/web`.

## Inventory transaction boundary

The storage migration creates composite tenant-aware keys and unique constraints. A mutation runs in one database transaction:

```text
authenticate → authorize warehouse → SET LOCAL tenant/user
  → claim tenant-scoped idempotency key
  → lock position rows → check expected versions and negative-stock policy
  → insert transaction header + balanced lines
  → update exact position/cost projections
  → append audit + outbox event → commit
```

Ledger and audit rows reject update/delete. A deferred constraint verifies each transaction balances. Reconciliation independently recomputes projections and must match exactly.

## RAG boundary

Requests are classified as structured lookup, document QA, mixed analysis, or proposed action. ACL filtering happens before retrieval candidates reach ranking. PostgreSQL full-text and pgvector results are fused and reranked; exact SKU/lot/serial/order identifiers retain lexical retrieval. Responses persist model and pipeline revisions, evidence IDs, record versions, freshness, citations, validation status, and feedback. SSE blocks follow `docs/contracts/API.md`.

## Forecast boundary

The daily fact grain is `organization × SKU × location × channel × local business date`. Gross demand, fulfillment, cancellation, return, backorder, lost-sales estimates, and censoring remain separate. Backtests are expanding rolling-origin folds evaluated at lead-time-plus-review horizons. Promotion is cohort-specific, administrator-approved, reversible, and requires a SeasonalNaive win plus calibration and business-simulation gates.

## Scale and release gates

The target envelope is 1,000 organizations, five million SKU-location positions, 100 million annual ledger lines, 2,000 interactive users, 5,000 connector events/minute, and ten million document chunks. CRUD p95 <300 ms, inventory commands p95 <500 ms, indexed search p95 <1 s, and healthy-route RAG TTFT p95 <2.5 s are internal beta gates, not a published SLA.

Production deployment remains disabled until two-tenant API/job/cache/file/export/RLS tests, PostgreSQL concurrency tests, restore drills, and all security/reconciliation gates pass.
