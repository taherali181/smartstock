# SmartStock product roadmap

## Product principles

- **RAG first, not chat bolted on:** every module should be queryable, every answer should expose its sources, and every mutation should require the same permissions as the underlying workflow.
- **Inventory truth is ledger-based:** never infer on-hand stock from mutable totals alone. Every receipt, allocation, pick, return, transfer, and adjustment becomes an immutable stock movement.
- **Recommendations are explainable:** forecasts expose accuracy, confidence intervals, inputs, and business drivers.
- **Human approval for consequential actions:** AI may draft a PO, transfer, or adjustment; a permitted user approves it.
- **Start as a modular monolith:** preserve clean domains and events without paying the operational cost of microservices too early.

## Competitive baseline

The following capabilities are table stakes based on Zoho Inventory and the strongest ideas from Stitch Labs:

| Domain | Required capabilities |
| --- | --- |
| Catalog | Products, variants, bundles/kits, categories, images, price lists, custom fields, import/export |
| Inventory | On-hand/available/committed/incoming, adjustments, cycle counts, reorder points, stock history, valuation |
| Traceability | Lots/batches, serials, expiration dates, barcode/QR generation and scanning |
| Warehousing | Multiple locations, bins, putaway, picklists, packing, transfers, stock routing |
| Sales | Quotes, sales orders, allocation, backorders, dropshipments, invoices, returns, shipment tracking |
| Purchasing | Vendors, purchase orders, approvals, partial receipts, bills, returns, lead-time tracking |
| Channels | Shopify/marketplace/POS sync, conflict handling, channel-specific availability buffers |
| Finance | COGS, FIFO and weighted-average valuation, landed costs, tax/currency support, accounting integrations |
| Automation | Reorder alerts, rules, webhooks, custom statuses, notification policies, audit logs |
| Analytics | Stock aging, sell-through, dead stock, order cycle time, margins, fill rate, supplier scorecards |

## Delivery phases

### Phase 0 — Frontend foundation (current)

- Design system: one electric-lime accent, dark monochrome default, light mode, responsive layouts
- App shell and durable information architecture
- Dashboard, inventory, forecasting, RAG assistant, module previews
- Typed mock-data boundary so components can migrate cleanly to API queries

### Phase 1 — Inventory core

- Organization, user, role, and warehouse setup
- Product/variant catalog, supplier records, CSV import
- Immutable stock ledger and stock-position projections
- Inventory adjustments, transfers, reorder policies, cycle counts
- Sales orders, purchase orders, partial receiving, allocation
- Audit log, idempotency, optimistic concurrency, and RBAC
- REST API contract plus generated frontend client

Exit criterion: a team can receive, allocate, transfer, adjust, and audit inventory across multiple warehouses without spreadsheets.

### Phase 2 — RAG operations layer

- Connect structured records plus PDFs, SOPs, contracts, invoices, and supplier documents
- Parsing, chunking, metadata enrichment, versioning, and incremental indexing
- Hybrid retrieval (Postgres full-text + vectors), reranking, record-level permission filters
- Natural-language answers with inline citations, freshness timestamps, and deep links
- Read-only inventory/order/supplier tools for deterministic live facts
- Draft actions for replenishment and transfers with permission checks and approval gates
- Evaluation set, retrieval metrics, hallucination monitoring, feedback, and trace inspection

Exit criterion: grounded answers meet an agreed retrieval/answer quality threshold and cannot leak cross-tenant or unauthorized data.

### Phase 3 — Forecasting and optimization

- Seasonal-naive and moving-average baselines first
- Global gradient-boosting models using sales, stockouts, promotions, price, calendar, and lead time
- Intermittent-demand model track for sparse SKUs
- Rolling backtests by SKU/location; WAPE, MASE, bias, and interval coverage
- Probabilistic forecasts, anomaly detection, ABC/XYZ segmentation
- Safety stock, dynamic reorder points, EOQ constraints, and replenishment simulation
- Champion/challenger registry, drift checks, scheduled retraining, explainability

Exit criterion: recommendations beat the baseline on held-out periods and quantify both upside and stockout risk.

### Phase 4 — Fulfillment, channels, and finance

- Bins, mobile scanning, putaway, pick/pack/ship, returns
- Shopify first; then marketplaces, carriers, and POS
- Webhook ingestion with replay/dead-letter tooling and channel conflict resolution
- FIFO/weighted-average valuation, landed cost, COGS, accounting sync
- Customer portal and shipment notifications

### Phase 5 — Scale and enterprise controls

- SSO/SAML, SCIM, fine-grained policies, warehouse restrictions
- Read replicas, table partitioning, archival, regional object storage
- Tenant quotas, model budgets, retention policies, compliance exports
- Load/failure tests, recovery drills, SLO dashboards
- Optional service extraction for integration ingestion, ML training, and inference

## MVP screen map

```text
Command center
├── Inventory
│   ├── Items / variants / kits
│   ├── Stock positions and movement history
│   ├── Adjustments and cycle counts
│   └── Replenishment rules
├── Orders
│   ├── Sales orders and allocation
│   ├── Pick / pack / ship
│   └── Returns and backorders
├── Purchasing
│   ├── Purchase orders and approvals
│   ├── Receiving
│   └── Suppliers
├── Warehouses
│   ├── Locations / bins
│   └── Transfers
├── Forecasting
│   ├── Demand explorer
│   ├── Accuracy / confidence
│   └── Replenishment plans
└── Ask SmartStock
    ├── Grounded Q&A and sources
    ├── Saved conversations
    └── Approval-gated action drafts
```

## Explicit non-goals for the first backend milestone

- No microservice split
- No autonomous purchasing or stock mutation
- No custom foundation-model training
- No generalized ERP/accounting replacement
- No marketplace beyond one design-partner integration

