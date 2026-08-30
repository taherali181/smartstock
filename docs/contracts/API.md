# API conventions

All public resources are under `/v1`. Large collections use opaque cursor pagination. Errors use RFC 9457 `application/problem+json`. Commands require `Idempotency-Key`; versioned resources use quoted numeric ETags and `If-Match`. Every response, job, event, and model trace carries a UUID correlation ID.

Imports, document indexing, exports, forecasts, and connector backfills return `202 Accepted` with a job resource. Uploads use signed direct-upload URLs; downloads are short-lived and audited.

`POST /v1/conversations/{id}/messages` accepts content, attachment IDs, requested scope, client message ID, and referenced records. Its SSE stream emits typed blocks: `answer_text`, `record_summary`, `forecast_summary`, `recommendation`, `citation`, `action_proposal`, `clarification`, `warning`, `error`, and `completed`.

Conversation persistence includes exact model profile/revision, prompt/tool/retriever/chunker versions, authorized evidence and tool results, record versions and freshness, exact document spans, fallback/validation status, and feedback.

## Phase 2 inventory interfaces

- Catalog commands: `/v1/products`, product variants/UOMs/suppliers/kits, `/v1/suppliers`, and `/v1/customers`.
- Location and traceability commands: `/v1/warehouses`, warehouse bins, `/v1/inventory/lots`, and `/v1/inventory/serials`.
- Inventory commands: adjustments, reservations/release, atomic transfers, and approved count postings under `/v1/inventory`.
- Inventory reads: permission-filtered positions and exact ledger/reservation reconciliation.

All Phase 2 writes require `Idempotency-Key`. Position or reservation snapshots use numeric versions and ETags. Transfer and count commands carry every affected source version because one ETag cannot represent multiple locked aggregates.

## Phase 3 operational command foundation

- Purchase orders: list/create/get under `/v1/purchase-orders`; transitions use `/commands/{command}` with an expected version.
- Purchase receipts: `POST /v1/purchase-orders/{order_id}/receipts` atomically records accepted and rejected quantities, updates PO progress, posts inventory/valuation, and generates putaway tasks. The request supplies the PO version and affected inventory-position versions.
- Sales orders: list/create/get under `/v1/sales-orders`; quote, confirmation, picking, shipment, delivery, cancellation, and closure are named commands.
- Sales allocation: `POST /v1/sales-orders/{order_id}/allocations` atomically creates sellable-stock reservations and position-specific pick work. Allocation state is derived from active reservations; it cannot be set through a status-only command.
- Shipment execution: `POST /v1/sales-orders/{order_id}/shipments` consumes exact active reservations, posts outbound inventory and valuation, and derives partial/full shipment state. Submitted quantities cannot bypass reserved demand.
- Returns: list/create/get under `/v1/returns`; named commands authorize, reject, cancel, inspect, refund, replace, credit, and close an RMA. `POST /v1/returns/{return_id}/receipt` posts every authorized line into quarantine using original shipment-cost provenance.
- Warehouse tasks: list/create under `/v1/warehouse-tasks`; assignment, start, generic completion, exception, reopen, and cancellation are named commands. Count tasks can only complete through `/v1/warehouse-tasks/{task_id}/count`, which atomically posts the frozen-version variance. Transfer source work completes through `/transfer/ship`, moves source stock to in-transit, and generates destination work; that work completes through `/transfer/receive`, posting the physical receipt and any shortage discrepancy.

Every command requires `Idempotency-Key`, emits an ETag/version, replays an identical retry, rejects a changed command using the same key, and is filtered by organization plus warehouse grants. The warehouse PWA persists each command with its original entity version and idempotency key, replays commands per task in creation order, stops later task commands behind conflicts, and requires an operator to discard stale local intent before refreshing authoritative state. Operational API responses are never stored in the service-worker HTTP cache; the permission-filtered IndexedDB task cache is the explicit offline source and is cleared before a different authenticated user/organization identity may enter the workspace.
