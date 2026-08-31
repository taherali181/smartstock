# SmartStock parallel delivery plan (Codex + Claude)

This file governs how two agents build `ORIGINAL_PLAN.md` concurrently in one repository.
`ORIGINAL_PLAN.md` remains the product specification. This file only decides **who builds what,
where they are allowed to write, and how their work merges without collision.**

Baseline at time of writing: Phase 0–2 complete, Phase 3 in progress, migrations `0001`–`0010`,
head `0010_staged_transfer_execution`, single branch `main`, sole author Codex.

---

## 1. Why the work splits this way

The split is not by phase. It is by **the kind of correctness each subsystem demands**, matched to
what each model is measurably better at.

**GPT-5.6-Codex (sol-high) — deterministic core.**
It wrote every line of the existing backend and holds the implicit conventions of
`postgres_operations.py` (2,176 lines) and `postgres_inventory.py` (2,295 lines). Its strengths are
long-horizon, spec-driven implementation inside an existing codebase; holding invariants steady
across large files; SQL, row-level locking, migration and concurrency correctness; and numeric
determinism. It is most valuable where the specification is already written and the failure mode is
a silent arithmetic or isolation bug. It is least valuable where the specification is prose that has
to be invented into an architecture, or where product surface has to be judged.

**Opus 5 (high) — greenfield, adversarial, and edge systems.**
Its strengths are turning prose requirements into subsystem architecture, prompt and retrieval
engineering, adversarial reasoning (prompt injection, ACL bypass, exfiltration, red-team suites),
messy external contracts (OAuth, webhook signatures, out-of-order convergence), and
React/TypeScript/PWA product surface. It is most valuable where nothing exists yet and the failure
mode is a design that cannot be secured or a screen an operator cannot use.

Therefore:

| Subsystem | Owner | Reason |
| --- | --- | --- |
| Ledger, orders, WMS execution, valuation | **Codex** | Existing author; transactional invariants |
| Forecasting portfolio, backtests, promotion gates | **Codex** | Numeric determinism, leakage rules, isolated service |
| Connector runtime (cursors, replay, reconciliation) | **Codex** | Deterministic convergence under duplicate/out-of-order delivery |
| RAG ingestion, retrieval, tools, SSE, evals | **Claude** | Greenfield architecture + adversarial security |
| Action proposals: persistence, validation, executor | **Claude** | Security-critical, version-bound, red-team surface |
| Provider adapters (Shopify, QBO, Xero, ShipStation, Stripe) | **Claude** | Messy external semantics, OAuth, sandbox contracts |
| All of `apps/web` (operational views, PWA, B2B portal) | **Claude** | Single frontend owner; PWA/offline/a11y depth |
| Security review, pentest, injection and a11y gates | **Claude** | Adversarial reasoning |
| Load, soak, failover, restore, concurrency drills | **Codex** | Deterministic reproduction and measurement |

Two lanes result:

- **Lane `core` — Codex.** Transactional truth and numerical truth.
- **Lane `edge` — Claude.** Intelligence layer, external edges, and every client surface.

---

## 2. Ownership matrix

An agent may **create, edit, and delete** only inside its own paths. Paths marked *shared* follow
the protocol in section 5. Anything not listed is read-only for both until claimed here.

### Lane `core` (Codex)

```
apps/api/smartstock_api/domain/{inventory,catalog,operations,valuation,allocation,availability,workflows,importer,platform}.py
apps/api/smartstock_api/domain/ports.py                  (creates; additive-only after publication)
apps/api/smartstock_api/domain/reporting.py              (new)
apps/api/smartstock_api/domain/notifications.py          (new)
apps/api/smartstock_api/infrastructure/postgres_*.py
apps/api/smartstock_api/infrastructure/{database,outbox,authorization,tenant_resources}.py
apps/api/smartstock_api/api/routes/{catalog,inventory,operations,platform,reports}.py
apps/api/smartstock_api/api/{schemas,catalog_schemas,operations_schemas,reporting_schemas}.py
apps/api/smartstock_api/integrations/runtime/**          (connector SDK, cursors, replay, reconciliation)
apps/api/smartstock_api/workers/tasks/{inventory,operations,reporting,connectors}.py
apps/api/alembic/versions/00{11..49}_*.py                (migration range 0011–0049)
apps/api/tests/test_{inventory,catalog,operations,allocation,reservations,valuation,workflows,importer,reporting,connector_runtime}*.py
apps/api/tests/test_postgres_*.py
services/forecasting/**
docs/adr/00{09..19}-*.md                                 (ADR range 0009–0019)
docs/status/core.md
.github/workflows/forecasting.yml, .github/workflows/load.yml
compose.forecasting.yaml
```

### Lane `edge` (Claude)

