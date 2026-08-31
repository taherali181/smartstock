# SmartStock execution plan — working core today, full product after

Two agents, one repository. Part 1 gets the main features genuinely working today.
Part 2 is the ordered path from there to the complete product in `ORIGINAL_PLAN.md`.

Ownership boundaries and the merge/collision protocol are unchanged and live in
`PARALLEL_PLAN.md` sections 2, 4 and 5. This file replaces only its *schedule*.

---

## 0. Verified starting state

Checked directly on this machine at 16:45 EDT, 2026-08-31 — not assumed:

| Fact | State |
| --- | --- |
| API boots and serves live requests | **Yes.** `POST /v1/products` returned `201` with a real record |
| Backend depth | ~12,850 lines; catalog, ledger, PO/SO/receipt/allocation/shipment/RMA/tasks/transfers/counts all implemented |
| Migrations | `0001`–`0010`, single head, downgrade tested in CI |
| Frontend | **2 pages, 980 lines total.** This is the real gap |
| Conversational canvas | Prototype shell only. No `/v1/conversations` endpoint exists |
| Docker | **Not installed.** Compose stack cannot start |
| Python runtime deps | `psycopg`, `celery`, `redis`, `boto3`, `kombu` **missing locally** |
| Ollama / GPU | Neither present. 6 cores, 18 GB RAM, 80 GB free |
| Seed data | **None.** Every list endpoint returns `[]` |

The backend is far more complete than the product looks. Today is mostly about making
existing capability reachable — seed it, surface it, and put a working canvas over it.

---

# PART 1 — TODAY

## 1. Definition of done: five golden paths

Today succeeds if a person can do all five in a browser against live PostgreSQL.
Nothing else counts as done, and no path is "done" while it only passes in a test.

**GP-1 — See the business.** Open the app, land on a seeded organization, browse products,
inventory positions by warehouse/bin, suppliers, customers, and open orders. Real records
from PostgreSQL through the generated client. No mock data anywhere.

**GP-2 — Purchase to stock.** Create a purchase order, approve it, receive it against its
warehouse task with a partial quantity, and watch on-hand rise. The ledger shows the posting,
a follow-up receiving task appears for the remainder, and a putaway task is created.

**GP-3 — Order to shipment.** Create a sales order, confirm it, allocate it against real
sellable stock, ship it. On-hand falls, the reservation is consumed, available recomputes.
Attempting to over-allocate is refused, visibly, with a Problem Details message.

**GP-4 — Warehouse execution.** Open `/warehouse` on a phone-width viewport, see the seeded
task queue, complete a blind count and a receive, and post a variance. Go offline mid-task,
queue a transition, come back online, watch it replay in order.

**GP-5 — Ask the canvas.** Type a real operational question and get a streamed, evidence-backed
answer with citations to actual records and freshness timestamps:
- "how much SKU-1017 do we have in WH-MAIN?"
- "what is below reorder point?"
- "why can't I allocate sales order SO-1004?"
- "what did we receive today?"
Then ask it to *do* something — "raise a PO for 200 of SKU-1017 from Acme" — and get an inert
draft proposal with an impact preview, which executes the real command only after approval.

## 2. Environment bring-up — start this first, it downloads in the background

`sudo` needs a password here, so **you run these**; paste each with a leading `!` so the output
lands in the session. Everything in block A and B downloads while the agents write code.

**Block A — containers** (~10 min, mostly image pulls)

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
sudo docker compose up -d postgres redis rabbitmq minio minio-init
```

Keycloak, Prometheus, Grafana, Loki and Tempo are **deliberately not started today**. Keycloak
alone costs ~1 GB of the 11 GB available and an hour of realm wiring. We run
`auth_mode=development` against `inventory_backend=postgres`, which still exercises real
schemas, real transactions, real row-level security and real tenant context — it only skips
token issuance. OIDC login is a Day-2 item, and the code path already exists and passes in CI.

**Block B — model** (~15-25 min, 2 GB + 5 GB pulls)

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull granite3.1-moe:3b     # champion today: MoE, tool-calling, fast on 6 CPU cores
ollama pull granite3.1-dense:8b   # challenger: better reasoning, ~3-6 tok/s on CPU
```

