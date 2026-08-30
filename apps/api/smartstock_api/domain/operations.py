from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from threading import RLock
from typing import Protocol
from uuid import UUID, uuid5

from .errors import (
    DuplicateResource,
    IdempotencyConflict,
    InvalidQuantity,
    InvalidStateTransition,
    ResourceNotFound,
    TenantBoundaryViolation,
)
from .inventory import (
    AdjustmentCommand,
    CountCommand,
    CountResult,
    ConsumeReservationCommand,
    InventoryStore,
    ReserveCommand,
    StockCondition,
    StockKey,
    TransferReceiptCommand,
    TransferReceiptResult,
    TransferShipmentCommand,
    TransferShipmentResult,
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
class ReceiptPostingLine:
    id: UUID
    order_line_id: UUID
    location_id: UUID
    accepted_quantity: Decimal
    rejected_quantity: Decimal = Decimal("0")
    expected_sellable_version: int = 0
    expected_quarantine_version: int = 0

    def __post_init__(self) -> None:
        if self.accepted_quantity < 0 or self.rejected_quantity < 0:
            raise InvalidQuantity("receipt quantities cannot be negative")
        if self.accepted_quantity + self.rejected_quantity <= 0:
            raise InvalidQuantity("a receipt line must contain a positive quantity")


@dataclass(frozen=True, slots=True)
class Receipt:
    id: UUID
    organization_id: UUID
    receipt_number: str
    purchase_order_id: UUID
    warehouse_id: UUID
    inventory_transaction_ids: tuple[UUID, ...]
    lines: tuple[ReceiptPostingLine, ...]
    state: str = "posted"
    version: int = 1
    posted_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ReceiptResult:
    receipt: Receipt
    order: OperationalOrder
    replayed: bool = False
    task: WarehouseTask | None = None
    follow_up_task: WarehouseTask | None = None


@dataclass(frozen=True, slots=True)
class AllocationPostingLine:
    id: UUID
    order_line_id: UUID
    location_id: UUID
    quantity: Decimal
    expected_position_version: int = 0

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise InvalidQuantity("allocation quantity must be positive")


@dataclass(frozen=True, slots=True)
class SalesAllocation:
    id: UUID
    organization_id: UUID
    sales_order_id: UUID
    warehouse_id: UUID
    reservation_ids: tuple[UUID, ...]
    lines: tuple[AllocationPostingLine, ...]
    state: str = "posted"
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class AllocationResult:
    allocation: SalesAllocation
    order: OperationalOrder
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ShipmentPostingLine:
    id: UUID
    order_line_id: UUID
    reservation_id: UUID
    expected_reservation_version: int = 1
    expected_position_version: int = 1


@dataclass(frozen=True, slots=True)
class Shipment:
    id: UUID
    organization_id: UUID
    sales_order_id: UUID
    warehouse_id: UUID
    inventory_transaction_ids: tuple[UUID, ...]
    lines: tuple[ShipmentPostingLine, ...]
    state: str = "shipped"
    version: int = 1
    shipped_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ShipmentResult:
    shipment: Shipment
    order: OperationalOrder
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ReturnLine:
    id: UUID
    order_line_id: UUID
    product_id: UUID
    quantity: Decimal
    uom: str
    reason_code: str
    received_quantity: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.quantity <= 0 or self.received_quantity < 0:
            raise InvalidQuantity("return quantities must be positive and nonnegative")
        if self.received_quantity > self.quantity:
            raise InvalidQuantity("received return quantity exceeds authorized quantity")


@dataclass(frozen=True, slots=True)
class ReturnAuthorization:
    id: UUID
    organization_id: UUID
    return_number: str
    sales_order_id: UUID
    warehouse_id: UUID
    state: str
    lines: tuple[ReturnLine, ...]
    notes: str | None = None
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def transition(self, target: str, organization_id: UUID,
                   expected_version: int) -> "ReturnAuthorization":
        changed = WorkflowEntity(
            self.id, self.organization_id, Workflow.RETURN, self.state, self.version
        ).transition(target, organization_id=organization_id, expected_version=expected_version)
        return replace(self, state=changed.state, version=changed.version,
                       updated_at=datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ReturnReceiptLine:
    return_line_id: UUID
    location_id: UUID
    expected_quarantine_version: int = 0


@dataclass(frozen=True, slots=True)
class ReturnReceiptResult:
    return_authorization: ReturnAuthorization
    inventory_transaction_ids: tuple[UUID, ...]
    replayed: bool = False


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
        if len(self.currency) != 3:
            raise InvalidQuantity("order line currency must be ISO 4217")

    @property
    def line_total(self) -> Decimal:
        return self.quantity * self.unit_price

    @property
    def open_quantity(self) -> Decimal:
        return max(self.quantity - self.received_or_shipped_quantity, Decimal("0"))


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
    destination_warehouse_id: UUID | None = None
    state: WarehouseTaskState = WarehouseTaskState.OPEN
    source_location_id: UUID | None = None
    destination_location_id: UUID | None = None
    product_id: UUID | None = None
    quantity: Decimal | None = None
    uom: str | None = None
    condition: StockCondition = StockCondition.SELLABLE
    ownership: str = "owned"
    lot_id: UUID | None = None
    serial_id: UUID | None = None
    expected_position_version: int | None = None
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
        if self.expected_position_version is not None and self.expected_position_version < 0:
            raise InvalidQuantity("expected inventory position version cannot be negative")
        if self.task_type == WarehouseTaskType.COUNT and (
            self.source_location_id is None
            or self.product_id is None
            or self.uom is None
            or self.expected_position_version is None
        ):
            raise InvalidStateTransition(
                "count tasks require a product, source location, UOM, and position version"
            )
        if (
            self.task_type == WarehouseTaskType.TRANSFER
            and self.state != WarehouseTaskState.CANCELLED
            and self.reference_type != "transfer_receipt"
            and (
                self.destination_warehouse_id is None
                or self.destination_warehouse_id == self.warehouse_id
                or self.source_location_id is None
                or self.destination_location_id is None
                or self.product_id is None
                or self.quantity is None
                or self.uom is None
                or self.expected_position_version is None
            )
        ):
            raise InvalidStateTransition(
                "transfer tasks require source/destination stock and a source position version"
            )

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


@dataclass(frozen=True, slots=True)
class WarehouseTaskCountResult:
    task: WarehouseTask
    count: CountResult
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class WarehouseTransferShipmentResult:
    task: WarehouseTask
    receipt_task: WarehouseTask
    shipment: TransferShipmentResult
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class WarehouseTransferReceiptResult:
    task: WarehouseTask
    receipt: TransferReceiptResult
    replayed: bool = False


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

    def task(
        self, organization_id: UUID, actor_id: UUID, task_id: UUID
    ) -> WarehouseTask: ...

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

    def complete_count_task(
        self,
        organization_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        counted_quantity: Decimal,
        expected_task_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> WarehouseTaskCountResult: ...

    def ship_transfer_task(
        self, organization_id: UUID, actor_id: UUID, task_id: UUID,
        expected_task_version: int, correlation_id: UUID, idempotency_key: str,
    ) -> WarehouseTransferShipmentResult: ...

    def receive_transfer_task(
        self, organization_id: UUID, actor_id: UUID, task_id: UUID,
        received_quantity: Decimal, expected_task_version: int,
        correlation_id: UUID, idempotency_key: str,
    ) -> WarehouseTransferReceiptResult: ...

    def post_receipt(
        self,
        organization_id: UUID,
        actor_id: UUID,
        purchase_order_id: UUID,
        receipt_id: UUID,
        receipt_number: str,
        lines: tuple[ReceiptPostingLine, ...],
        expected_order_version: int,
        over_receipt_tolerance_percent: Decimal,
        correlation_id: UUID,
        idempotency_key: str,
        task_id: UUID | None = None,
        expected_task_version: int | None = None,
    ) -> ReceiptResult: ...

    def allocate_sales_order(
        self,
        organization_id: UUID,
        actor_id: UUID,
        sales_order_id: UUID,
        allocation_id: UUID,
        lines: tuple[AllocationPostingLine, ...],
        expected_order_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> AllocationResult: ...

    def post_shipment(
        self, organization_id: UUID, actor_id: UUID, sales_order_id: UUID,
        shipment_id: UUID, lines: tuple[ShipmentPostingLine, ...],
        expected_order_version: int, correlation_id: UUID, idempotency_key: str,
    ) -> ShipmentResult: ...

    def create_return(
        self, item: ReturnAuthorization, actor_id: UUID, correlation_id: UUID,
        idempotency_key: str,
    ) -> tuple[ReturnAuthorization, bool]: ...

    def returns_for(self, organization_id: UUID, actor_id: UUID) -> list[ReturnAuthorization]: ...

    def return_record(
        self, organization_id: UUID, actor_id: UUID, return_id: UUID
    ) -> ReturnAuthorization: ...

    def transition_return(
        self, organization_id: UUID, actor_id: UUID, return_id: UUID, target: str,
        expected_version: int, correlation_id: UUID, idempotency_key: str,
    ) -> tuple[ReturnAuthorization, bool]: ...

    def receive_return(
        self, organization_id: UUID, actor_id: UUID, return_id: UUID,
        lines: tuple[ReturnReceiptLine, ...], expected_version: int,
        correlation_id: UUID, idempotency_key: str,
    ) -> ReturnReceiptResult: ...


class InMemoryOperationsStore:
    def __init__(self, inventory_store: InventoryStore | None = None) -> None:
        self._orders: dict[tuple[UUID, OrderKind, UUID], OperationalOrder] = {}
        self._numbers: set[tuple[UUID, OrderKind, str]] = set()
        self._tasks: dict[tuple[UUID, UUID], WarehouseTask] = {}
        self._task_numbers: set[tuple[UUID, str]] = set()
        self._commands: dict[tuple[UUID, str], tuple[str, object]] = {}
        self._receipts: dict[tuple[UUID, UUID], Receipt] = {}
        self._receipt_numbers: set[tuple[UUID, str]] = set()
        self._allocations: dict[tuple[UUID, UUID], SalesAllocation] = {}
        self._shipments: dict[tuple[UUID, UUID], Shipment] = {}
        self._returns: dict[tuple[UUID, UUID], ReturnAuthorization] = {}
        self._return_numbers: set[tuple[UUID, str]] = set()
        self._allocated_by_line: dict[tuple[UUID, UUID], Decimal] = {}
        self._inventory = inventory_store
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
            self._generate_task(transitioned)
            self._commands[(organization_id, idempotency_key)] = (fingerprint, transitioned)
            return transitioned, False

    def _generate_task(self, order: OperationalOrder) -> None:
        if order.kind == OrderKind.PURCHASE and order.state == "acknowledged":
            task_type = WarehouseTaskType.RECEIVE
            prefix = "RCV"
        elif order.kind == OrderKind.SALES and order.state == "allocated":
            task_type = WarehouseTaskType.PICK
            prefix = "PICK"
        else:
            return
        task_id = uuid5(order.id, task_type.value)
        if (order.organization_id, task_id) in self._tasks:
            return
        task = WarehouseTask(
            id=task_id,
            organization_id=order.organization_id,
            task_number=f"{prefix}-{order.order_number}",
            task_type=task_type,
            warehouse_id=order.warehouse_id,
            reference_type=f"{order.kind.value}_order",
            reference_id=order.id,
            priority=50,
        )
        self._tasks[(order.organization_id, task.id)] = task
        self._task_numbers.add((order.organization_id, task.task_number.casefold()))

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

    def task(
        self, organization_id: UUID, actor_id: UUID, task_id: UUID
    ) -> WarehouseTask:
        del actor_id
        with self._lock:
            task = self._tasks.get((organization_id, task_id))
            if task is None:
                raise ResourceNotFound("warehouse task not found")
            return task

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
            if (
                current.task_type in {
                    WarehouseTaskType.COUNT,
                    WarehouseTaskType.RECEIVE,
                    WarehouseTaskType.TRANSFER,
                }
                and target == WarehouseTaskState.COMPLETED
            ):
                raise InvalidStateTransition(
                    "physical receive, count, and transfer tasks require their posting command"
                )
            transitioned = current.transition(
                target, organization_id, expected_version, assigned_to
            )
            self._tasks[(organization_id, task_id)] = transitioned
            self._commands[(organization_id, idempotency_key)] = (fingerprint, transitioned)
            return transitioned, False

    def complete_count_task(
        self,
        organization_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        counted_quantity: Decimal,
        expected_task_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> WarehouseTaskCountResult:
        if self._inventory is None:
            raise InvalidStateTransition("inventory store is unavailable for count posting")
        fingerprint = self._hash(
            {
                "command": "complete_count_task",
                "task_id": task_id,
                "counted_quantity": counted_quantity,
                "expected_task_version": expected_task_version,
            }
        )
        with self._lock:
            prior = self._replay(organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return replace(prior, replayed=True)  # type: ignore[arg-type]
            current = self.task(organization_id, actor_id, task_id)
            if current.task_type != WarehouseTaskType.COUNT:
                raise InvalidStateTransition("only count tasks can post a cycle count")
            transitioned = current.transition(
                WarehouseTaskState.COMPLETED, organization_id, expected_task_version
            )
            assert current.product_id is not None
            assert current.source_location_id is not None
            assert current.uom is not None
            assert current.expected_position_version is not None
            count = self._inventory.post_count(
                CountCommand(
                    organization_id=organization_id,
                    actor_id=actor_id,
                    count_number=current.task_number,
                    stock_key=StockKey(
                        organization_id=organization_id,
                        product_id=current.product_id,
                        warehouse_id=current.warehouse_id,
                        location_id=current.source_location_id,
                        uom=current.uom,
                        condition=current.condition,
                        ownership=current.ownership,
                        lot_id=current.lot_id,
                        serial_id=current.serial_id,
                    ),
                    counted_quantity=counted_quantity,
                    expected_position_version=current.expected_position_version,
                    idempotency_key=f"warehouse-count:{task_id}:{idempotency_key}",
                    correlation_id=correlation_id,
                )
            )
            self._tasks[(organization_id, task_id)] = transitioned
            result = WarehouseTaskCountResult(transitioned, count)
            self._commands[(organization_id, idempotency_key)] = (fingerprint, result)
            return result

    def ship_transfer_task(
        self,
        organization_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        expected_task_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> WarehouseTransferShipmentResult:
        if self._inventory is None:
            raise InvalidStateTransition("inventory store is unavailable for transfer posting")
        fingerprint = self._hash(
            {"command": "ship_transfer_task", "task_id": task_id,
             "expected_task_version": expected_task_version}
        )
        with self._lock:
            prior = self._replay(organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return replace(prior, replayed=True)  # type: ignore[arg-type]
            current = self.task(organization_id, actor_id, task_id)
            if (
                current.task_type != WarehouseTaskType.TRANSFER
                or current.reference_type == "transfer_receipt"
            ):
                raise InvalidStateTransition("task is not a transfer shipment")
            transitioned = current.transition(
                WarehouseTaskState.COMPLETED, organization_id, expected_task_version
            )
            assert current.destination_warehouse_id is not None
            assert current.source_location_id is not None
            assert current.destination_location_id is not None
            assert current.product_id is not None
            assert current.quantity is not None
            assert current.uom is not None
            assert current.expected_position_version is not None
            transfer_id = uuid5(current.id, "staged-transfer")
            common = {
                "organization_id": organization_id,
                "product_id": current.product_id,
                "uom": current.uom,
                "condition": current.condition,
                "ownership": current.ownership,
                "lot_id": current.lot_id,
                "serial_id": current.serial_id,
            }
            shipment = self._inventory.ship_transfer(
                TransferShipmentCommand(
                    organization_id, actor_id, transfer_id, current.task_number,
                    StockKey(warehouse_id=current.warehouse_id,
                             location_id=current.source_location_id, **common),
                    StockKey(warehouse_id=current.destination_warehouse_id,
                             location_id=current.destination_location_id, **common),
                    current.quantity, current.expected_position_version,
                    f"warehouse-transfer-ship:{task_id}:{idempotency_key}", correlation_id,
                )
            )
            receipt_task = WarehouseTask(
                id=uuid5(transfer_id, "receipt-task"),
                organization_id=organization_id,
                task_number=f"RCV-{current.task_number}",
                task_type=WarehouseTaskType.TRANSFER,
                warehouse_id=current.destination_warehouse_id,
                source_location_id=current.source_location_id,
                destination_location_id=current.destination_location_id,
                product_id=current.product_id,
                quantity=current.quantity,
                uom=current.uom,
                condition=current.condition,
                ownership=current.ownership,
                lot_id=current.lot_id,
                serial_id=current.serial_id,
                expected_position_version=shipment.destination_position.version,
                reference_type="transfer_receipt",
                reference_id=transfer_id,
                priority=current.priority,
            )
            self._tasks[(organization_id, task_id)] = transitioned
            self._tasks[(organization_id, receipt_task.id)] = receipt_task
            self._task_numbers.add((organization_id, receipt_task.task_number.casefold()))
            result = WarehouseTransferShipmentResult(transitioned, receipt_task, shipment)
            self._commands[(organization_id, idempotency_key)] = (fingerprint, result)
            return result

    def receive_transfer_task(
        self,
        organization_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        received_quantity: Decimal,
        expected_task_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> WarehouseTransferReceiptResult:
        if self._inventory is None:
            raise InvalidStateTransition("inventory store is unavailable for transfer posting")
        fingerprint = self._hash(
            {"command": "receive_transfer_task", "task_id": task_id,
             "received_quantity": received_quantity,
             "expected_task_version": expected_task_version}
        )
        with self._lock:
            prior = self._replay(organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return replace(prior, replayed=True)  # type: ignore[arg-type]
            current = self.task(organization_id, actor_id, task_id)
            if (
                current.task_type != WarehouseTaskType.TRANSFER
                or current.reference_type != "transfer_receipt"
                or current.reference_id is None
                or current.expected_position_version is None
            ):
                raise InvalidStateTransition("task is not a transfer receipt")
            transitioned = current.transition(
                WarehouseTaskState.COMPLETED, organization_id, expected_task_version
            )
            receipt = self._inventory.receive_transfer(
                TransferReceiptCommand(
                    organization_id, actor_id, current.reference_id, received_quantity,
                    current.expected_position_version,
                    f"warehouse-transfer-receive:{task_id}:{idempotency_key}", correlation_id,
                )
            )
            self._tasks[(organization_id, task_id)] = transitioned
            result = WarehouseTransferReceiptResult(transitioned, receipt)
            self._commands[(organization_id, idempotency_key)] = (fingerprint, result)
            return result

    def post_receipt(
        self,
        organization_id: UUID,
        actor_id: UUID,
        purchase_order_id: UUID,
        receipt_id: UUID,
        receipt_number: str,
        lines: tuple[ReceiptPostingLine, ...],
        expected_order_version: int,
        over_receipt_tolerance_percent: Decimal,
        correlation_id: UUID,
        idempotency_key: str,
        task_id: UUID | None = None,
        expected_task_version: int | None = None,
    ) -> ReceiptResult:
        if self._inventory is None:
            raise InvalidStateTransition("inventory store is unavailable for receipt posting")
        if not lines:
            raise InvalidQuantity("a receipt requires at least one line")
        if over_receipt_tolerance_percent < 0 or over_receipt_tolerance_percent > 100:
            raise InvalidQuantity("over-receipt tolerance must be between 0 and 100 percent")
        if (task_id is None) != (expected_task_version is None):
            raise InvalidStateTransition(
                "task-bound receipts require both a task and its expected version"
            )
        fingerprint = self._hash(
            {"command": "receive_purchase_order_task" if task_id else "post_receipt",
             "order_id": purchase_order_id,
             "receipt_id": receipt_id, "number": receipt_number, "lines": lines,
             "expected_version": expected_order_version,
             "tolerance": over_receipt_tolerance_percent, "task_id": task_id,
             "expected_task_version": expected_task_version}
        )
        with self._lock:
            prior = self._replay(organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return replace(prior, replayed=True)  # type: ignore[arg-type]
            if (organization_id, receipt_number.casefold()) in self._receipt_numbers:
                raise DuplicateResource("receipt number already exists")
            current_task: WarehouseTask | None = None
            transitioned_task: WarehouseTask | None = None
            if task_id is not None:
                current_task = self.task(organization_id, actor_id, task_id)
                if (
                    current_task.task_type != WarehouseTaskType.RECEIVE
                    or current_task.reference_type != "purchase_order"
                    or current_task.reference_id != purchase_order_id
                ):
                    raise InvalidStateTransition("task is not for this purchase order receipt")
                assert expected_task_version is not None
                transitioned_task = current_task.transition(
                    WarehouseTaskState.COMPLETED,
                    organization_id,
                    expected_task_version,
                )
            order = self.order(organization_id, actor_id, OrderKind.PURCHASE, purchase_order_id)
            if order.version != expected_order_version:
                from .errors import ConcurrencyConflict
                raise ConcurrencyConflict("purchase order version changed")
            if order.state not in {"acknowledged", "partially_received"}:
                raise InvalidStateTransition("purchase order is not receivable")
            by_id = {line.id: line for line in order.lines}
            if len({line.order_line_id for line in lines}) != len(lines):
                raise DuplicateResource("receipt order lines must be unique")
            updated: dict[UUID, Decimal] = {}
            transaction_ids: list[UUID] = []
            for posting in lines:
                order_line = by_id.get(posting.order_line_id)
                if order_line is None:
                    raise ResourceNotFound("purchase order line not found")
                delivered = posting.accepted_quantity + posting.rejected_quantity
                maximum = order_line.quantity * (
                    Decimal("1") + over_receipt_tolerance_percent / Decimal("100")
                ) - order_line.received_or_shipped_quantity
                if delivered > maximum:
                    raise InvalidQuantity("receipt exceeds the configured over-receipt tolerance")
                updated[order_line.id] = order_line.received_or_shipped_quantity + delivered
                accepted_position_version: int | None = None
                for label, quantity, condition, version in (
                    ("accepted", posting.accepted_quantity, StockCondition.SELLABLE,
                     posting.expected_sellable_version),
                    ("rejected", posting.rejected_quantity, StockCondition.QUARANTINED,
                     posting.expected_quarantine_version),
                ):
                    if quantity <= 0:
                        continue
                    result = self._inventory.adjust(
                        AdjustmentCommand(
                            organization_id, actor_id,
                            StockKey(organization_id, order_line.product_id, order.warehouse_id,
                                     posting.location_id, order_line.uom, condition=condition),
                            quantity, f"purchase_receipt_{label}", receipt_number,
                            f"{idempotency_key}:{posting.id}:{label}", correlation_id, version,
                            unit_cost=order_line.unit_price, currency=order.currency,
                        )
                    )
                    transaction_ids.append(result.transaction.id)
                    if label == "accepted":
                        accepted_position_version = result.position.version
                if posting.accepted_quantity > 0:
                    task = WarehouseTask(
                        id=uuid5(receipt_id, f"putaway:{posting.id}"), organization_id=organization_id,
                        task_number=f"PUT-{receipt_number}-{str(posting.id)[:8]}",
                        task_type=WarehouseTaskType.PUTAWAY, warehouse_id=order.warehouse_id,
                        source_location_id=posting.location_id, product_id=order_line.product_id,
                        quantity=posting.accepted_quantity, uom=order_line.uom,
                        expected_position_version=accepted_position_version,
                        reference_type="receipt", reference_id=receipt_id, priority=40,
                    )
                    self._tasks[(organization_id, task.id)] = task
                    self._task_numbers.add((organization_id, task.task_number.casefold()))
            next_lines = tuple(
                replace(line, received_or_shipped_quantity=updated.get(
                    line.id, line.received_or_shipped_quantity)) for line in order.lines
            )
            target = "received" if all(
                line.open_quantity == 0 for line in next_lines
            ) else "partially_received"
            transitioned = order.transition(target, organization_id, expected_order_version)
            transitioned = replace(transitioned, lines=next_lines)
            self._orders[(organization_id, OrderKind.PURCHASE, order.id)] = transitioned
            follow_up_task: WarehouseTask | None = None
            if transitioned_task is not None:
                self._tasks[(organization_id, transitioned_task.id)] = transitioned_task
                if transitioned.state == "partially_received":
                    follow_up_task = WarehouseTask(
                        id=uuid5(receipt_id, "follow-up-receive"),
                        organization_id=organization_id,
                        task_number=f"RCV-{transitioned.order_number}-V{transitioned.version}",
                        task_type=WarehouseTaskType.RECEIVE,
                        warehouse_id=transitioned.warehouse_id,
                        reference_type="purchase_order",
                        reference_id=transitioned.id,
                        priority=current_task.priority if current_task else 50,
                    )
                    self._tasks[(organization_id, follow_up_task.id)] = follow_up_task
                    self._task_numbers.add(
                        (organization_id, follow_up_task.task_number.casefold())
                    )
            receipt = Receipt(receipt_id, organization_id, receipt_number, order.id,
                              order.warehouse_id, tuple(transaction_ids), lines)
            result = ReceiptResult(
                receipt,
                transitioned,
                task=transitioned_task,
                follow_up_task=follow_up_task,
            )
            self._receipts[(organization_id, receipt.id)] = receipt
            self._receipt_numbers.add((organization_id, receipt_number.casefold()))
            self._commands[(organization_id, idempotency_key)] = (fingerprint, result)
            return result

    def allocate_sales_order(
        self,
        organization_id: UUID,
        actor_id: UUID,
        sales_order_id: UUID,
        allocation_id: UUID,
        lines: tuple[AllocationPostingLine, ...],
        expected_order_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> AllocationResult:
        if self._inventory is None:
            raise InvalidStateTransition("inventory store is unavailable for allocation")
        if not lines:
            raise InvalidQuantity("an allocation requires at least one line")
        fingerprint = self._hash(
            {"command": "allocate_sales_order", "order_id": sales_order_id,
             "allocation_id": allocation_id, "lines": lines,
             "expected_version": expected_order_version}
        )
        with self._lock:
            prior = self._replay(organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return replace(prior, replayed=True)  # type: ignore[arg-type]
            order = self.order(organization_id, actor_id, OrderKind.SALES, sales_order_id)
            if order.version != expected_order_version:
                from .errors import ConcurrencyConflict
                raise ConcurrencyConflict("sales order version changed")
            if order.state not in {"confirmed", "partially_allocated", "backordered"}:
                raise InvalidStateTransition("sales order is not allocatable")
            if len({(line.order_line_id, line.location_id) for line in lines}) != len(lines):
                raise DuplicateResource("allocation position lines must be unique")
            by_id = {line.id: line for line in order.lines}
            requested: dict[UUID, Decimal] = {}
            for posting in lines:
                order_line = by_id.get(posting.order_line_id)
                if order_line is None:
                    raise ResourceNotFound("sales order line not found")
                prior_quantity = self._allocated_by_line.get(
                    (organization_id, order_line.id), Decimal("0")
                )
                next_quantity = requested.get(order_line.id, prior_quantity) + posting.quantity
                if next_quantity > order_line.quantity:
                    raise InvalidQuantity("allocation exceeds the sales order line quantity")
                requested[order_line.id] = next_quantity
            reservation_ids: list[UUID] = []
            for posting in lines:
                order_line = by_id[posting.order_line_id]
                reservation = self._inventory.reserve(
                    ReserveCommand(
                        organization_id, actor_id,
                        StockKey(organization_id, order_line.product_id, order.warehouse_id,
                                 posting.location_id, order_line.uom),
                        "sales_order_line", order_line.id, posting.quantity,
                        posting.expected_position_version,
                        f"{idempotency_key}:{posting.id}", correlation_id,
                    )
                ).reservation
                reservation_ids.append(reservation.id)
                task = WarehouseTask(
                    id=uuid5(allocation_id, f"pick:{posting.id}"),
                    organization_id=organization_id,
                    task_number=f"PICK-{order.order_number}-{str(posting.id)[:8]}",
                    task_type=WarehouseTaskType.PICK,
                    warehouse_id=order.warehouse_id,
                    source_location_id=posting.location_id,
                    product_id=order_line.product_id,
                    quantity=posting.quantity,
                    uom=order_line.uom,
                    reference_type="sales_allocation",
                    reference_id=allocation_id,
                    priority=30,
                )
                self._tasks[(organization_id, task.id)] = task
                self._task_numbers.add((organization_id, task.task_number.casefold()))
            self._allocated_by_line.update(
                {(organization_id, line_id): quantity for line_id, quantity in requested.items()}
            )
            fully_allocated = all(
                self._allocated_by_line.get((organization_id, line.id), Decimal("0"))
                >= line.quantity for line in order.lines
            )
            target = "allocated" if fully_allocated else "partially_allocated"
            transitioned = order.transition(target, organization_id, expected_order_version)
            self._orders[(organization_id, OrderKind.SALES, order.id)] = transitioned
            allocation = SalesAllocation(
                allocation_id, organization_id, order.id, order.warehouse_id,
                tuple(reservation_ids), lines,
            )
            result = AllocationResult(allocation, transitioned)
            self._allocations[(organization_id, allocation_id)] = allocation
            self._commands[(organization_id, idempotency_key)] = (fingerprint, result)
            return result

    def post_shipment(
        self, organization_id: UUID, actor_id: UUID, sales_order_id: UUID,
        shipment_id: UUID, lines: tuple[ShipmentPostingLine, ...],
        expected_order_version: int, correlation_id: UUID, idempotency_key: str,
    ) -> ShipmentResult:
        if self._inventory is None or not lines:
            raise InvalidStateTransition("shipment inventory or lines are unavailable")
        fingerprint = self._hash({"command": "post_shipment", "order_id": sales_order_id,
                                  "shipment_id": shipment_id, "lines": lines,
                                  "expected_version": expected_order_version})
        with self._lock:
            prior = self._replay(organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return replace(prior, replayed=True)  # type: ignore[arg-type]
            order = self.order(organization_id, actor_id, OrderKind.SALES, sales_order_id)
            if order.version != expected_order_version:
                from .errors import ConcurrencyConflict
                raise ConcurrencyConflict("sales order version changed")
            if order.state not in {"picking", "partially_shipped"}:
                raise InvalidStateTransition("sales order is not ready to ship")
            order_lines = {line.id: line for line in order.lines}
            shipped: dict[UUID, Decimal] = {}
            transaction_ids: list[UUID] = []
            for posting in lines:
                if posting.order_line_id not in order_lines:
                    raise ResourceNotFound("sales order line not found")
                consumed = self._inventory.consume_reservation(ConsumeReservationCommand(
                    organization_id, actor_id, posting.reservation_id,
                    posting.expected_reservation_version, posting.expected_position_version,
                    f"{idempotency_key}:{posting.id}", correlation_id,
                ))
                if consumed.reservation.source_id != posting.order_line_id:
                    raise TenantBoundaryViolation("reservation does not belong to the order line")
                shipped[posting.order_line_id] = (
                    shipped.get(posting.order_line_id, Decimal("0"))
                    + consumed.reservation.quantity
                )
                transaction_ids.append(consumed.transaction.id)
            next_lines = tuple(replace(
                line, received_or_shipped_quantity=line.received_or_shipped_quantity
                + shipped.get(line.id, Decimal("0"))
            ) for line in order.lines)
            if any(line.received_or_shipped_quantity > line.quantity for line in next_lines):
                raise InvalidQuantity("shipment exceeds ordered quantity")
            target = "shipped" if all(line.open_quantity == 0 for line in next_lines) \
                else "partially_shipped"
            transitioned = replace(
                order.transition(target, organization_id, expected_order_version), lines=next_lines
            )
            self._orders[(organization_id, OrderKind.SALES, order.id)] = transitioned
            shipment = Shipment(shipment_id, organization_id, order.id, order.warehouse_id,
                                tuple(transaction_ids), lines)
            result = ShipmentResult(shipment, transitioned)
            self._shipments[(organization_id, shipment_id)] = shipment
            self._commands[(organization_id, idempotency_key)] = (fingerprint, result)
            return result

    def create_return(
        self, item: ReturnAuthorization, actor_id: UUID, correlation_id: UUID,
        idempotency_key: str,
    ) -> tuple[ReturnAuthorization, bool]:
        del actor_id, correlation_id
        fingerprint = self._hash({"command": "create_return", "return": item})
        with self._lock:
            prior = self._replay(item.organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return prior, True  # type: ignore[return-value]
            number = (item.organization_id, item.return_number.casefold())
            if number in self._return_numbers:
                raise DuplicateResource("return number already exists")
            order = self.order(item.organization_id, UUID(int=0), OrderKind.SALES,
                               item.sales_order_id)
            if order.state not in {"shipped", "delivered", "closed"}:
                raise InvalidStateTransition("sales order is not returnable")
            by_id = {line.id: line for line in order.lines}
            if any(line.order_line_id not in by_id or line.quantity >
                   by_id[line.order_line_id].received_or_shipped_quantity for line in item.lines):
                raise InvalidQuantity("return exceeds shipped order quantity")
            self._returns[(item.organization_id, item.id)] = item
            self._return_numbers.add(number)
            self._commands[(item.organization_id, idempotency_key)] = (fingerprint, item)
            return item, False

    def returns_for(self, organization_id: UUID, actor_id: UUID) -> list[ReturnAuthorization]:
        del actor_id
        return [item for (tenant, _), item in self._returns.items()
                if tenant == organization_id]

    def return_record(self, organization_id: UUID, actor_id: UUID,
                      return_id: UUID) -> ReturnAuthorization:
        del actor_id
        item = self._returns.get((organization_id, return_id))
        if item is None:
            raise ResourceNotFound("return not found")
        return item

    def transition_return(
        self, organization_id: UUID, actor_id: UUID, return_id: UUID, target: str,
        expected_version: int, correlation_id: UUID, idempotency_key: str,
    ) -> tuple[ReturnAuthorization, bool]:
        del actor_id, correlation_id
        fingerprint = self._hash({"command": "transition_return", "id": return_id,
                                  "target": target, "version": expected_version})
        with self._lock:
            prior = self._replay(organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return prior, True  # type: ignore[return-value]
            item = self.return_record(organization_id, UUID(int=0), return_id)
            changed = item.transition(target, organization_id, expected_version)
            self._returns[(organization_id, return_id)] = changed
            if target == "authorized":
                task = WarehouseTask(
                    uuid5(return_id, "receive"), organization_id,
                    f"RMA-{item.return_number}", WarehouseTaskType.RECEIVE,
                    item.warehouse_id, reference_type="return", reference_id=return_id,
                    priority=35,
                )
                self._tasks[(organization_id, task.id)] = task
                self._task_numbers.add((organization_id, task.task_number.casefold()))
            self._commands[(organization_id, idempotency_key)] = (fingerprint, changed)
            return changed, False

    def receive_return(
        self, organization_id: UUID, actor_id: UUID, return_id: UUID,
        lines: tuple[ReturnReceiptLine, ...], expected_version: int,
        correlation_id: UUID, idempotency_key: str,
    ) -> ReturnReceiptResult:
        if self._inventory is None or not lines:
            raise InvalidQuantity("return receipt requires inventory and lines")
        fingerprint = self._hash({"command": "receive_return", "id": return_id,
                                  "lines": lines, "version": expected_version})
        with self._lock:
            prior = self._replay(organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return replace(prior, replayed=True)  # type: ignore[arg-type]
            item = self.return_record(organization_id, actor_id, return_id)
            if item.state != "authorized" or item.version != expected_version:
                raise InvalidStateTransition("return is not authorized at the expected version")
            by_id = {line.id: line for line in item.lines}
            transactions: list[UUID] = []
            updated: dict[UUID, Decimal] = {}
            for posting in lines:
                line = by_id.get(posting.return_line_id)
                if line is None:
                    raise ResourceNotFound("return line not found")
                quantity = line.quantity - line.received_quantity
                result = self._inventory.adjust(AdjustmentCommand(
                    organization_id, actor_id,
                    StockKey(organization_id, line.product_id, item.warehouse_id,
                             posting.location_id, line.uom,
                             condition=StockCondition.QUARANTINED),
                    quantity, "return_receipt", item.return_number,
                    f"{idempotency_key}:{line.id}", correlation_id,
                    posting.expected_quarantine_version,
                ))
                transactions.append(result.transaction.id)
                updated[line.id] = line.quantity
            next_lines = tuple(replace(line, received_quantity=updated.get(
                line.id, line.received_quantity)) for line in item.lines)
            if not all(line.received_quantity == line.quantity for line in next_lines):
                raise InvalidQuantity("all authorized return lines must be received together")
            changed = replace(item.transition("received", organization_id, expected_version),
                              lines=next_lines)
            self._returns[(organization_id, return_id)] = changed
            result = ReturnReceiptResult(changed, tuple(transactions))
            self._commands[(organization_id, idempotency_key)] = (fingerprint, result)
            return result