```
apps/api/smartstock_api/rag/**                           (retrieval, chunking, fusion, rerank, compose, guards)
apps/api/smartstock_api/documents/**                     (ingest, Docling/OCR, quarantine, hashing, quotas)
apps/api/smartstock_api/conversations/**                 (sessions, SSE blocks, provenance, feedback)
apps/api/smartstock_api/proposals/**                     (persistence, impact validation, executor)
apps/api/smartstock_api/models/**                        (ModelProfile, LiteLLM routing, health, fallback)
apps/api/smartstock_api/integrations/providers/**        (shopify, qbo, xero, shipstation, stripe, csv, rest)
apps/api/smartstock_api/api/routes/{documents,conversations,proposals,integrations,models}.py
apps/api/smartstock_api/api/{rag_schemas,integration_schemas}.py
apps/api/smartstock_api/domain/proposals.py              (state machine; core does not edit)
apps/api/smartstock_api/workers/tasks/{documents,indexing,conversations,providers}.py
apps/api/alembic/versions/00{50..99}_*.py                (migration range 0050–0099)
apps/api/tests/test_{rag,documents,conversations,proposals,models,integration_provider,injection,acl}*.py
apps/api/tests/fakes/**                                  (in-memory doubles for core ports)
apps/web/**                                              (entire frontend, PWA, B2B portal)
docs/adr/00{20..29}-*.md                                 (ADR range 0020–0029)
docs/contracts/{THREAT_MODEL,SECURITY}.md
docs/status/edge.md
.github/workflows/ai.yml, .github/workflows/web.yml
compose.ai.yaml
```

### Shared — protocol required (section 5)

```
apps/api/smartstock_api/main.py                          (router registry only)
apps/api/smartstock_api/config.py                        (per-lane nested settings only)
apps/api/smartstock_api/api/routes/__init__.py
compose.yaml, Makefile, package.json, .github/workflows/quality.yml
docs/IMPLEMENTATION_STATUS.md, docs/PRODUCT_ROADMAP.md
docs/contracts/{API,DOMAIN,EVENTS,DATA_DICTIONARY,TEST_STRATEGY}.md
```

### Generated — never hand-merged

```
docs/contracts/openapi.json
apps/web/src/api/schema.d.ts
package-lock.json
```

### Frozen for both

```
ORIGINAL_PLAN.md, PARALLEL_PLAN.md, docs/adr/0001-0008
```

---

## 3. Wave schedule

Waves are synchronization points, not hard barriers. A lane that finishes early pulls the next
wave's first item rather than crossing into the other lane.

### Wave 0 — Enablement (both, ~half a day, sequential where noted)

Must land on `main` before either lane opens a long-running branch.

1. **Claude:** `.gitattributes` for generated artifacts; `docs/status/{core,edge}.md`; split
   `main.py` router inclusion into `api/routes/__init__.py::iter_routers()` reading two per-lane
   tuples, so route registration stops being a conflict site.
2. **Codex:** publish `apps/api/smartstock_api/domain/ports.py` — `Protocol` definitions only, no
   implementations — covering the read and command surface the edge lane consumes (section 4).
   This is the single highest-priority unblocker; nothing in Lane `edge` beyond documents/retrieval
   can be finished without it.
3. **Codex:** add a CI step asserting `alembic heads` returns exactly one head.
4. Both: create worktrees (section 5.1).

### Wave 1 — Phase 3 tail ∥ Phase 4 foundation

**Codex (`core`)**
- Warehouse execution for the three task types that exist as enum values but have no commands:
  `PUTAWAY`, `PICK`, `PACK` — bin-to-bin movement posting, pick confirmation against reservations,
  pack verification, wave/batch grouping.
- Shipment completion: `packed → labelled` states, cartons, labels, manifests.
- Transfer discrepancy resolution commands (`discrepancy_review → closed`) with variance posting.
- Replenishment task generation and warehouse exception queues with ownership, due dates, escalation.
- Reporting read models: valuation, aging, sell-through, fill rate, turns, dead stock, margin,
  order cycle time, supplier scorecards. Endpoints under `/v1/reports`, scheduled exports via Celery.
- Notification/automation engine: rules, webhooks, saved filters, driven off the existing outbox.

**Claude (`edge`)**
- Document pipeline: signed upload, MIME/magic validation, antivirus quarantine, content hashing,
  Docling parse, OCR fallback, structural chunking with page/section/table/bbox provenance, quotas,
  deletion propagation. Migrations `0050+` add document, chunk, and `pgvector` HNSW tables.
- Hybrid retrieval: PostgreSQL FTS + pgvector, RRF fusion, BGE reranking, tenant/ACL filters applied
  **before** ranking, mandatory lexical branch for SKU/lot/serial/PO/order identifiers.
