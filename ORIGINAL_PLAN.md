# SmartStock Production Backend and AI Platform Plan

## 1. Product Definition

Build SmartStock as a cloud-first, authoritative inventory and order-management platform for US and Canadian retail/wholesale businesses.

The first complete release will include:

- Multitenant inventory, purchasing, sales orders, core WMS, returns, wholesale portal, kits/assemblies, forecasting, replenishment, accounting synchronization, and Shopify/ShipStation connectivity.
- A RAG-first conversational workspace backed by deterministic operational tools and permission-filtered document retrieval.
- AI-generated actions remain inert drafts until an authorized user reviews and approves them.
- Automatic champion-model selection for RAG and forecasting, with administrator-visible challenger comparisons and exact model/version provenance.
- An offline-capable warehouse PWA for receiving, putaway, counting, picking, packing, transfers, and barcode scanning.
- A best-effort production beta without a contractual availability or recovery SLA, while retaining backups, restore drills, performance gates, and observability.

Stitch Labs will be treated as a historical reference for centralized allocation and routing because Square sunset the product in 2021. Current benchmarks are [Zoho Inventory](https://www.zoho.com/us/inventory/features/ai-in-inventory/), [Cin7](https://www.cin7.com/features/), [Katana](https://katanamrp.com/features/manufacturing/), [Odoo Inventory](https://www.odoo.com/app/inventory-features), and [Netstock](https://www.netstock.com/solutions/inventory-optimization/).

SmartStock’s differentiation will be:

- Evidence-backed operational answers with freshness and record versions.
- Transparent model tournaments instead of opaque forecasting claims.
- Approval-gated actions with impact previews and complete audit history.
- Self-hosted, commercially permissive open-model infrastructure.
- An exception workspace prioritizing stockout risk, excess stock, late orders, forecast drift, fulfillment risk, and inventory discrepancies.

## 2. Architecture and Technology Stack

### Repository and service structure

Consolidate SmartStock into one product repository:

- `apps/web`: existing React/TypeScript application, API integration, B2B portal, and warehouse PWA.
- `apps/api`: Python modular monolith containing authoritative transactional domains.
- `services/forecasting`: separately deployable Python forecasting and model-evaluation service.

API workers share domain code with the modular monolith but run as separate processes. Inference servers remain infrastructure services behind internal APIs.

Reuse Restock algorithms, fixtures, and useful tests selectively. Do not incrementally expose its current SQLite schema or unauthenticated routes.

### Application stack

- Web: React, TypeScript, Vite, React Router, TanStack Query, generated OpenAPI client, IndexedDB, Workbox service worker.
- API: Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic.
- Database: PostgreSQL 16 with pgvector, PostgreSQL full-text search, row-level security, and point-in-time recovery.
- Durable jobs: Celery with RabbitMQ; separate queues for imports, connectors, documents, forecasts, notifications, and exports.
- Cache and quotas: Redis for caching, distributed rate limits, short-lived locks, and ephemeral state only.
- Files and artifacts: S3-compatible storage; MinIO in local development and AWS S3 in production.
- Identity: Keycloak through OIDC. SmartStock owns organizations, memberships, permissions, and warehouse grants.
- Observability: OpenTelemetry, Prometheus, Grafana, Loki, Tempo, and Langfuse OSS for AI traces. Langfuse does not replace the application audit log.
- Infrastructure: Docker Compose locally; Terraform-managed AWS reference deployment.
- API deployment: ECS Fargate behind ALB/WAF.
- Data services: RDS PostgreSQL, ElastiCache Redis, Amazon MQ RabbitMQ, S3, KMS, Secrets Manager.
- Models: private GPU EC2 autoscaling group running vLLM and Hugging Face TEI.
- Delivery: ECR and GitHub Actions with migration, security, contract, and smoke-test gates.
- Primary beta region: `us-east-1`; Canadian business localization is supported, but Canadian data-residency guarantees are not part of the initial beta.

Kubernetes, Kafka, Qdrant, and service extraction are deferred until measured workload or isolation needs justify them.

### Scale envelope

Design and load-test for:

- 1,000 organizations.
- Five million aggregate SKU-location inventory positions.
- A largest tenant of approximately 250,000 SKUs and 100 locations.
- 100 million inventory-ledger lines annually.
- 2,000 concurrent interactive users.
- Connector bursts of 5,000 events per minute.
- Ten million indexed document chunks.
- Horizontal API and worker scaling without local filesystem dependencies.

Internal beta performance objectives:

- CRUD p95 below 300 ms.
- Inventory commands p95 below 500 ms, excluding queued work.
- Indexed operational search p95 below one second.
- RAG time-to-first-token p95 below 2.5 seconds when the GPU route is healthy.

These are engineering gates, not a published beta SLA.

## 3. Authoritative Domain Model

### Tenancy and security foundation

Every business, job, vector, file, audit, and model record carries `organization_id`.

Implement:

- Organizations, users, memberships, invitations, API clients, service accounts, and warehouse/location grants.
- Roles: owner, administrator, planner, buyer, warehouse operator, salesperson, accountant, and viewer.
- Separate permissions for viewing, proposing, approving, and executing actions.
- PostgreSQL row-level security in addition to service-layer authorization.
- Composite tenant-aware foreign keys and tenant-scoped uniqueness for SKUs, warehouse codes, order numbers, external IDs, and idempotency keys.
- Transaction-local tenant/user context with connection-pool reset tests.
- Global authentication by default; only health endpoints and explicit OIDC/webhook callbacks are exempt.
- MFA-ready Keycloak policies, session revocation, verified email, recovery flows, OIDC key rotation, and service credentials.
- Immutable audit events for authentication, exports, administration, stock changes, approvals, connector repairs, and AI activity.

### Catalog

Support:

- Products, variants, categories, brands, images, custom fields, lifecycle state, archive/merge behavior, and barcode aliases.
- Decimal quantities, base UOM, purchasing/selling conversions, case packs, and reversible conversions.
- Multiple suppliers per SKU with supplier SKU, MOQ, price breaks, lead-time history, preferred supplier, currency, and ordering constraints.
- Customer-specific catalogs, price lists, tiers, tax-code mappings, and channel listings.
- Kits, bundles, light BOMs, assembly, disassembly, and component availability.
- No work centers, production routings, or full MRP in this release.

Money is stored as `NUMERIC` plus ISO currency. Quantities are decimal plus UOM. Floating-point money and integer-only quantities are prohibited.

### Inventory ledger

The source of truth is an immutable transaction header plus balanced ledger lines. Mutable stock totals are projections only.

Track inventory by:

- Organization, warehouse, zone, bin, product, lot, serial, ownership, and stock condition.
- Conditions include sellable, quarantined, damaged, expired, and in-transit.
- Lots include manufacture/expiry dates; allocation supports FEFO.
- Serial numbers are uniquely traceable within an organization.

Definitions:

- `on_hand`: accepted physical inventory currently at a location.
- `reserved`: active reservations tied to specific inventory.
- `available`: sellable on-hand minus active reservations.
- `committed`: unfulfilled confirmed sales demand.
- `incoming`: unreceived approved purchase quantities plus inbound transfers.
- `in_transit`: shipped transfer quantities not yet received.
- `backordered`: confirmed demand not currently allocatable.
- `ATP(horizon)`: available plus eligible incoming before the horizon, minus unreserved committed demand and policy safety stock.

Every mutation requires:

- Idempotency key.
- Actor and reason code.
- Business reference.
- Entity versions.
- Atomic ledger posting and outbox event.
- Negative-stock policy enforcement under database locking.
- Exact projection reconciliation after commit.

Transfers use paired source, in-transit, discrepancy, and destination-receipt lines. Counts support freeze, blind count, recount, approval, and variance posting.

Valuation supports organization-selectable weighted average or FIFO. The method becomes immutable after financial postings unless a controlled revaluation migration is performed. Landed cost can be allocated by quantity, weight, value, or manual share.

### Operational state machines

Use command endpoints for transitions; generic CRUD updates cannot change workflow state.

- Purchase order: `draft → pending_approval → approved → sent → acknowledged → partially_received → received → closed`, with controlled cancellation and supplier-return branches.
- Sales order: `quote → draft → confirmed → partially_allocated/allocated → picking → partially_shipped/shipped → delivered → closed`, with cancellation, backorder, and dropship branches.
- Transfer: `draft → approved → picking → shipped → partially_received/received → discrepancy_review → closed`.
- Cycle count: `scheduled → frozen → counting → review → approved/posted`, with recount and cancellation branches.
- Return/RMA: `requested → authorized → received → inspected → refund/replacement/credit → closed`.
- Shipment: `planned → picking → packed → labelled → shipped → delivered`, with exception and void states.
- AI proposal: `draft → validating → awaiting_review → approved/rejected/expired → executing → succeeded/failed`.

Approval revalidates permission, policy, inventory, prices, source versions, and stale data before executing the same command used by the manual UI.

### Functional modules

Implement complete workflows for:

- Purchasing: requisitions, approval limits, POs, acknowledgements, tolerances, partial/over/under receipts, inspection, putaway, landed cost, bills, credits, supplier returns, and performance metrics.
- Sales: quotes, orders, reservations, allocation, partial fulfillment, backorders, dropshipments, cancellations, invoices, RMAs, refunds, exchanges, and credit notes.
- Core WMS: warehouses, zones, bins, replenishment, receiving, putaway, transfers, pick lists, batch/wave grouping, barcode verification, packing, labels, manifests, cycle counts, and exception queues.
- Wholesale: customer accounts, price lists, net terms, credit limits, salesperson assignment, order approvals, B2B portal, invoices, Stripe checkout/payment links, and account history.
- Reporting: stock valuation, aging, sell-through, fill rate, inventory turns, dead stock, margin, order cycle time, supplier scorecards, forecast accuracy, and scheduled exports.
- Automation: rules, notifications, approvals, webhooks, saved filters, scheduled jobs, exception ownership, due dates, and escalation.

The conversational canvas remains the home surface, but bulk editing, scanning, approvals, connector repair, reconciliation, administration, and warehouse queues receive dedicated task-oriented views.

## 4. Public Interfaces and Event Contracts

### REST conventions

Expose versioned `/v1` APIs using generated OpenAPI contracts.

Standards:

- Cursor pagination for large lists.
- RFC 9457 Problem Details errors.
- `Idempotency-Key` on commands.
- `If-Match`/ETag entity concurrency.
- Correlation IDs on every request, job, event, and model trace.
- Signed direct uploads and time-limited downloads.
- Asynchronous `202 Accepted` responses for imports, indexing, exports, forecasts, and connector backfills.
- State transitions through named command resources, not arbitrary status patches.

Primary groups:

- `/v1/organizations`, `/memberships`, `/roles`, `/approval-policies`
- `/v1/products`, `/suppliers`, `/customers`, `/price-lists`
- `/v1/inventory/positions`, `/ledger`, `/reservations`, `/transfers`, `/counts`
- `/v1/purchase-orders`, `/receipts`, `/supplier-returns`
- `/v1/sales-orders`, `/allocations`, `/shipments`, `/returns`
- `/v1/warehouses`, `/bins`, `/warehouse-tasks`
- `/v1/integrations`, `/sync-runs`, `/reconciliation`
- `/v1/documents`, `/conversations`, `/action-proposals`
- `/v1/forecast-policies`, `/forecast-runs`, `/backtests`, `/scenarios`

### Conversation contract

`POST /v1/conversations/{id}/messages` accepts:

- User content.
- Attachment IDs.
- Requested data scope.
- Client message ID.
- Optional referenced records.

Stream the response through SSE using typed blocks:

- `answer_text`
- `record_summary`
- `forecast_summary`
- `recommendation`
- `citation`
- `action_proposal`
- `clarification`
- `warning`
- `error`
- `completed`

Persist:

- Exact model profile and revision.
- Prompt/tool/retriever/chunker versions.
- Tool results and authorized evidence IDs.
- Record versions and freshness timestamps.
- Citations to exact document spans or operational records.
- Fallback/validation status.
- User feedback.

### Domain events

Write events through a transactional outbox and deliver them at least once:

- `inventory.ledger_posted`
- `inventory.position_changed`
- `order.confirmed`
- `order.allocation_changed`
- `shipment.shipped`
- `purchase_order.approved`
- `receipt.posted`
- `transfer.shipped`
- `transfer.received`
- `document.indexed`
- `forecast.run_completed`
- `forecast.drift_detected`
- `model.promoted`
- `action_proposal.approved`

Consumers must be idempotent. Replay and dead-letter tooling are required.

## 5. RAG and Open-Model System

### Architecture boundary

Operational facts never come from vector memory or unconstrained model reasoning.

Flow:

1. Authenticate user and resolve tenant/warehouse policy.
2. Classify structured lookup, document QA, mixed analysis, or proposed action.
3. Invoke permission-checked typed read tools and/or hybrid document retrieval.
4. Apply tenant and ACL filters before candidates reach ranking.
5. Fuse lexical and dense results, rerank, and compose.
6. Validate structured output and citations.
7. Abstain or ask a focused clarification when evidence is weak, contradictory, stale, or unauthorized.
8. Convert requested writes into inert action proposals.
9. Reauthorize and revalidate at approval time before domain execution.

Retrieved documents are untrusted data and cannot grant permissions, alter system instructions, or invoke tools.

### Model portfolio

Use administrator-configured `ModelProfile` records. Ordinary users do not select arbitrary endpoints.

Generation:

- Default: [IBM Granite 4.1 8B](https://huggingface.co/ibm-granite/granite-4.1-8b), Apache 2.0, for fast RAG and tool calling.
- Complex reasoning challenger: [gpt-oss-20b](https://huggingface.co/openai/gpt-oss-20b), Apache 2.0.
- Optional multilingual/long-context challenger: [Qwen3-30B-A3B-Instruct](https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507), Apache 2.0.
- Optional visual-document challenger: [Mistral Small 3.1](https://huggingface.co/mistralai/Mistral-Small-3.1-24B-Instruct-2503).

Retrieval:

- Embeddings: [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), 1,024 dimensions.
- Quality challenger: Qwen3-Embedding-4B.
- Reranker: BGE reranker v2 M3.
- Hybrid retrieval: PostgreSQL lexical search plus pgvector HNSW, fused with reciprocal-rank fusion.
- Exact identifiers such as SKU, lot, serial, PO, and order number always retain lexical branches.

Document processing:

- Docling for PDF, Office, HTML, image, layout, tables, and provenance.
- Tesseract/OCRmyPDF fallback for scans.
- Structural chunking by document version, page, section, table, and bounding box.
- Antivirus quarantine, MIME/magic validation, content hashing, parser sandboxing, quotas, and deletion propagation.

Serving:

- vLLM for GPU generation.
- Hugging Face TEI for embeddings and reranking.
- llama.cpp quantized Granite as degraded CPU fallback.
- LiteLLM internal gateway for routing, health, quotas, retry policy, and equivalent-capability fallback.

Model fallback is permitted only between profiles with compatible residency, security classification, context capacity, tool schema, and successful evaluation releases. External model APIs are disabled.

Embedding changes require dual-index migration, shadow evaluation, backfill, atomic cutover, and rollback. Different embedding models are never mixed in one index.

### RAG release gates

- 100% of displayed citations resolve to an authorized source.
- At least 95% citation-supported claims on the curated evaluation set.
- 100% refusal on forbidden tenant/warehouse/document cases.
- Zero successful prompt-injection tool invocation or permission escalation.
- Exact numeric and identifier accuracy for structured operational answers.
- Valid tool/JSON schemas for all approved workflows.
- Safe abstention when evidence is missing or conflicting.
- Shadow and canary evaluation before champion promotion.

## 6. Forecasting and Replenishment

Deploy forecasting as an independent FastAPI service with Celery workers, PostgreSQL metadata, S3/Parquet snapshots, Polars/DuckDB feature pipelines, MLflow OSS registry, and Nixtla forecasting libraries.

### Required portfolio

- Baselines: Naive, SeasonalNaive, historic mean.
- Regular demand: AutoETS, AutoTheta, AutoARIMA, and MSTL.
- Intermittent/lumpy demand: Croston SBA/Optimized, TSB, ADIDA, and IMAPA through [StatsForecast](https://nixtlaverse.nixtla.io/statsforecast/src/core/models.html).
- Default global retail candidate: LightGBM with Tweedie objective and quantile variants.
- Neural challengers: N-HiTS first; TFT only where future covariates justify it.
- Open foundation challengers: [Chronos-2](https://github.com/amazon-science/chronos-forecasting) and [TimesFM 2.5](https://github.com/google-research/timesfm).
- Hierarchy reconciliation: bottom-up default, with MinTrace promoted only when backtests demonstrate improvement.
- External quantiles: p10, p50, and p90.

Do not select one model globally. Select champions at a stable cohort such as organization, demand frequency, lifecycle, ABC/XYZ class, and demand-pattern class. Use ensembles when candidates are statistically close.

### Data contract

Daily fact grain:

`organization × SKU × location × channel × local business date`

Store separately:

- Gross demand, fulfilled demand, cancellation, return, backorder, and lost-sales estimate.
- Stockout/availability censoring.
- On-hand, ATP, reservations, inbound, transfers, and adjustment history.
- Price, promotion, cost, calendar, marketing, and known-future events.
- Product/location/channel hierarchies and lifecycle metadata.
- Supplier lead-time observations, MOQ, case packs, service targets, and receipt performance.
- Point-in-time feature snapshots and known-future covariate versions.

Stockout days are censored observations, not zero demand. Returns do not silently subtract from original demand. Training folds may only use information available at their cutoff.

### Evaluation and promotion

- Expanding rolling-origin backtests using actual lead-time-plus-review horizons.
- Minimum three folds, preferably six when history permits.
- Primary metrics: WRMSSE or WAPE by hierarchy/cohort.
- Secondary metrics: MASE, bias, MAE, weighted pinball loss, p10–p90 coverage, interval width, and calibration.
- Business simulation: fill rate, stockout cost, holding cost, spoilage, expedites, and working-capital impact.
- Every candidate must beat SeasonalNaive on the weighted eligible cohort without materially degrading critical cohorts.
- Nominal p10–p90 coverage must remain within five percentage points of the intended 80% level on eligible cohorts.
- Champion promotion requires an authorized administrator, complete gates, audit reason, and rollback target.
- Nightly forecast refresh and weekly candidate retraining by default.
- GPU/foundation candidates degrade cleanly to statistical and LightGBM candidates.

RAG receives persisted forecast IDs, cutoffs, quantiles, champion versions, confidence grades, and reason codes through typed tools. It never calculates forecast values itself.

### Replenishment outputs

Generate reviewable proposals for:

- Purchase quantities.
- Transfer-versus-buy decisions.
- Safety stock and reorder points.
- Supplier selection.
- Order timing.
- Service-level scenarios.
- MOQ/case-pack rounding.
- Capacity and budget constraints.
- Excess/obsolete inventory responses.

All proposals remain drafts until approval.

## 7. Certified Integrations

### Ownership rules

- SmartStock owns inventory, allocation, purchasing, warehouse execution, and operational state.
- Shopify owns storefront content, checkout, and inbound ecommerce orders. SmartStock publishes availability and fulfillment/tracking.
- QuickBooks Online and Xero own the general ledger, reconciled accounting state, taxes, payments, and financial reporting. SmartStock sends operational invoices, bills, credits, valuation/COGS mappings, and imports accounting status.
- ShipStation owns carrier rate/label execution and carrier tracking integration. SmartStock owns shipment and fulfillment state.
- Stripe supplies B2B portal payment collection; SmartStock owns customer credit/terms/order state.

### Connector framework

Every connector implements:

- OAuth/credential vault integration.
- Source-of-truth and field-ownership matrix.
- Initial resumable backfill.
- Sync cursor and checkpointing.
- Signed webhook verification.
- Duplicate and out-of-order convergence.
- Provider rate-limit and token-expiry handling.
- SKU, warehouse, tax, currency, bundle, and status mappings.
- Channel inventory buffers and oversell policy.
- Partial-success handling.
- Reconciliation reports.
- Replay and dead-letter repair.
- Provider sandbox contract tests.

Certified beta connectors:

- Shopify
- QuickBooks Online
- Xero
- ShipStation
- Stripe
- Generic CSV and versioned REST/webhook integration

Amazon, Walmart, eBay, WooCommerce, POS, 3PL, and EDI use the same connector SDK later but are not launch-certified.

## 8. Delivery Sequence

### Phase 0 — Product and architecture lock

- Consolidate repository structure.
- Record ADRs for tenancy, ledger semantics, API conventions, queueing, model routing, integration ownership, and AWS deployment.
- Produce data dictionary, state-machine diagrams, permission matrix, threat model, event catalog, OpenAPI conventions, and test strategy.
- Freeze the legacy Restock API as reference code; create no in-place SQLite migration.

Exit gate: architecture, state machines, inventory formulas, and security boundaries are executable specifications.

### Phase 1 — Platform foundation

- PostgreSQL schema, Alembic migrations, organizations, memberships, Keycloak OIDC, roles, warehouse grants, RLS, audit, approval policies, API keys, outbox, Celery/RabbitMQ, Redis, S3 uploads, CI/CD, Terraform, observability, backups, and feature flags.
- Replace local files and FastAPI BackgroundTasks.
- Establish generated TypeScript API client and frontend authentication.

Exit gate: two-tenant adversarial tests prove isolation across API, jobs, cache, files, exports, and RLS.

### Phase 2 — Catalog and inventory truth

- Products, variants, suppliers, customers, UOMs, kits, imports, warehouses, bins, lots, serials, ledger, projections, reservations, adjustments, transfers, counts, valuation, and audit.
- Build an optional one-shot importer for demo Restock data with ID mapping and reconciliation.
- Replace frontend mock records through the generated client.

Exit gate: ledger and cost projections reconcile exactly; retries are idempotent; concurrent allocations cannot oversell.

### Phase 3 — Transactional operations and WMS

- Complete purchase-to-receive, order-to-return, transfer, warehouse task, shipment, notification, approval, exception, and reporting workflows.
- Deliver installable offline PWA with safe task synchronization and conflict handling.

Exit gate: all state transitions and interruption/retry cases pass; scanner workflows work at supported responsive breakpoints.

### Phase 4 — RAG operations layer

- Document upload and ingestion.
- Typed operational read tools.
- Hybrid retrieval, reranking, citations, streaming conversation, history, feedback, model routing, evaluation, and security defenses.
- Add version-bound action proposals and approval execution.

Exit gate: RAG quality, citation, tenant isolation, prompt injection, and action-safety gates pass.

### Phase 5 — Forecasting and replenishment

- Point-in-time demand facts.
- Statistical/intermittent/LightGBM candidates.
- Quantile forecasts, hierarchy reconciliation, backtests, MLflow registry, admin comparison, scenarios, drift, replenishment, and explanations.
- Add neural/foundation challengers only after baseline operation is stable.

Exit gate: champion models pass baseline, calibration, leakage, reproducibility, runtime, and business-simulation gates.

### Phase 6 — Integrations and wholesale

- Certify Shopify, QuickBooks Online, Xero, ShipStation, Stripe, CSV, and REST/webhooks.
- Deliver B2B portal, catalogs, price lists, terms, credit controls, approvals, invoices, and payments.
- Deliver connector reconciliation and repair workspace.

Exit gate: duplicate/out-of-order replay converges correctly and reconciliation detects every injected mismatch.

### Phase 7 — Production beta hardening

- Load and soak tests at the agreed scale envelope.
- Security review, dependency/SBOM/image scanning, penetration testing, accessibility, browser coverage, object and database restore drills, queue/model/connector outage drills, runbooks, customer support tooling, tenant export/deletion, and staged rollout.
- Roll out through internal tenants, one design partner, limited beta, then broader best-effort beta.
- No contractual SLA/RPO/RTO is published during this beta; actual telemetry informs later GA commitments.

Exit gate: no unresolved critical security findings, no reconciliation discrepancies, successful restore drill, and all domain/AI/integration acceptance gates pass.

## 9. Comprehensive Test Plan

- Domain unit tests for every calculation and state transition.
- Property-based tests for ledger conservation, UOM conversions, valuation, reservations, and retries.
- PostgreSQL concurrency tests for allocation, receiving, transfer, count, and approval races.
- RLS and authorization tests using two organizations and multiple warehouse grants.
- Alembic tests against real PostgreSQL, including clean install and rollback.
- RabbitMQ/Redis/S3 integration tests for retry, lease expiry, dead-letter, replay, and duplicate delivery.
- OpenAPI contract tests and generated-client compatibility tests.
- Shopify, QBO, Xero, ShipStation, and Stripe sandbox contract tests.
- Reconciliation tests with deliberately missing, duplicated, delayed, and out-of-order events.
- RAG golden-set, citation, ACL, prompt-injection, data-exfiltration, model-outage, and abstention tests.
- Forecast leakage, reproducibility, cold-start, intermittent demand, hierarchy, calibration, drift, and promotion tests.
- PWA offline, sync-conflict, camera/barcode, interrupted-task, responsive, accessibility, and browser tests.
- Load, soak, queue saturation, database failover, object outage, model outage, and connector degradation tests.
- Backup restore and tenant export/deletion drills.

## 10. Explicit Assumptions and Boundaries

- The existing Restock data is demo-only; there is no production-data migration or dual-write cutover.
- The selected launch segment is retail and wholesale, not a generalized ERP.
- US and Canadian workflows, USD/CAD, lots, serials, expiry, FEFO, and general-SMB controls are included.
- Native general ledger, tax filing, payroll, advanced manufacturing/MRP, full S&OP, EDI, advanced 3PL billing, autonomous AI execution, and global localization are excluded from the first complete release.
- Weighted-average and FIFO valuation are supported operationally; QBO/Xero remain accounting authorities.
- Core ledger integrity, API access, and data portability are not feature-metered. AI inference, document processing, connector throughput, and advanced planning can carry quotas.
- All production models must have pinned revisions, a license manifest, SBOM, benchmark release, hardware profile, and rollback path.
- “Free/open-source model” refers to model licensing; GPU hosting, storage, and inference operations still incur infrastructure cost.
- The application is considered functionally complete only when the Phase 7 gates pass, not when individual screens or model demos appear operational.
