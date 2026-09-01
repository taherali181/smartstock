# Edge lane status

## Resolved

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
