from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from threading import RLock
from typing import Protocol
from uuid import UUID

from .errors import (
    DuplicateResource,
    IdempotencyConflict,
    InvalidQuantity,
    InvalidStateTransition,
    ResourceNotFound,
    TenantBoundaryViolation,
)
from .workflows import Workflow, WorkflowEntity


class OrderKind(StrEnum):
    PURCHASE = "purchase"
    SALES = "sales"


class WarehouseTaskType(StrEnum):
    RECEIVE = "receive"
    PUTAWAY = "putaway"
    PICK = "pick"
    PACK = "pack"
    TRANSFER = "transfer"
    COUNT = "count"
    REPLENISH = "replenish"


class WarehouseTaskState(StrEnum):
    OPEN = "open"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    EXCEPTION = "exception"
    CANCELLED = "cancelled"


TASK_TRANSITIONS: dict[WarehouseTaskState, frozenset[WarehouseTaskState]] = {
    WarehouseTaskState.OPEN: frozenset(
        {WarehouseTaskState.ASSIGNED, WarehouseTaskState.IN_PROGRESS, WarehouseTaskState.CANCELLED}
    ),
    WarehouseTaskState.ASSIGNED: frozenset(
        {WarehouseTaskState.IN_PROGRESS, WarehouseTaskState.OPEN, WarehouseTaskState.CANCELLED}
    ),
    WarehouseTaskState.IN_PROGRESS: frozenset(
        {WarehouseTaskState.COMPLETED, WarehouseTaskState.EXCEPTION, WarehouseTaskState.CANCELLED}
    ),
    WarehouseTaskState.EXCEPTION: frozenset(
        {WarehouseTaskState.OPEN, WarehouseTaskState.IN_PROGRESS, WarehouseTaskState.CANCELLED}
    ),
    WarehouseTaskState.COMPLETED: frozenset(),
    WarehouseTaskState.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class OrderLine:
    id: UUID
    product_id: UUID
    quantity: Decimal
    uom: str
    unit_price: Decimal
    currency: str
    received_or_shipped_quantity: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise InvalidQuantity("order line quantity must be positive")
        if self.unit_price < 0:
            raise InvalidQuantity("order line unit price cannot be negative")
        if self.received_or_shipped_quantity < 0:
            raise InvalidQuantity("processed quantity cannot be negative")
        if self.received_or_shipped_quantity > self.quantity:
            raise InvalidQuantity("processed quantity cannot exceed ordered quantity")
        if len(self.currency) != 3:
            raise InvalidQuantity("order line currency must be ISO 4217")

    @property
    def line_total(self) -> Decimal:
        return self.quantity * self.unit_price

    @property
    def open_quantity(self) -> Decimal:
        return self.quantity - self.received_or_shipped_quantity


@dataclass(frozen=True, slots=True)
class OperationalOrder:
    id: UUID
    organization_id: UUID
    kind: OrderKind
    order_number: str
    party_id: UUID
    warehouse_id: UUID
    state: str
    lines: tuple[OrderLine, ...]
    currency: str
    expected_on: date | None = None
    notes: str | None = None
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.lines:
            raise InvalidQuantity("an order requires at least one line")
        if any(line.currency != self.currency for line in self.lines):
            raise InvalidQuantity("all order lines must use the order currency")
        if len({line.id for line in self.lines}) != len(self.lines):
            raise DuplicateResource("order line IDs must be unique")

    @property
    def total(self) -> Decimal:
        return sum((line.line_total for line in self.lines), Decimal("0"))

    @property
    def workflow(self) -> Workflow:
        return Workflow.PURCHASE_ORDER if self.kind == OrderKind.PURCHASE else Workflow.SALES_ORDER

    def transition(self, target: str, organization_id: UUID, expected_version: int) -> "OperationalOrder":
        transitioned = WorkflowEntity(
            self.id, self.organization_id, self.workflow, self.state, self.version
        ).transition(target, organization_id=organization_id, expected_version=expected_version)
        return replace(
            self,
            state=transitioned.state,
            version=transitioned.version,
            updated_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class WarehouseTask:
    id: UUID
    organization_id: UUID
    task_number: str
    task_type: WarehouseTaskType
    warehouse_id: UUID
    state: WarehouseTaskState = WarehouseTaskState.OPEN
    source_location_id: UUID | None = None
    destination_location_id: UUID | None = None
    product_id: UUID | None = None
    quantity: Decimal | None = None
    uom: str | None = None
    reference_type: str | None = None
    reference_id: UUID | None = None
    assigned_to: UUID | None = None
    priority: int = 100
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.quantity is not None and self.quantity <= 0:
            raise InvalidQuantity("warehouse task quantity must be positive")
        if not 1 <= self.priority <= 999:
            raise InvalidQuantity("warehouse task priority must be between 1 and 999")

    def transition(
        self,
        target: WarehouseTaskState,
        organization_id: UUID,
        expected_version: int,
        assigned_to: UUID | None = None,
    ) -> "WarehouseTask":
        if organization_id != self.organization_id:
            raise TenantBoundaryViolation("warehouse task belongs to another organization")
        if expected_version != self.version:
            from .errors import ConcurrencyConflict

            raise ConcurrencyConflict("warehouse task version changed")
        if target not in TASK_TRANSITIONS[self.state]:
            raise InvalidStateTransition(f"cannot transition warehouse task from {self.state} to {target}")
        next_assignee = assigned_to if target == WarehouseTaskState.ASSIGNED else self.assigned_to
        if target == WarehouseTaskState.ASSIGNED and next_assignee is None:
            raise InvalidStateTransition("assigning a warehouse task requires an assignee")
        return replace(
            self,
            state=target,
            assigned_to=next_assignee,
            version=self.version + 1,
            updated_at=datetime.now(UTC),
        )


class OperationsStore(Protocol):
    def create_order(
        self, order: OperationalOrder, actor_id: UUID, correlation_id: UUID, idempotency_key: str
    ) -> tuple[OperationalOrder, bool]: ...

    def orders_for(
        self, organization_id: UUID, actor_id: UUID, kind: OrderKind
    ) -> list[OperationalOrder]: ...

    def order(
        self, organization_id: UUID, actor_id: UUID, kind: OrderKind, order_id: UUID
    ) -> OperationalOrder: ...

    def transition_order(
        self,
        organization_id: UUID,
        actor_id: UUID,
        kind: OrderKind,
        order_id: UUID,
        target: str,
        expected_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> tuple[OperationalOrder, bool]: ...

    def create_task(
        self, task: WarehouseTask, actor_id: UUID, correlation_id: UUID, idempotency_key: str
    ) -> tuple[WarehouseTask, bool]: ...

    def tasks_for(
        self, organization_id: UUID, actor_id: UUID, warehouse_id: UUID | None = None
    ) -> list[WarehouseTask]: ...

    def transition_task(
        self,
        organization_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        target: WarehouseTaskState,
        expected_version: int,
        correlation_id: UUID,
        idempotency_key: str,
        assigned_to: UUID | None = None,
    ) -> tuple[WarehouseTask, bool]: ...


class InMemoryOperationsStore:
    def __init__(self) -> None:
        self._orders: dict[tuple[UUID, OrderKind, UUID], OperationalOrder] = {}
        self._numbers: set[tuple[UUID, OrderKind, str]] = set()
        self._tasks: dict[tuple[UUID, UUID], WarehouseTask] = {}
        self._task_numbers: set[tuple[UUID, str]] = set()
        self._commands: dict[tuple[UUID, str], tuple[str, object]] = {}
        self._lock = RLock()

    @staticmethod
    def _hash(payload: dict[str, object]) -> str:
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    def _replay(self, organization_id: UUID, key: str, fingerprint: str) -> object | None:
        prior = self._commands.get((organization_id, key))
        if prior is None:
            return None
        if prior[0] != fingerprint:
            raise IdempotencyConflict("idempotency key was reused with a different command")
        return prior[1]

    def create_order(
        self, order: OperationalOrder, actor_id: UUID, correlation_id: UUID, idempotency_key: str
    ) -> tuple[OperationalOrder, bool]:
        del actor_id, correlation_id
        fingerprint = self._hash({"command": "create_order", "order": order})
        with self._lock:
            prior = self._replay(order.organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return prior, True  # type: ignore[return-value]
            number_key = (order.organization_id, order.kind, order.order_number.casefold())
            if number_key in self._numbers:
                raise DuplicateResource("order number already exists")
            self._orders[(order.organization_id, order.kind, order.id)] = order
            self._numbers.add(number_key)
            self._commands[(order.organization_id, idempotency_key)] = (fingerprint, order)
            return order, False

    def orders_for(
        self, organization_id: UUID, actor_id: UUID, kind: OrderKind
    ) -> list[OperationalOrder]:
        del actor_id
        with self._lock:
            return sorted(
                [
                    order
                    for (tenant_id, order_kind, _), order in self._orders.items()
                    if tenant_id == organization_id and order_kind == kind
                ],
                key=lambda item: (item.created_at, item.order_number),
                reverse=True,
            )

    def order(
        self, organization_id: UUID, actor_id: UUID, kind: OrderKind, order_id: UUID
    ) -> OperationalOrder:
        del actor_id
        with self._lock:
            result = self._orders.get((organization_id, kind, order_id))
            if result is None:
                raise ResourceNotFound("order not found")
            return result

    def transition_order(
        self,
        organization_id: UUID,
        actor_id: UUID,
        kind: OrderKind,
        order_id: UUID,
        target: str,
        expected_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> tuple[OperationalOrder, bool]:
        del actor_id, correlation_id
        fingerprint = self._hash(
            {
                "command": "transition_order",
                "kind": kind,
                "order_id": order_id,
                "target": target,
                "expected_version": expected_version,
            }
        )
        with self._lock:
            prior = self._replay(organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return prior, True  # type: ignore[return-value]
            current = self.order(organization_id, UUID(int=0), kind, order_id)
            transitioned = current.transition(target, organization_id, expected_version)
            self._orders[(organization_id, kind, order_id)] = transitioned
            self._commands[(organization_id, idempotency_key)] = (fingerprint, transitioned)
            return transitioned, False

    def create_task(
        self, task: WarehouseTask, actor_id: UUID, correlation_id: UUID, idempotency_key: str
    ) -> tuple[WarehouseTask, bool]:
        del actor_id, correlation_id
        fingerprint = self._hash({"command": "create_task", "task": task})
        with self._lock:
            prior = self._replay(task.organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return prior, True  # type: ignore[return-value]
            number_key = (task.organization_id, task.task_number.casefold())
            if number_key in self._task_numbers:
                raise DuplicateResource("warehouse task number already exists")
            self._tasks[(task.organization_id, task.id)] = task
            self._task_numbers.add(number_key)
            self._commands[(task.organization_id, idempotency_key)] = (fingerprint, task)
            return task, False

    def tasks_for(
        self, organization_id: UUID, actor_id: UUID, warehouse_id: UUID | None = None
    ) -> list[WarehouseTask]:
        del actor_id
        with self._lock:
            return sorted(
                [
                    task
                    for (tenant_id, _), task in self._tasks.items()
                    if tenant_id == organization_id
                    and (warehouse_id is None or task.warehouse_id == warehouse_id)
                ],
                key=lambda item: (item.priority, item.created_at, item.task_number),
            )

    def transition_task(
        self,
        organization_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        target: WarehouseTaskState,
        expected_version: int,
        correlation_id: UUID,
        idempotency_key: str,
        assigned_to: UUID | None = None,
    ) -> tuple[WarehouseTask, bool]:
        del actor_id, correlation_id
        fingerprint = self._hash(
            {
                "command": "transition_task",
                "task_id": task_id,
                "target": target,
                "expected_version": expected_version,
                "assigned_to": assigned_to,
            }
        )
        with self._lock:
            prior = self._replay(organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return prior, True  # type: ignore[return-value]
            current = self._tasks.get((organization_id, task_id))
            if current is None:
                raise ResourceNotFound("warehouse task not found")
            transitioned = current.transition(
                target, organization_id, expected_version, assigned_to
            )
            self._tasks[(organization_id, task_id)] = transitioned
            self._commands[(organization_id, idempotency_key)] = (fingerprint, transitioned)
            return transitioned, False
