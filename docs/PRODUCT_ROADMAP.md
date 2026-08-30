# SmartStock delivery roadmap

## Phase 0 — Product and architecture lock

Implementation status: complete.

Accepted architecture decisions cover tenancy/identity, ledger semantics, API/queue conventions, model routing, integration ownership, and AWS deployment. Executable domain state machines, inventory formulas, permission mappings, data dictionary, threat model, event catalog, OpenAPI conventions, and test strategy live under `apps/api/smartstock_api/domain`, `docs/adr`, and `docs/contracts`.

## Phase 1 — Platform foundation

Implementation status: complete. SmartStock may proceed to Phase 2.

Implemented foundations include PostgreSQL/Alembic, organizations and memberships, OIDC/PKCE, roles and warehouse grants, approval policies, API/service credential storage, forced RLS, immutable audit, feature flags, tenant-safe cache/object/export/job contracts, transactional outbox, RabbitMQ/Celery queues, Redis, S3/MinIO, Keycloak, generated TypeScript OpenAPI client, TanStack Query, observability services, CI integration/security gates, backup/restore runbooks, and Terraform-managed AWS data-plane infrastructure.

Exit criterion: satisfied on August 29, 2026. The CI quality workflow passed its two-tenant RLS and connection-pool reset tests against PostgreSQL 16 together with live RabbitMQ, Redis, and MinIO adapter checks. Production deployment remains governed by the later phase gates.

## Phase 2 — Catalog and inventory truth

Implementation status: complete.

Implemented scope includes products and variants, suppliers and price breaks, customers, reversible decimal UOM conversions, kits, warehouses/bins/zones, lots/serials, FEFO selection, reservations, valued adjustments, atomic transfers, approved count variances, weighted-average/FIFO valuation, landed-cost allocation, exact projection reconciliation, a deterministic one-shot Restock demo-import planner with stable ID mappings, and a generated-client web inventory view backed by permission-filtered live records instead of local mock data.

Exit criterion: satisfied on August 30, 2026. Quality run `33296540032` passed all six jobs. Its platform-integration job applied the Phase 2 migration to live PostgreSQL, exercised tenant-restricted catalog commands, exact ledger/reservation reconciliation, global retry replay, concurrent reservations proving inventory cannot be oversold, and migration downgrade/re-upgrade.

## Phase 3 — Transactional operations and WMS

Implementation status: in progress. The delivered slices add persisted purchase-order, sales-order, receipt, allocation, shipment, RMA, and warehouse-task aggregates with decimal lines, tenant-scoped command idempotency, optimistic versions, named transitions, warehouse grants, RLS, audit, outbox events, generated API contracts, and a live operational queue panel. Receipts post valued accepted/quarantined inventory; allocations reserve row-locked sellable positions; shipments consume exact reservations and post valued outbound inventory. RMAs validate shipped quantities, require authorization, receive at original shipment cost into quarantine, and separate inspection from refund/replacement/credit decisions. Count tasks bind a blind count to an exact stock snapshot and atomically post variance plus completion. Transfer tasks now ship source stock into an in-transit ledger account, generate destination receipt work, and resolve the full shipment into received stock plus any discrepancy. An installable Workbox PWA provides a responsive task queue, IndexedDB cache, ordered offline physical-command replay, explicit conflict review, and camera/hardware/manual barcode verification. The remaining Phase 3 work is reporting/notifications, receive/putaway/pick/pack-specific execution, transfer discrepancy resolution, and the full device/browser exit gate.

Purchase-to-receive, order-to-return, transfers, warehouse tasks, shipments, approvals, exceptions, reporting, notifications, and offline warehouse PWA. Exit on complete state/retry tests and supported scanner workflows.

## Phase 4 — RAG operations layer

Document ingestion, typed operational tools, ACL-filtered hybrid retrieval, reranking, exact citations, SSE conversations, model profiles, evaluation, prompt-injection defenses, and version-bound action proposals. Models are self-hosted; external paid model APIs remain disabled. Exit on citation, quality, isolation, abstention, and action-safety gates.

## Phase 5 — Forecasting and replenishment

Point-in-time demand facts, statistical/intermittent/LightGBM candidates, quantiles, hierarchy reconciliation, rolling backtests, MLflow registry, champion comparison/promotion, drift, scenarios, and draft replenishment actions. Neural/foundation challengers follow stable baselines.

## Phase 6 — Integrations and wholesale

Certified Shopify, QBO, Xero, ShipStation, Stripe, CSV, and REST/webhook connectors plus B2B portal, catalogs, terms, credit controls, approvals, invoices, payments, reconciliation, and repair tooling.

## Phase 7 — Production beta hardening

Scale/soak tests, security and penetration review, SBOM/image scanning, accessibility/browser coverage, database/object restore drills, outage exercises, tenant export/deletion, support tooling, and staged rollout. Beta publishes no contractual SLA/RPO/RTO.