Two models is not indulgence — it makes the `ModelProfile` champion/challenger machinery from
`ORIGINAL_PLAN.md` §5 real on day one instead of theoretical. The 3B answers the demo; the 8B
proves routing, health checks and fallback work.

**Block C — Python deps** (~2 min, no sudo, an agent can run it)

```bash
pip install -e "apps/api[test]"
```

Required. Without `psycopg`, `redis`, `boto3` and `kombu` the PostgreSQL lifespan cannot start.

## 3. Block schedule

Blocks are dependency-ordered, not clock-bound. Both lanes start at T0; nobody waits on
downloads because the first block is contract work that needs no running infrastructure.

### Block 0 — T0 to T+45m — foundations, zero infrastructure needed

| Codex (`core`) | Claude (`edge`) |
| --- | --- |
| `domain/ports.py` — Protocols only, the read + command surface (`PARALLEL_PLAN.md` §4, Seam 1). **First commit, nothing else before it** — the entire canvas lane is blocked on it | `make dev` runner booting API + Vite together; web dev-auth mode sending `X-Development-*` headers so no Keycloak is needed |
| `smartstock_api/seed.py` — a deterministic, idempotent demo organization: 3 warehouses with zones/bins, ~40 products incl. lot- and serial-tracked, 6 suppliers with price breaks and lead times, 8 customers, opening stock, 2 open POs, 3 sales orders in different states, and a live task queue | `App.tsx` → lane-split route registry so both lanes append pages without conflict |
| Run Block C, then `alembic upgrade head` the moment Postgres is up | `conversations/` skeleton: SSE block types, tool registry, tool JSON schemas over `ports.py`, tested against fakes in `tests/fakes/` |

### Block 1 — T+45m to T+2h30 — live data and a live canvas

| Codex (`core`) | Claude (`edge`) |
| --- | --- |
| Seed runs green against live PostgreSQL. This unblocks every screen | `POST /v1/conversations` + SSE `/messages` emitting the typed blocks: `answer_text`, `record_summary`, `citation`, `action_proposal`, `clarification`, `warning`, `error`, `completed` |
| Read surface the UI and canvas need and that does not exist yet: `/v1/reports/stock-summary`, `/v1/reports/reorder-suggestions`, `/v1/reports/receipts-today`, product text search, position filters by warehouse/bin/condition | Ollama adapter behind `SMARTSTOCK_LLM_ROUTE` (`ollama` \| `deterministic`), with the deterministic tool router as the **mandatory** fallback — the plan's abstention path, not a stopgap |
| Fix what the smoke test exposed: required-field ergonomics (`POST /v1/warehouses` currently 422s without `timezone`) and any 500 on the golden paths | Injection guard + citation validator: retrieved/user text can never invoke a tool or widen scope; every displayed number carries a record ID and freshness stamp |
| Golden-path integration test: PO→receive→on-hand up; SO→allocate→ship→on-hand down; over-allocation refused | Wire `RagWorkspace.tsx` to the live SSE stream, replacing prototype behaviour |

### Block 2 — T+2h30 to T+4h30 — the screens

Codex's backend is done by now, so it absorbs frontend volume. The two lanes write **different
files**, so this parallelises cleanly.

| Codex (`core`) — `apps/web/src/pages/ops/*` | Claude (`edge`) — shell, canvas, PWA |
| --- | --- |
| `InventoryPage` — positions by warehouse/bin/condition, on-hand/reserved/available/incoming | App shell, navigation, org header, error and empty states |
| `ProductsPage` — list, search, detail with suppliers, UOM, kit components | Canvas polish: streaming, citation chips, source panel, scope selector |
| `OrdersPage` — PO and SO lists, detail, command buttons wired to the command endpoints | Action proposals: impact preview, approve/reject, execution result |
| `TasksPage` — warehouse task queue with state and assignment | `WarehouseWorkspace` verified against live seeded tasks; offline replay proven on a throttled device profile |

