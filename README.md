# SmartStock

SmartStock is a cloud-first, multitenant inventory and order-management platform for US and Canadian retail and wholesale businesses. Its authoritative inventory is an immutable, balanced ledger; AI answers use permission-filtered evidence; AI-generated writes remain inert proposals until a permitted reviewer approves them.

## Repository status

This repository now contains the first executable production-foundation slice:

- `apps/web`: the React/TypeScript conversational workspace, OIDC/PKCE boundary, TanStack Query, and generated OpenAPI client.
- `apps/api`: Python 3.12 FastAPI API contracts, OIDC/development identity boundary, RFC 9457 errors, correlation IDs, an invariant-tested inventory domain, approval proposal state machine, Alembic migration, forced PostgreSQL RLS, outbox, and immutable audit/ledger storage.
- `services/forecasting`: isolated baseline forecast/evaluation service with stockout censoring, exact decimal metrics, quantile contracts, and champion-promotion gates.
- `compose.yaml`: PostgreSQL 16 + pgvector, RabbitMQ, Redis, MinIO, and Keycloak for local infrastructure.
- Executable architecture, security, domain, event, and API conventions in `docs`.

The API includes both an in-memory test/development adapter and a PostgreSQL command repository. The PostgreSQL path claims idempotency, locks the stock position, appends balanced ledger lines, advances the projection, and writes audit/outbox records in one tenant-scoped transaction. Deployment must stay blocked until its real-PostgreSQL concurrency/RLS suite passes. The later WMS, RAG, integrations, wholesale, and production-hardening phases remain roadmap work; this repository does not claim beta completeness yet.

## Run and verify

Frontend:

```bash
npm install
npm run dev
```

API in explicit development-auth mode:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e "apps/api[test]"
SMARTSTOCK_AUTH_MODE=development uvicorn smartstock_api.main:app --app-dir apps/api --reload
```

Forecast service:

```bash
pip install -e "services/forecasting[test]"
uvicorn smartstock_forecasting.main:app --app-dir services/forecasting --port 8001
```

Quality gates:

```bash
make check
```

Local platform (requires Docker):

```bash
docker compose up -d
```

Development credentials in `.env.example` and `compose.yaml` are local-only. OIDC is the default API authentication mode and development header auth is refused when `SMARTSTOCK_ENVIRONMENT=production`.

## Source-of-truth boundaries

- SmartStock owns inventory, purchasing, allocation, warehouse execution, and operational order state.
- Shopify owns storefront content and checkout.
- QuickBooks Online and Xero own the general ledger, tax, payment, and reconciled accounting state.
- ShipStation owns carrier labels and carrier tracking integration.
- Stripe performs B2B payment collection.
- Forecasts and AI output are evidence-bound recommendations, never autonomous stock or financial mutations.

See [Architecture](docs/ARCHITECTURE.md), [domain contracts](docs/contracts/DOMAIN.md), [security model](docs/contracts/SECURITY.md), and [delivery roadmap](docs/PRODUCT_ROADMAP.md).
