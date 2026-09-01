# SmartStock demo script

Every step below has been executed against live PostgreSQL with the seeded demo
organization. Where something is not yet reachable in the browser it says so.

## Start the stack

```bash
scripts/devstack.sh start        # PostgreSQL 16 + pgvector, Ollama. No Docker, no sudo.
npm run migrate                  # first run only
npm run seed                     # idempotent
npm run dev:api                  # API on :8000
npm run dev                      # web on :5173
```

`scripts/devstack.sh status` reports both services, the schema revision and the
loaded models. `scripts/devstack.sh bootstrap` provisions everything from
nothing, including GNU make.

Development identity is used, so Keycloak is not required. The web client sends
`X-Development-User` and `X-Development-Organization`; the API accepts them only
when `SMARTSTOCK_AUTH_MODE=development` and refuses them outright in production.

## Seeded identifiers

| Thing | Values |
| --- | --- |
| Organization | SmartStock Demo Company |
| Warehouses | `WH-MAIN` Baltimore, `WH-EAST` Toronto, `WH-WEST` Reno |
| Products | `SKU-1001` … `SKU-1040` (`SKU-1001` Classic Cotton Tee, `SKU-1040` Digital Shipping Scale) |
| Purchase orders | `PO-2001` acknowledged, `PO-2002` approved |
| Sales orders | `SO-1001` quote, `SO-1002` allocated, `SO-1004` confirmed |
| Warehouse tasks | `COUNT-WH-MAIN-001`, `XFER-WH-MAIN-EAST-001`, `PICK-SO-1002-…`, `RCV-PO-2001` |

## GP-5 — ask the canvas  (verified in a browser)

Open <http://localhost:5173>. Each answer shows the route, model and latency, and
every number carries a citation with a record version and freshness stamp.

| Ask | Answer |
| --- | --- |
| `how much SKU-1001 do we have in WH-MAIN?` | 31 on hand, 31 available, cited to the position record |
| `what should I reorder?` | 5 items at or below reorder point, 462 units suggested |
| `what is running low?` | 14 positions at or below a 10 unit threshold |
| `which purchase orders are approved?` | `PO-2002`, 1140 USD, expected 2026-09-10 |
| `status of SO-1004` | confirmed, `WH-MAIN`, 2100 USD |
| `show me open warehouse tasks` | the four seeded tasks |
| `what did we receive today?` | states plainly that nothing was received |

Answers are exact or absent. `tell me a joke` abstains and lists what it can
answer instead; `how much SKU-9999 do we have?` says no such product exists
rather than inventing a number.

### Action proposals

Ask `raise a PO for 200 of SKU-1001`. A draft appears stating that nothing has
changed, priced from the recorded unit cost, naming the supplier and receiving
warehouse, and reporting how many record versions it is bound to.

**Approve and execute** creates a real purchase order and the card turns
`SUCCEEDED` with its number. Approving again is refused. A product with no
recorded cost is refused rather than priced by guess.

## GP-4 — warehouse PWA

Open <http://localhost:5173/warehouse> on a phone-width viewport.

The API contract behind it is verified: the four seeded tasks list, `start`
moves a count task to `in_progress` v2, posting a counted quantity returns 201,
and **replaying a command against a stale version returns 412
`concurrency_conflict`** — the refusal the offline queue relies on to hold a
command for review instead of applying it silently.

The offline queue, IndexedDB cache and barcode paths have unit coverage. The
full device and browser matrix is still outstanding.

## GP-1, GP-2, GP-3 — not yet reachable in the browser

The backend is verified by the golden-path integration tests: purchase order to
receipt raises on-hand, sales order to shipment lowers it, and over-allocation is
refused. The operational screens that expose these — inventory, products, orders
and tasks — are still being built, so there is no click path yet. They mount
themselves from `apps/web/src/pages/ops/routes.tsx` as they land.

## Known rough edges

- `/v1/purchase-orders` returns totals as `1100.000000000000000000`. The canvas
  trims trailing zeros; the operations schema does not yet.
- The demo database carries residue from an early integration run against it
  (`DST-…` and `SRC-…` warehouses). `smartstock_test` now exists for tests; reset
  `smartstock` and re-seed for a clean demo.
- Browser runs of the proposal flow have left a few `PO-AI-…` orders behind.
