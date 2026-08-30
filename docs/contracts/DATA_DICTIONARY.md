# Data dictionary

## Global conventions

All tenant-owned records use `organization_id uuid`. Aggregate identifiers are UUIDs. Times are `timestamptz` in UTC; business dates are stored separately with the location timezone used to derive them. Quantities and conversion factors use `numeric(28,9)`. Money uses `numeric(28,9)` plus a three-letter ISO 4217 currency. Versions are monotonically increasing positive integers.

Empty strings are not substitutes for `NULL`. Optional lot and serial dimensions use a zero UUID only inside projection uniqueness keys; API and ledger values remain nullable. External identifiers are unique within `(organization_id, provider, resource_type)`.

## Platform entities

| Entity | Purpose | Tenant key and invariants |
| --- | --- | --- |
| `organizations` | Business account and immutable valuation choice | globally unique slug; USD/CAD initially |
| `users` | Keycloak-linked human identity | global OIDC subject; no password storage |
| `memberships` | User access to an organization | unique organization/user; fixed launch role |
| `warehouse_grants` | Membership access to a warehouse | membership and warehouse must share tenant |
| `invitations` | Expiring membership invitation | token digest only; unique active email per tenant |
| `api_clients` | Scoped machine credential | token digest only; expiry/revocation; no human session |
| `service_accounts` | Internal workload identity | explicit permissions and credential rotation |
| `approval_policies` | Amount/action approval limits | currency-aware, versioned policy document |
| `feature_flags` | Tenant-scoped rollout override | unique tenant/key; audit every change |
| `audit_events` | Immutable security and business history | actor/correlation/resource plus before/after state |

## Inventory entities

| Entity | Purpose | Invariants |
| --- | --- | --- |
| `products` | Tenant catalog SKU | tenant-scoped SKU; decimal base UOM |
| `product_variants` | Child SKU and option attributes | tenant-scoped SKU; same-tenant parent product |
| `product_barcodes` | Scan aliases | barcode unique within tenant |
| `uom_conversions` | Reversible product-specific conversion | positive decimal factor and version |
| `suppliers` / `product_suppliers` | Multi-source purchasing rules | supplier SKU, MOQ, case pack, lead time, currency, preferred source |
| `supplier_price_breaks` | Quantity pricing | positive ordered threshold and nonnegative unit price |
| `customers` | Core customer catalog identity | tenant-scoped code and currency |
| `kits` / `kit_components` | Light BOM availability | positive decimal component quantities; direct self-cycle prohibited |
| `warehouses` | Physical or virtual warehouse | tenant-scoped code and IANA timezone |
| `locations` | Bin or control location | composite tenant/warehouse FK |
| `lots` | Lot traceability and FEFO metadata | product-scoped lot number; manufacture/expiry chronology |
| `serial_numbers` | Unit traceability | serial unique within tenant; one unit per serial position |
| `inventory_transactions` | Immutable mutation header | tenant-scoped idempotency key; actor/reason/reference |
| `inventory_ledger_lines` | Immutable balanced postings | nonzero decimal quantity; deferred transaction balance |
| `inventory_positions` | Rebuildable stock projection | unique full stock dimension; versioned under row lock |
| `reservations` | Versioned stock claim | exact position/source and active quantity reflected in projection |
| `transfers` / `transfer_lines` | Source/in-transit/destination movement | paired warehouse/location dimensions and decimal quantities |
| `cycle_counts` / `cycle_count_lines` | Blind count and approved variance | snapshot, counted, variance and actor/version provenance |
| `cost_layers` | FIFO receipt cost | ordered remaining quantity, unit cost and currency |
| `valuation_postings` | Immutable inventory financial history | pinned method, transaction, quantity and exact cost |
| `import_runs` / `import_id_mappings` | One-shot demo import provenance | source hash, stable legacy mapping and reconciliation result |
| `idempotency_records` | Stable command outcome | request SHA-256 and serialized response with expiry |

`available` is derived as sellable `on_hand - reserved`; it is not independently writable. `incoming`, `in_transit`, `committed`, `backordered`, and ATP are projections over approved operational records and policy.

## Platform delivery entities

| Entity | Purpose | Invariants |
| --- | --- | --- |
| `outbox_events` | Atomic domain-event delivery | immutable envelope; at-least-once publish |
| `consumed_events` | Consumer deduplication | unique consumer/event per tenant |
| `jobs` | Durable async operation status | queue, attempts, correlation, terminal outcome |
| `object_records` | Tenant-owned S3 object metadata | tenant-prefixed immutable storage key; content digest |
| `exports` | Audited asynchronous export | time-limited artifact; creator and filters retained |
| `action_proposals` | Inert AI/manual proposed command | exact source versions, expiry, reviewer, impact preview |

## Forecast facts

Daily grain is `organization × SKU × location × channel × local business date`. Gross demand, fulfilled demand, cancellations, returns, backorders, lost-sales estimates, and stockout censoring are separate columns. Every feature snapshot records its cutoff and known-future covariate version; a training fold may not access later information.
