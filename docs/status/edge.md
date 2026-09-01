# Edge lane status

## Resolved

- **Warehouse task states rendered `In_progress`.** The label helper used
  `replace('-', ' ')`, which substitutes only the first occurrence and ignores
  underscores entirely. Replaced by `enumLabel` in `data/format.ts`, which
  replaces both separators globally and is covered by tests.
- **"Why can't I allocate SO-1004?" answered with only the order record.** A new
  `allocation_readiness` tool pairs each demand line with authorised sellable
  availability at the order's warehouse and states the exact gap, for example
  "SO-1004 is confirmed and cannot be fully allocated at WH-MAIN: SKU-1017 is
  short 38 of the 50 required (12 available)", with per-line required, on-hand,
  reserved, available and short-by, and citations to every position read. A
  plain "status of SO-1004" still returns the order row.
- **API unit suite hang, second report.** The suite pinned only some settings, so
  an exported `SMARTSTOCK_INVENTORY_BACKEND=postgres` sent unit tests through the
  PostgreSQL lifespan, which constructs external clients inside
  `TestClient.__enter__` — where the deadlock was localised. conftest now pins
  the backend, auth mode, environment and model route for the whole session, the
  proposal fixture no longer mutates environment variables or clears the settings
  cache per test, and `faulthandler_timeout = 60` makes any future hang dump
  every thread and fail rather than block. Verified with a deliberately hostile
  environment exported.

- **API unit suite hang** (raised in `docs/status/core.md`). The conversation
  route could reach the local model during tests. That call carries a long read
  timeout so a cold model still answers in production; reached from a test the
  same timeout reads as a hang rather than a failure. Fixed in three places:
  `apps/api/tests/conftest.py` forces `SMARTSTOCK_ENVIRONMENT=test` and
  `SMARTSTOCK_LLM_ROUTE=deterministic` for the whole suite; the route returns
  `None` outright in the test environment; and an autouse guard fails any unit
  test that attempts an outbound HTTP request instead of letting it stall.
  Verified with the model both stopped and running: 128 unit tests and 11
  PostgreSQL tests pass, and the suite completes in about two seconds.

## Delivered

- Conversation layer: typed permission-filtered tools, hybrid routing, SSE
  blocks, injection and argument-grounding guards, provenance.
- Version-bound action proposals with approval-time revalidation and execution
  through the ordinary domain command.
- Rootless development stack (PostgreSQL 16 + pgvector, Ollama, GNU make).
- Web: live conversation canvas, proposal review, warehouse queue at phone width.

## Responsive and accessibility gate

`node scripts/audit-ui.mjs` drives 6 routes across 6 viewports (320 scanner to
1920 desktop) and fails on horizontal overflow, controls with no accessible
name, heading-order jumps and undersized targets. Targets are scored at 44px on
touch viewports and 24px on pointer viewports, because the 44px figure is
guidance for fingertips rather than for mice.

Edge-owned routes (`/` and `/warehouse`) are **clean on all 12 combinations**.
No horizontal overflow exists on any route at any viewport.

## Requests

- `/v1/purchase-orders` returns `total` as `1100.000000000000000000`. Clients
  trim trailing zeros for display; the operations schema still emits them.
- **Operational controls are 42px tall, two pixels under the 44px touch
  minimum.** Thirteen of thirty-six viewport/route combinations fail on this
  alone: the Refresh button, warehouse/bin/condition selects, the product search
  input, New order, the purchase/sales tabs and Submit for approval. One rule in
  `ops.css` closes it. Verify with `node scripts/audit-ui.mjs`.

- ~~**Operational nav links have no accessible name below 1024px.**~~ Fixed in
  `5e69151`; the audit reports no unlabelled controls anywhere.
- ~~**Row links are 18px tall.**~~ Fixed; no longer reported.
- ~~**No endpoint lists a warehouse's bins.**~~ Delivered in `bdcdf56`;
  `GET /v1/warehouses/{id}/bins` returns the RECEIVING location, unblocking
  offline receipt-line entry.

<details><summary>original wording</summary>

- **Operational nav links have no accessible name below 1024px.** The label
  `<span>` inside each sidebar link is `display:none` under that width, leaving
  an icon-only `<a>` with no `aria-label`; `textContent` still reads
  "Inventory" but `innerText` is empty, so a screen reader announces nothing.
  Affects `/inventory`, `/products`, `/orders` and `/tasks` at every touch
  viewport. WCAG 4.1.2. An `aria-label` on the link fixes it without changing
  the visual design.
- **Row links are 18px tall** on `/products`, `/orders` and `/tasks` (SKU,
  order number, task number). That is under the 24px WCAG 2.5.8 minimum even on
  pointer viewports, and well under 44px on touch. Either pad the link to fill
  its row or make the whole row the target.
- **Blocking offline receipt-line entry:** there is no endpoint that lists a
  warehouse's bins. `POST /v1/warehouse-tasks/{id}/receipt` requires a
  `location_id` per line, and the warehouse PWA has to offer that choice while
  offline, so it needs the bins cached ahead of time. Deriving them from
  inventory positions only surfaces bins that already hold stock, which is the
  wrong set for receiving. Requesting `GET /v1/warehouses/{warehouse_id}/bins`
  returning id, code, location_type and pick_sequence.
