# Core lane status

## Requests

- The full API unit suite hangs while creating the `TestClient` fixture for edge-owned
  `test_proposals_flow.py::test_a_question_is_not_a_write`. The core PostgreSQL suite
  remains green (11/11). Please make the edge fixture deterministic and bounded so
  `npm run test:api` completes without manual interruption.
