# ADR 0004: tenancy and identity

Status: accepted

Keycloak authenticates OIDC subjects; SmartStock owns organizations, memberships, permissions, approval policies, and warehouse grants. Authorization is enforced in service code and forced PostgreSQL RLS. Tenant/user database context is transaction-local and pooled connections roll back on return. Human, API client, and service identities remain distinct and auditable.
