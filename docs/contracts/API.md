# API conventions

All public resources are under `/v1`. Large collections use opaque cursor pagination. Errors use RFC 9457 `application/problem+json`. Commands require `Idempotency-Key`; versioned resources use quoted numeric ETags and `If-Match`. Every response, job, event, and model trace carries a UUID correlation ID.

Imports, document indexing, exports, forecasts, and connector backfills return `202 Accepted` with a job resource. Uploads use signed direct-upload URLs; downloads are short-lived and audited.

`POST /v1/conversations/{id}/messages` accepts content, attachment IDs, requested scope, client message ID, and referenced records. Its SSE stream emits typed blocks: `answer_text`, `record_summary`, `forecast_summary`, `recommendation`, `citation`, `action_proposal`, `clarification`, `warning`, `error`, and `completed`.

Conversation persistence includes exact model profile/revision, prompt/tool/retriever/chunker versions, authorized evidence and tool results, record versions and freshness, exact document spans, fallback/validation status, and feedback.