Self-contained components with a fixed prop contract. Claude mounts them in the route registry.

### Block 3 — T+4h30 to end — integration and proof

Both lanes merge to `main`, run `make check`, then walk GP-1 through GP-5 **in a browser,
together, out loud**. Every failure is fixed or explicitly logged as descoped. Finish by writing
`docs/DEMO.md`: the exact click path, seeded IDs and the four canvas questions that work.

## 4. Descope ladder — agreed now, not argued at 22:00

Cut strictly in this order. Anything above the line still ships:

1. `granite3.1-dense:8b` challenger — keep the 3B champion only
2. Ollama entirely — `SMARTSTOCK_LLM_ROUTE=deterministic` still answers GP-5 with exact numbers
3. GP-5's action-proposal half — keep question answering
4. PWA offline replay (GP-4) — keep online task execution
5. GP-3's ship step — keep create → confirm → allocate

**Never cut:** GP-1 and GP-2. Seeded data visible in a real UI, and one workflow that provably
moves stock, is the minimum that makes this an application rather than a repository.

## 5. Today's risks

| Risk | Mitigation |
| --- | --- |
| CPU-only 8B is too slow to demo | 3B MoE is the champion; 8B is the challenger, and route falls back automatically |
| 11 GB RAM across Postgres, RabbitMQ, MinIO and a 5 GB model | Keycloak and the four observability services stay down today |
| Seed and screens race — screens have nothing to render | Seed is Codex's second commit and lands before Block 2 |
| Docker group membership needs a re-login | Use `sudo docker compose` today; fix the group tomorrow |
| Frontend volume exceeds one lane | Block 2 moves Codex onto `pages/ops/*` once its backend work closes |

---

# PART 2 — COMPLETION ROADMAP

Everything after today, in dependency order. Owners follow `PARALLEL_PLAN.md` §1: Codex takes
deterministic transactional and numerical work, Claude takes greenfield, adversarial and client
work. Durations assume both lanes running.

## Day 2 — make today's demo real infrastructure

**Codex:** Keycloak realm up, OIDC login working end to end, dev-auth mode disabled outside
development, `alembic downgrade 0002 && upgrade head` verified locally, observability stack up.
**Claude:** replace web dev headers with the real OIDC/PKCE flow, session revocation, permission-
aware UI hiding, and the two-tenant adversarial isolation walkthrough in a browser.

## Week 1 — close Phase 3

**Codex:** putaway/pick/pack execution commands, wave and batch grouping, shipment
`packed → labelled` with cartons/labels/manifests, transfer discrepancy resolution, replenishment
task generation, exception queues with ownership/due dates/escalation, the full reporting set
(valuation, aging, sell-through, fill rate, turns, dead stock, margin, cycle time, supplier
scorecards), scheduled exports, and the notification/automation rules engine on the outbox.
**Claude:** offline receipt-line entry, putaway/pick/pack screens, discrepancy review UI,
barcode/camera hardening, the responsive/scanner/accessibility matrix, and reporting views.

**Gate:** every state transition and interruption/retry case passes; scanner workflows work at
supported breakpoints. Both lanes sign off in one commit.

## Weeks 2-3 — Phase 4, the real RAG layer

Today's canvas answers from typed tools. This is where documents arrive.

**Claude:** signed upload, MIME/magic validation, antivirus quarantine, content hashing, Docling
parse with OCR fallback, structural chunking with page/section/table/bbox provenance, quotas and
deletion propagation; pgvector HNSW plus PostgreSQL FTS with RRF fusion and BGE reranking; ACL
filters applied *before* ranking; mandatory lexical branches for SKU/lot/serial/PO/order;
conversation history, feedback, and the full evaluation and red-team suites.
**Codex:** the read-tool implementations behind `ports.py` at production performance, plus the
indexed-search latency gate.

**Gate:** 100% citation resolution, ≥95% citation-supported claims, 100% refusal on forbidden
tenant/warehouse/document cases, zero successful injection or escalation, exact numeric accuracy,
safe abstention, shadow and canary before any champion promotion.

