# Security and permission contract

## Trust boundaries

Keycloak authenticates people and service credentials. SmartStock owns organizations, memberships, role permissions, approval policies, and warehouse grants. OIDC is the default; development header identity is available only outside production.

Authorization is deny-by-default at the HTTP service and forced RLS layers. Every request establishes transaction-local `app.organization_id` and `app.user_id`. Connection return rolls back state. Background jobs carry signed immutable identity context and establish the same database settings.

## Permission dimensions

Roles are owner, administrator, planner, buyer, warehouse operator, salesperson, accountant, and viewer. Permissions are capabilities, not role-name checks:

| Domain | View | Propose | Approve | Execute |
| --- | --- | --- | --- | --- |
| Inventory | `inventory.view` | `inventory.propose` | `inventory.approve` | `inventory.adjust` |
| Purchasing | `purchasing.view` | `purchasing.propose` | `purchasing.approve` | `purchasing.execute` |
| Orders | `orders.view` | `orders.propose` | `orders.approve` | `orders.execute` |
| AI | `ai.use` | `ai.propose` | `ai.approve` | never direct |
| Exports | `exports.create` | n/a | policy-dependent | `exports.download` |

Warehouse grants further restrict every warehouse-scoped permission. An organization-wide permission must be explicit.

## Authentication requirements

- Validate signature, issuer, audience, expiry, issued-at, subject, and organization claims.
- Accept only pinned algorithms; never use an algorithm from untrusted token input.
- Support Keycloak key rotation, session revocation, verified email, recovery, and MFA-ready policies.
- Hash API credentials; show secrets once; support expiry, scopes, rotation, and revocation.
- Webhook callbacks authenticate provider signatures against the raw body and enforce replay windows.

## AI and document defenses

ACL/tenant filters run before candidate ranking. Retrieved content is data, not instruction. Uploads require size/quota checks, MIME and magic validation, antivirus quarantine, content hashing, parser sandboxing, and deletion propagation. Model fallback is permitted only across evaluated profiles with compatible residency, classification, context, and tool schemas. External model APIs are disabled.

Required adversarial gates: 100% refusal across forbidden tenant/warehouse/document cases; zero successful prompt-injection tool invocation or permission escalation; 100% displayed-citation authorization.
