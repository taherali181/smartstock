# Core lane status

## Requests

- The full API unit suite hangs in edge-owned
  `test_proposals_flow.py::test_a_question_is_not_a_write`. A run with
  `faulthandler_timeout=8` locates the deadlock at line 43 inside
  `TestClient.__enter__`, before the test sends a request or reaches a model adapter.
  The core PostgreSQL suite remains green (11/11). Please make the edge fixture
  deterministic and bounded so `npm run test:api` completes without interruption.
