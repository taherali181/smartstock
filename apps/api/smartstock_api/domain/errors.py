class DomainError(Exception):
    code = "domain_error"
    status_code = 422


class IdempotencyConflict(DomainError):
    code = "idempotency_conflict"
    status_code = 409


class ConcurrencyConflict(DomainError):
    code = "concurrency_conflict"
    status_code = 412


class InsufficientStock(DomainError):
    code = "insufficient_stock"
    status_code = 409


class UnbalancedPosting(DomainError):
    code = "unbalanced_posting"


class TenantBoundaryViolation(DomainError):
    code = "tenant_boundary_violation"
    status_code = 403


class ResourceNotFound(DomainError):
    code = "resource_not_found"
    status_code = 404


class DuplicateResource(DomainError):
    code = "duplicate_resource"
    status_code = 409


class InvalidStateTransition(DomainError):
    code = "invalid_state_transition"
    status_code = 409


class InvalidQuantity(DomainError):
    code = "invalid_quantity"
