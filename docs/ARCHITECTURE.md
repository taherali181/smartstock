# Proposed architecture

## Recommended stack

| Layer | Choice | Reason |
| --- | --- | --- |
| Web | React, TypeScript, Vite | Fast typed UI foundation; current implementation |
| Client data | TanStack Query + generated OpenAPI client | Cache, retries, invalidation, and typed API boundaries |
| API | Node.js, Express, TypeScript | Requested stack; mature ecosystem and clean incremental path |
| Structure | Domain-oriented modular monolith | Transactional consistency now, extractable boundaries later |
| Database | PostgreSQL | Inventory transactions, tenant isolation, reporting, full-text search |
| Vector retrieval | pgvector | Keeps vector and relational filters in the same ACID system initially |
| Jobs/cache | Redis + BullMQ | Imports, webhooks, indexing, forecasts, and retriable background work |
| Files | S3-compatible object storage | Documents, product media, exports, and model artifacts |
| Model serving | vLLM or llama.cpp behind an internal inference API | Self-hosted open-weight model flexibility |
| ML service | Python + FastAPI | Forecast training/inference ecosystem; separate from transactional API |
| Observability | OpenTelemetry + metrics/log/trace backend | Request, queue, retrieval, model, and integration visibility |

## Domain boundaries

```text
React web
   │ HTTPS / SSE
   ▼
Express API ──────────────────────────────┐
   │                                     │
   ├─ Identity & tenancy                 ├─ Redis / BullMQ
   ├─ Catalog                            │    ├─ imports
   ├─ Inventory ledger                   │    ├─ integrations
   ├─ Orders & allocation                │    ├─ document indexing
   ├─ Purchasing & receiving             │    └─ forecast jobs
   ├─ Warehouses & fulfillment           │
   ├─ Reporting                          ├─ Object storage
   └─ AI orchestration                   │
          │                              │
          ├─ Retrieval ── PostgreSQL + pgvector
          ├─ LLM gateway ── vLLM / llama.cpp
          └─ Forecast client ── Python ML service
```

## RAG design

SmartStock should use two evidence paths and combine them only at orchestration time:

1. **Deterministic tools for live operational facts.** Stock, orders, suppliers, lead times, and forecasts come from permission-checked SQL/API tools. The LLM never estimates these values from embedded text.
2. **Hybrid retrieval for documents and descriptive knowledge.** Supplier terms, SOPs, invoices, notes, and policies use full-text plus dense retrieval, metadata/tenant filters, and reranking.

Initial model candidates (benchmark before locking):

- Generation: an Apache-2.0 Qwen3 size appropriate to the deployment target
- Embeddings: BGE-M3 for multilingual dense/sparse retrieval and long document chunks
- Reranking: a BGE reranker family model
- Serving: vLLM for GPU deployments; llama.cpp for compact/on-prem deployments

Every answer payload should contain `answer`, structured `citations`, `data_freshness`, `confidence`, `tool_calls`, and optional `proposed_actions`. Proposed actions are inert objects until approved through the normal domain API.

## Inventory correctness

- `stock_movements` is append-only and references the business transaction that caused it.
- Stock position is a projection: `on_hand`, `allocated`, `available`, `incoming`, `in_transit`.
- Commands carry idempotency keys; externally sourced events retain provider IDs.
- Allocation and receiving use database transactions plus row/version checks.
- Integration events use a transactional outbox; consumers are idempotent.
- All tenant-owned tables include `organization_id`; authorization is enforced in the service layer and database policies where practical.

## Forecasting approach

Begin with measurable baselines before adding complexity. Backtest per product/location and route sparse, seasonal, and high-volume series to appropriate candidates. A likely first ensemble combines seasonal-naive forecasts with LightGBM/XGBoost-style regressors using lagged demand, stockout censoring, promotions, price, calendar, and supplier lead-time features. Persist predictions with model version, training cutoff, quantiles, and input snapshot identifiers.

The forecasting service recommends; the inventory domain decides whether a proposal is valid and a user approves the resulting action.

## Scale path

1. Scale stateless API and workers horizontally.
2. Add Redis caching only for measured hot reads; database remains authoritative.
3. Partition high-volume stock movement, audit, and event tables by tenant/time.
4. Add read replicas for analytics and exports.
5. Separate ML training from online inference and apply independent autoscaling.
6. Extract a service only when a boundary has distinct scaling, ownership, or failure needs.

