# Test strategy

## Test layers

1. Pure unit and property tests cover calculations, state machines, authorization, key namespaces, idempotency, and deterministic model gates.
2. PostgreSQL integration tests run every migration from empty, test downgrade where supported, exercise forced RLS as the application role, alternate tenant context through a one-connection pool, and race inventory commands.
3. RabbitMQ/Redis/S3 integration tests cover duplicate delivery, lease expiry, dead-letter/replay, quota boundaries, tenant key isolation, and object deletion.
4. OpenAPI tests validate Problem Details, command headers, SSE block schemas, and generated TypeScript compatibility.
5. Provider sandbox tests cover webhook signatures, rate limits, token expiry, reconciliation, and replay.
6. Browser/PWA tests cover offline task sync, conflicts, scanners, accessibility, and supported responsive breakpoints.
7. RAG/forecast evaluation suites are versioned release artifacts, not ad hoc demonstrations.
8. Load, failure, restore, export/deletion, and security drills gate beta rollout.

## Mandatory Phase 1 adversarial matrix

| Boundary | Attack | Expected result |
| --- | --- | --- |
| API | tenant A token requests tenant B resource | 404/403 with no existence leak |
| RLS | tenant A session selects/inserts tenant B row | zero rows/policy violation |
| Pool | tenant A transaction returned then tenant B borrows connection | only tenant B context visible |
| Job | payload organization differs from signed envelope | reject and audit |
| Cache | tenants use identical logical keys | different physical keys and values |
| File | traversal or tenant B object key supplied | reject before S3 call |
| Export | tenant A requests tenant B export/artifact | reject and audit |
| Idempotency | same key in two tenants | independent; same tenant/different request conflicts |

## CI gates

Every change runs web lint/build, Python unit tests, clean Alembic SQL generation, secret/dependency/image scanning, and generated contract drift checks. PostgreSQL and service integration jobs use production-compatible versions. Deployment requires migration success followed by smoke tests; rollback never silently reverses financial or ledger history.

## Test data

Fixtures always include two organizations, overlapping SKUs/order numbers/object names, users with different warehouse grants, decimal quantities, lot/serial dimensions, and duplicate/out-of-order events. Synthetic data contains no customer PII.