- `ModelProfile` registry, LiteLLM gateway routing, health/quota/fallback policy, llama.cpp degraded
  route. External model APIs stay disabled by configuration and by test.
- Typed operational read tools implemented against `domain/ports.py`, with fakes under
  `apps/api/tests/fakes/` so the lane never blocks on core.
- Phase 3 PWA gap closure, which is frontend-only and needs no new backend: offline receipt-line
  entry, putaway/pick/pack execution screens, transfer discrepancy review, device/browser matrix.

### Wave 2 — Phase 5 ∥ Phase 4 completion

**Codex (`core`)** — forecasting, in `services/forecasting`, which is 226 lines today and therefore
entirely greenfield with zero collision surface:
- Point-in-time demand facts at `organization × SKU × location × channel × local date`, with
  stockout censoring, returns kept separate from original demand, and cutoff-respecting folds.
- Portfolio: Naive/SeasonalNaive/mean, AutoETS/AutoTheta/AutoARIMA/MSTL, Croston SBA & Optimized,
  TSB, ADIDA, IMAPA, LightGBM Tweedie + quantile variants.
- Rolling-origin backtests at lead-time-plus-review horizons, ≥3 folds (6 preferred), WRMSSE/WAPE
  primary, MASE/bias/MAE/pinball/coverage/calibration secondary, business simulation.
- Cohort-level champion selection, MLflow registry, promotion gates with authorized approver, audit
  reason and rollback target, drift detection, nightly refresh, weekly retrain.
- Replenishment proposal generation — quantities, transfer-vs-buy, safety stock, reorder points,
  supplier selection, timing, MOQ/case-pack rounding, budget constraints — emitted as **drafts** into
  the edge lane's proposal store through `domain/ports.py`, never executed directly.

**Claude (`edge`)**
- SSE conversation contract: all nine typed blocks, persisted model profile/revision, prompt/tool/
  retriever/chunker versions, evidence IDs, record versions, freshness, citations, fallback status,
  feedback.
- Action proposals end to end: persistence, impact preview, version-bound revalidation at approval,
  and an executor that calls the **same** command endpoints the manual UI uses.
- RAG release gates as executable suites: 100% citation resolution, ≥95% citation-supported claims,
  100% refusal on forbidden tenant/warehouse/document cases, zero successful injection or escalation,
  exact numeric/identifier accuracy, schema validity, safe abstention, shadow/canary before promotion.
- Frontend: conversational canvas on live SSE, reporting views over Codex's Wave 1 endpoints, bulk
  editing, approvals, administration.

### Wave 3 — Phase 6, split down the middle

The connector framework is one clean seam: Codex builds the runtime, Claude builds the adapters.

**Codex** — connector SDK: credential vault integration, resumable backfill, sync cursors and
checkpointing, duplicate and out-of-order convergence, rate-limit and token-expiry handling, partial
success, dead-letter, replay, reconciliation reports and the repair engine.

**Claude** — provider adapters against that SDK: Shopify, QuickBooks Online, Xero, ShipStation,
Stripe, generic CSV and versioned REST/webhook; OAuth flows, signed webhook verification, field
ownership matrices, SKU/warehouse/tax/currency/bundle/status mappings, channel buffers and oversell
policy, sandbox contract tests. Plus the B2B portal, price lists, terms, credit controls, invoices
and payments.

Connectors write **only** through published command ports. No provider adapter touches ledger
internals.

### Wave 4 — Phase 7

**Codex:** load and soak at the stated scale envelope, queue saturation, database failover, object
outage, model outage, connector degradation, backup restore and PITR drills, tenant export/deletion.

**Claude:** security review, dependency/SBOM/image scanning, penetration testing, accessibility and
browser coverage, support tooling, staged rollout gating.

---

## 4. Contract seams

Parallelism holds only where a stable interface separates the lanes. Three seams matter.

**Seam 1 — `domain/ports.py` (core publishes, edge consumes).**
`Protocol` classes for permission-checked reads (inventory positions, ATP by horizon, product and
supplier lookup, purchase-order status, sales-order status, shipment status, warehouse task status,
reporting aggregates, forecast lookup) and for command execution (the named command surface a
proposal executor or connector may invoke). Rules: additive only after publication; a breaking
change requires a note in `docs/status/core.md` and a matching edge commit in the same merge window.
Edge implements fakes in `apps/api/tests/fakes/` and never imports `infrastructure/postgres_*`.

**Seam 2 — OpenAPI (core produces, edge consumes).**
Frontend work is generated-client-driven. Core lands endpoints, regenerates the contract, merges;
edge pulls `main` and regenerates. Edge never hand-edits `schema.d.ts`.

