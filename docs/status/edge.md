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

## Requests

- `/v1/purchase-orders` returns `total` as `1100.000000000000000000`. Clients
  trim trailing zeros for display; the operations schema still emits them.
