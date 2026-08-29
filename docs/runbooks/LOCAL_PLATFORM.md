# Local platform runbook

## Start

Copy `.env.example` to `.env`, then start the platform dependencies:

```bash
docker compose up -d postgres rabbitmq redis minio minio-init keycloak
docker compose run --rm api alembic upgrade head
docker compose up -d api forecasting prometheus grafana loki tempo
```

The web application runs with development authentication by default. Set `VITE_AUTH_MODE=oidc` to exercise Keycloak PKCE. The imported realm is for local use; production redirect origins and administrative credentials must come from environment-specific provisioning.

## Verify

```bash
npm run check
npm run check:api-drift
curl --fail http://localhost:8000/health/live
curl --fail http://localhost:8000/health/ready
curl --fail http://localhost:8000/metrics
```

Run the full platform integration matrix with the environment variables used by `.github/workflows/quality.yml`. Unit tests skip marked external/PostgreSQL cases when those services are absent.

## Stop

```bash
docker compose down
```

Named volumes retain local state. Use `docker compose down --volumes` only when intentionally deleting all local development data.