## Weeks 4-6 — Phase 5, forecasting and replenishment

**Codex, in `services/forecasting`** (226 lines today, so effectively greenfield): point-in-time
demand facts at `organization × SKU × location × channel × local date` with stockout censoring and
returns held separate; the portfolio — Naive/SeasonalNaive/mean, AutoETS/AutoTheta/AutoARIMA/MSTL,
Croston SBA and Optimized, TSB, ADIDA, IMAPA, LightGBM Tweedie and quantile variants; rolling-origin
backtests at lead-time-plus-review horizons with ≥3 folds; WRMSSE/WAPE primary, MASE/bias/MAE/
pinball/coverage/calibration secondary; business simulation; cohort-level champion selection;
MLflow registry; promotion gates with authorized approver, audit reason and rollback target; drift
detection; nightly refresh and weekly retrain. Replenishment proposals — quantities, transfer-vs-buy,
safety stock, reorder points, supplier selection, timing, MOQ and case-pack rounding, budget
constraints — emitted as **drafts** through `ports.py`, never executed directly.
**Claude:** admin champion/challenger comparison, scenario UI, forecast explanation surfaces, and
the replenishment approval workspace. Neural and foundation challengers (N-HiTS, TFT, Chronos-2,
TimesFM) only after the statistical baseline is stable.

**Gate:** every candidate beats SeasonalNaive on the weighted eligible cohort without degrading
critical cohorts; p10-p90 coverage within five points of 80%; leakage, reproducibility and runtime
gates pass.

## Weeks 7-9 — Phase 6, integrations and wholesale

Split at the SDK seam. **Codex** builds the connector runtime: credential vault, resumable backfill,
sync cursors and checkpointing, duplicate and out-of-order convergence, rate-limit and token-expiry
handling, partial success, dead-letter, replay, reconciliation reports and the repair engine.
**Claude** builds the adapters on top: Shopify, QuickBooks Online, Xero, ShipStation, Stripe,
generic CSV and versioned REST/webhook — OAuth, signed webhook verification, field-ownership
matrices, SKU/warehouse/tax/currency/bundle/status mappings, channel buffers and oversell policy,
sandbox contract tests — plus the B2B portal, price lists, terms, credit controls, invoices and
payments. Connectors write **only** through published command ports.

**Gate:** duplicate and out-of-order replay converges correctly; reconciliation detects every
injected mismatch.

## Weeks 10-12 — Phase 7, production beta hardening

**Codex:** load and soak at the stated scale envelope (1,000 orgs, 5M positions, 100M ledger lines
per year, 2,000 concurrent users, 5,000 connector events per minute), queue saturation, database
failover, object and model outage, connector degradation, backup restore and PITR drills, tenant
export and deletion.
**Claude:** security review, dependency/SBOM/image scanning, penetration testing, accessibility and
browser coverage, support tooling, staged rollout through internal tenants → one design partner →
limited beta → broader best-effort beta.

**Gate:** no unresolved critical security findings, no reconciliation discrepancies, a successful
restore drill, and every domain, AI and integration gate green.

## Standing rules, unchanged by the deadline

1. Every mutation keeps its idempotency key, actor, reason code, business reference, entity
   versions, atomic ledger posting plus outbox event, and exact projection reconciliation.
   Today's deadline does not license relaxing this.
2. Retrieved documents and user text are untrusted in every code path. They cannot grant
   permissions, alter instructions, or invoke tools.
3. AI output is inert until an authorized human approves it, and approval revalidates permission,
   policy, inventory, prices, source versions and staleness before running the same command the
   manual UI runs.
4. Money is `NUMERIC` plus ISO currency; quantities are decimal plus UOM.
5. External model APIs stay disabled. Today's Ollama route is local inference.
6. In-memory mode is a development runtime only. PostgreSQL remains the production adapter and
   `environment=production` already refuses anything else.
7. No production or beta-completeness claim while any Phase 7 gate is open. Today produces a
   working application, not a shippable beta — those are different claims and we will not blur them.