**Seam 3 — Proposals (edge owns store, core produces drafts).**
Replenishment output from forecasting enters the edge lane's proposal store through a port, in
`draft`. Approval and execution stay entirely in the edge lane. `domain/proposals.py` — the state
machine — is edge-owned; core reads it, does not edit it.

---

## 5. Collision protocol

### 5.1 Working trees and branches

Two agents cannot share one working directory. Use worktrees:

```
git worktree add ../smartstock-core -b lane/core
git worktree add ../smartstock-edge -b lane/edge
```

Codex works only in `../smartstock-core`; Claude only in `../smartstock-edge`. Branches are
short-lived: rebase on `main` before every merge, merge to `main` at least once per working day and
never later than three commits. A lane branch older than 24 hours is a defect.

### 5.2 Migrations

Reserved ranges: core `0011`–`0049`, edge `0050`–`0099`. Filenames never collide.

Both lanes keep a **lane-local linear chain**. On merge, if `main`'s head has moved, the merging lane
edits exactly one line — the `down_revision` of the oldest unmerged migration in its own chain — to
point at the new head. That is the only circumstance in which a lane touches a migration file after
it has been written, and it never touches the other lane's files.

CI enforces a single head and continues to run `alembic downgrade 0002 && alembic upgrade head`.
Enum and column additions are additive; neither lane alters or drops another lane's tables.

### 5.3 Generated artifacts

`.gitattributes` marks `docs/contracts/openapi.json`, `apps/web/src/api/schema.d.ts` and
`package-lock.json` as non-mergeable. On any conflict the resolution is always:

```
git checkout --ours <file> && npm run generate:api      # or: npm install
```

Never resolve these by hand. `npm run check:api-drift` in CI is the backstop.

### 5.4 Shared files

- `main.py` / `api/routes/__init__.py`: after Wave 0, each lane appends to its own router tuple. Two
  independent tuples means append-only edits in different regions.
- `config.py`: each lane adds one nested settings model, declared in its own module and referenced by
  a single line. No lane reorders existing fields.
- `compose.yaml`: frozen. New services go in `compose.ai.yaml` (edge: vLLM, TEI, Langfuse) and
  `compose.forecasting.yaml` (core: MLflow), composed with `-f`.
- CI: `quality.yml` is frozen; each lane adds its own workflow file.
- `docs/IMPLEMENTATION_STATUS.md` and `docs/PRODUCT_ROADMAP.md`: edited **only when a phase gate
  closes**, only by the lane that closed it, in a commit that changes nothing else. Day-to-day
  progress goes in `docs/status/core.md` and `docs/status/edge.md`.
- `docs/contracts/*.md`: edits confined to the sections a lane owns; append new sections rather than
  restructuring existing ones.

### 5.5 Tests

Test files are lane-prefixed per section 2. Neither lane edits the other's test files. Both run
`make check` before every merge; the lane whose merge breaks the other lane's suite fixes it or
reverts within one commit.

### 5.6 Escalation

A lane that needs a change inside the other lane's paths does not make it. It records the request in
its own `docs/status/*.md` under a `## Requests` heading and continues on other work. The owning lane
picks it up at its next merge. If both lanes are blocked on each other, the human breaks the tie.

---

## 6. Gate ownership

| Gate | Owner | Blocking dependency |
| --- | --- | --- |
| Phase 3 exit — state/retry coverage, scanner workflows | Both | Core commands, then edge screens |
| Phase 4 exit — citation, quality, isolation, abstention, action safety | Claude | Core `ports.py` |
| Phase 5 exit — baseline, calibration, leakage, reproducibility, runtime, business simulation | Codex | None |
| Phase 6 exit — replay convergence, reconciliation detects every injected mismatch | Both | Core SDK, then edge adapters |
| Phase 7 exit — no critical findings, no discrepancies, successful restore drill | Both | All prior gates |

No lane may mark a gate satisfied in `docs/IMPLEMENTATION_STATUS.md` on the basis of its own half.
Phase 3 and Phase 6 gates require both lanes to sign off in the same commit.

---

## 7. Standing rules

1. Every mutation keeps the existing discipline: idempotency key, actor, reason code, business
   reference, entity versions, atomic ledger posting plus outbox event, exact projection
   reconciliation. Neither lane relaxes this to move faster.
2. Retrieved documents are untrusted data in every code path. They cannot grant permissions, alter
   instructions, or invoke tools.
3. AI output is inert until an authorized human approves it, and approval revalidates permission,
   policy, inventory, prices, source versions and staleness before executing the same command the
   manual UI uses.
4. Money is `NUMERIC` plus ISO currency; quantities are decimal plus UOM. No exceptions in either lane.
5. External model APIs stay disabled.
6. No production or beta-completeness claim while any Phase 7 gate is open.
