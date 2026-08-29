# Security response runbook

For suspected tenant leakage or credential compromise: disable affected service accounts/API clients, revoke Keycloak sessions, rotate secrets and connector tokens, preserve immutable audit/model traces, restrict exports, and identify every organization/resource touched. Do not delete evidence.

For malicious documents: keep the object quarantined, stop the parser job, record its digest and parser version, invalidate derived chunks, and propagate deletion to every index after investigation.

For model prompt injection: disable the affected model/tool release through a feature flag, preserve evidence/tool traces, verify no write command executed without approval, and rerun the forbidden-access and tool-escalation evaluation sets before promotion.
