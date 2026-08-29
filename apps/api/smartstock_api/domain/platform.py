from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import PurePosixPath
from threading import RLock
from typing import Any, Protocol
from uuid import UUID, uuid4

from .errors import TenantBoundaryViolation


class Role(StrEnum):
    OWNER = "owner"
    ADMINISTRATOR = "administrator"
    PLANNER = "planner"
    BUYER = "buyer"
    WAREHOUSE_OPERATOR = "warehouse_operator"
    SALESPERSON = "salesperson"
    ACCOUNTANT = "accountant"
    VIEWER = "viewer"


ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.OWNER: frozenset({"*"}),
    Role.ADMINISTRATOR: frozenset(
        {"administration.manage", "inventory.view", "inventory.adjust", "exports.create"}
    ),
    Role.PLANNER: frozenset(
        {"inventory.view", "forecast.view", "forecast.propose", "ai.use", "ai.propose"}
    ),
    Role.BUYER: frozenset(
        {"inventory.view", "purchasing.view", "purchasing.propose", "purchasing.execute"}
    ),
    Role.WAREHOUSE_OPERATOR: frozenset(
        {"inventory.view", "inventory.adjust", "warehouse.execute"}
    ),
    Role.SALESPERSON: frozenset({"inventory.view", "orders.view", "orders.execute"}),
    Role.ACCOUNTANT: frozenset(
        {"inventory.view", "accounting.view", "exports.create"}
    ),
    Role.VIEWER: frozenset({"inventory.view"}),
}


@dataclass(frozen=True, slots=True)
class Organization:
    id: UUID
    slug: str
    name: str
    currency: str
    valuation_method: str = "weighted_average"


@dataclass(frozen=True, slots=True)
class Membership:
    organization_id: UUID
    user_id: UUID
    role: Role
    active: bool = True

    @property
    def permissions(self) -> frozenset[str]:
        return ROLE_PERMISSIONS[self.role]


@dataclass(frozen=True, slots=True)
class WarehouseGrant:
    organization_id: UUID
    user_id: UUID
    warehouse_id: UUID


@dataclass(frozen=True, slots=True)
class ApprovalPolicy:
    id: UUID
    organization_id: UUID
    action_type: str
    minimum_approvals: int
    approver_permission: str
    amount_threshold: Decimal | None = None
    currency: str | None = None
    active: bool = True

    def applies(self, action_type: str, amount: Decimal | None, currency: str | None) -> bool:
        if not self.active or action_type != self.action_type:
            return False
        if self.amount_threshold is None:
            return True
        return currency == self.currency and amount is not None and amount >= self.amount_threshold


@dataclass(frozen=True, slots=True)
class PlatformJob:
    id: UUID
    organization_id: UUID
    actor_id: UUID
    queue: str
    job_type: str
    correlation_id: UUID
    payload: dict[str, Any]
    status: str = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class TenantKeyspace:
    @staticmethod
    def cache(organization_id: UUID, namespace: str, logical_key: str) -> str:
        TenantKeyspace._validate(namespace)
        TenantKeyspace._validate(logical_key)
        return f"smartstock:{organization_id}:{namespace}:{logical_key}"

    @staticmethod
    def object(organization_id: UUID, purpose: str, object_id: UUID, filename: str) -> str:
        TenantKeyspace._validate(purpose)
        TenantKeyspace._validate(filename)
        safe_name = filename.replace(" ", "_")
        return f"{organization_id}/{purpose}/{object_id}/{safe_name}"

    @staticmethod
    def assert_owned(organization_id: UUID, object_key: str) -> None:
        if not object_key.startswith(f"{organization_id}/"):
            raise TenantBoundaryViolation("object key belongs to a different organization")
        TenantKeyspace._validate(object_key)

    @staticmethod
    def _validate(value: str) -> None:
        if not value or "\x00" in value or value.startswith("/"):
            raise ValueError("unsafe tenant key component")
        path = PurePosixPath(value)
        if ".." in path.parts or "." in path.parts:
            raise ValueError("tenant keys cannot contain traversal segments")


class PlatformStore(Protocol):
    def organization(self, organization_id: UUID) -> Organization: ...

    def policies_for(
        self, organization_id: UUID, action_type: str | None = None
    ) -> list[ApprovalPolicy]: ...

    def flags_for(self, organization_id: UUID) -> dict[str, tuple[bool, dict[str, Any]]]: ...


