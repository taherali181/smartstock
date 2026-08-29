# Threat model

## Protected assets

Inventory and financial records, customer/supplier data, documents, OAuth credentials, API/service secrets, model prompts/evidence, exports, audit history, and availability of warehouse operations.

## Trust boundaries

Browser/PWA → WAF/ALB → API; API → PostgreSQL/Redis/RabbitMQ/S3; connectors → provider APIs/webhooks; document parser → untrusted files; RAG orchestrator → retrieved data/model servers; CI → registry/deployment role; administrators → Keycloak and model/connector controls.

## Priority threats and controls

| Threat | Required controls | Verification |
| --- | --- | --- |
| Cross-tenant record access | service authorization, composite FKs, forced RLS, transaction-local tenant context | two-tenant API and direct-SQL tests |
| Connection identity leakage | `set_config(..., true)`, rollback-on-return, no session-level `SET` | alternating pooled-connection test |
| Warehouse privilege bypass | grant filtering on reads and commands; separate organization-wide capability | multi-grant authorization tests |
| Credential theft | Keycloak MFA-ready policy, KMS/Secrets Manager, one-time API secret display, hashed tokens, rotation/revocation | secret scanning and revocation tests |
| Replay/duplicate mutation | signed webhook windows, tenant idempotency keys, consumer receipts | concurrent duplicate and out-of-order tests |
| Ledger tampering | append-only triggers, balanced constraint, audit, restore/reconciliation | mutation rejection and projection rebuild tests |
| Object/export leakage | tenant-derived object keys, private buckets, signed short-lived downloads, audited export creation/download | cross-tenant key and URL tests |
| Cache collision | organization and authorization scope in every key; cache is never authoritative | adversarial keyspace tests |
| Queue context forgery | immutable signed job envelope, tenant context re-established by worker | job boundary tests |
| Prompt injection/tool escalation | retrieved content treated as data; allowlisted typed read tools; writes only via proposals | injection and tool-schema red team suite |
| Malicious document | quarantine, MIME/magic checks, antivirus, sandboxed parser, quotas | polyglot/bomb/eicar fixtures |
| SSRF | outbound allowlists, private-address denial, connector-specific clients | URL parser and egress tests |
| Supply-chain compromise | pinned dependencies/images/models, SBOM, signatures, restricted CI permissions | CI scan gates |
| Destructive administrator action | reauthentication/approval where material, immutable audit, backups and restore drills | policy and recovery exercises |

## Abuse cases

The system must remain safe when a user changes organization IDs, guesses UUIDs, reuses another tenant’s idempotency key, injects organization IDs into job payloads, requests an object key containing traversal segments, submits an expired proposal, alters proposal evidence versions, sends duplicate webhooks, or embeds tool instructions in retrieved documents.

## Residual risks for beta

No contractual SLA, RPO, RTO, or Canadian residency guarantee is made. Self-hosted models reduce third-party disclosure but retain prompt-injection and model-quality risk. Operational cost and GPU capacity remain availability constraints. These are disclosed and monitored; they do not weaken tenant or ledger integrity gates.
