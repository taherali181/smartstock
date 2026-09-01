# Core lane status

## Requests

- The full API unit suite hangs in edge-owned
  `test_proposals_flow.py::test_a_question_is_not_a_write`. A run with
  `faulthandler_timeout=8` locates the deadlock at line 43 inside
  `TestClient.__enter__`, before the test sends a request or reaches a model adapter.
  The core PostgreSQL suite remains green (11/11). Please make the edge fixture
  deterministic and bounded so `npm run test:api` completes without interruption.

- Browser GP-5 currently answers `why can't I allocate sales order SO-1004?` with
  only the order record (`confirmed`, 50 units of SKU-1017). It needs to combine
  the sales-order line with authorized inventory/availability evidence and state
  the actual shortfall; the current response does not answer “why”. The other
  three required questions return cited, freshness-stamped operational answers.

- The phone-width warehouse path works, including offline replay, but task state
  labels render underscores (`In_progress`). Please have the edge PWA label helper
  replace underscores as well as hyphens.