class InMemoryPlatformStore:
    """Deterministic platform adapter used by unit and transport contract tests."""

    def __init__(self) -> None:
        self._organizations: dict[UUID, Organization] = {}
        self._memberships: dict[tuple[UUID, UUID], Membership] = {}
        self._grants: set[WarehouseGrant] = set()
        self._policies: dict[tuple[UUID, UUID], ApprovalPolicy] = {}
        self._flags: dict[tuple[UUID, str], tuple[bool, dict[str, Any]]] = {}
        self._jobs: dict[tuple[UUID, UUID], PlatformJob] = {}
        self._lock = RLock()

    def add_organization(self, organization: Organization) -> None:
        with self._lock:
            self._organizations[organization.id] = organization

    def organization(self, organization_id: UUID) -> Organization:
        with self._lock:
            return self._organizations[organization_id]

    def add_membership(self, membership: Membership) -> None:
        if membership.organization_id not in self._organizations:
            raise KeyError("organization does not exist")
        with self._lock:
            self._memberships[(membership.organization_id, membership.user_id)] = membership

    def membership(self, organization_id: UUID, user_id: UUID) -> Membership | None:
        with self._lock:
            return self._memberships.get((organization_id, user_id))

    def grant_warehouse(self, grant: WarehouseGrant) -> None:
        if self.membership(grant.organization_id, grant.user_id) is None:
            raise TenantBoundaryViolation("warehouse grant requires a same-tenant membership")
        with self._lock:
            self._grants.add(grant)

    def warehouses_for(self, organization_id: UUID, user_id: UUID) -> frozenset[UUID]:
        with self._lock:
            return frozenset(
                grant.warehouse_id
                for grant in self._grants
                if grant.organization_id == organization_id and grant.user_id == user_id
            )

    def add_policy(self, policy: ApprovalPolicy) -> None:
        if policy.organization_id not in self._organizations:
            raise KeyError("organization does not exist")
        with self._lock:
            self._policies[(policy.organization_id, policy.id)] = policy

    def policies_for(
        self, organization_id: UUID, action_type: str | None = None
    ) -> list[ApprovalPolicy]:
        with self._lock:
            return [
                policy
                for (tenant_id, _), policy in self._policies.items()
                if tenant_id == organization_id
                and (action_type is None or policy.action_type == action_type)
            ]

    def set_flag(
        self, organization_id: UUID, key: str, enabled: bool, configuration: dict[str, Any]
    ) -> None:
        if organization_id not in self._organizations:
            raise KeyError("organization does not exist")
        with self._lock:
            self._flags[(organization_id, key)] = (enabled, configuration.copy())

    def flag(self, organization_id: UUID, key: str) -> tuple[bool, dict[str, Any]] | None:
        with self._lock:
            value = self._flags.get((organization_id, key))
            return None if value is None else (value[0], value[1].copy())

    def flags_for(self, organization_id: UUID) -> dict[str, tuple[bool, dict[str, Any]]]:
        with self._lock:
            return {
                key: (enabled, configuration.copy())
                for (tenant_id, key), (enabled, configuration) in self._flags.items()
                if tenant_id == organization_id
            }

    def enqueue(
        self,
        organization_id: UUID,
        actor_id: UUID,
        queue: str,
        job_type: str,
        correlation_id: UUID,
        payload: dict[str, Any],
    ) -> PlatformJob:
        if self.membership(organization_id, actor_id) is None:
            raise TenantBoundaryViolation("job actor is not a member of the organization")
        if "organization_id" in payload and str(payload["organization_id"]) != str(organization_id):
            raise TenantBoundaryViolation("job payload attempts to override tenant context")
        job = PlatformJob(
            id=uuid4(),
            organization_id=organization_id,
            actor_id=actor_id,
            queue=queue,
            job_type=job_type,
            correlation_id=correlation_id,
            payload=payload.copy(),
        )
        with self._lock:
            self._jobs[(organization_id, job.id)] = job
        return job

    def job(self, organization_id: UUID, job_id: UUID) -> PlatformJob | None:
        with self._lock:
            return self._jobs.get((organization_id, job_id))
