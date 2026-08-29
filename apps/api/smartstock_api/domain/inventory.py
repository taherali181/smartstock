from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from threading import RLock
from typing import Iterable, Protocol
from uuid import UUID, uuid4

from .errors import (
    ConcurrencyConflict,
    IdempotencyConflict,
    InsufficientStock,
    TenantBoundaryViolation,
    UnbalancedPosting,
)

ZERO = Decimal("0")


class InventoryAccount(StrEnum):
    ON_HAND = "on_hand"
    IN_TRANSIT = "in_transit"
    EXTERNAL = "external"
    DISCREPANCY = "discrepancy"


class StockCondition(StrEnum):
    SELLABLE = "sellable"
    QUARANTINED = "quarantined"
    DAMAGED = "damaged"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class StockKey:
    organization_id: UUID
    product_id: UUID
    warehouse_id: UUID
    location_id: UUID
    uom: str
    condition: StockCondition = StockCondition.SELLABLE
    lot_id: UUID | None = None
    serial_id: UUID | None = None
    ownership: str = "owned"


@dataclass(frozen=True, slots=True)
class LedgerLine:
    account: InventoryAccount
    quantity: Decimal
    stock_key: StockKey | None = None


@dataclass(frozen=True, slots=True)
class LedgerTransaction:
    id: UUID
    organization_id: UUID
    actor_id: UUID
    reason_code: str
    business_reference: str
    idempotency_key: str
    occurred_at: datetime
    lines: tuple[LedgerLine, ...]


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    id: UUID
    organization_id: UUID
    topic: str
    aggregate_id: UUID
    correlation_id: UUID
    occurred_at: datetime
    payload: dict[str, str]


@dataclass(slots=True)
class InventoryPosition:
    key: StockKey
    on_hand: Decimal = ZERO
    reserved: Decimal = ZERO
    version: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def available(self) -> Decimal:
        if self.key.condition != StockCondition.SELLABLE:
            return ZERO
        return self.on_hand - self.reserved


@dataclass(frozen=True, slots=True)
class AdjustmentCommand:
    organization_id: UUID
    actor_id: UUID
    stock_key: StockKey
    quantity_delta: Decimal
    reason_code: str
    business_reference: str
    idempotency_key: str
    correlation_id: UUID
    expected_version: int
    allow_negative: bool = False

    def fingerprint(self) -> str:
        values = (
            self.organization_id,
            self.actor_id,
            self.stock_key,
            self.quantity_delta,
            self.reason_code,
            self.business_reference,
            self.expected_version,
            self.allow_negative,
        )
        return sha256(repr(values).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class AdjustmentResult:
    transaction: LedgerTransaction
    position: InventoryPosition
    replayed: bool


class InventoryStore(Protocol):
    def positions_for(
        self, organization_id: UUID, actor_id: UUID | None = None
    ) -> list[InventoryPosition]: ...

    def adjust(self, command: AdjustmentCommand) -> AdjustmentResult: ...


def assert_balanced(lines: Iterable[LedgerLine]) -> tuple[LedgerLine, ...]:
    materialized = tuple(lines)
    if len(materialized) < 2 or sum((line.quantity for line in materialized), ZERO) != ZERO:
        raise UnbalancedPosting("inventory transactions require at least two balanced lines")
    return materialized


class InventoryLedger:
    """Reference transaction boundary.

    The PostgreSQL adapter uses the same rules under row locks. This in-process
    implementation powers deterministic unit tests and explicit development mode.
    """

    def __init__(self) -> None:
        self._positions: dict[StockKey, InventoryPosition] = {}
        self._transactions: list[LedgerTransaction] = []
        self._events: list[OutboxEvent] = []
        self._idempotency: dict[tuple[UUID, str], tuple[str, AdjustmentResult]] = {}
        self._lock = RLock()

    def position(self, key: StockKey) -> InventoryPosition:
        with self._lock:
            current = self._positions.get(key)
            return self._snapshot(current or InventoryPosition(key=key))

    def positions_for(
        self, organization_id: UUID, actor_id: UUID | None = None
    ) -> list[InventoryPosition]:
        with self._lock:
            return [
                self._snapshot(position)
                for key, position in self._positions.items()
                if key.organization_id == organization_id
            ]

    def adjust(self, command: AdjustmentCommand) -> AdjustmentResult:
        if command.stock_key.organization_id != command.organization_id:
            raise TenantBoundaryViolation("stock key belongs to a different organization")
        if command.quantity_delta == ZERO:
            raise UnbalancedPosting("a zero adjustment is not a business transaction")

        idempotency_scope = (command.organization_id, command.idempotency_key)
        fingerprint = command.fingerprint()
        with self._lock:
            prior = self._idempotency.get(idempotency_scope)
            if prior:
                if prior[0] != fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different command")
                result = prior[1]
                return AdjustmentResult(result.transaction, self._snapshot(result.position), True)

            current = self._positions.get(command.stock_key)
            current_version = current.version if current else 0
            current_on_hand = current.on_hand if current else ZERO
            current_reserved = current.reserved if current else ZERO
            if current_version != command.expected_version:
                raise ConcurrencyConflict(
                    f"expected position version {command.expected_version}, got {current_version}"
                )
            next_on_hand = current_on_hand + command.quantity_delta
            if not command.allow_negative and next_on_hand < current_reserved:
                raise InsufficientStock("adjustment would reduce sellable stock below reservations")

            lines = assert_balanced(
                (
                    LedgerLine(InventoryAccount.ON_HAND, command.quantity_delta, command.stock_key),
                    LedgerLine(InventoryAccount.EXTERNAL, -command.quantity_delta),
                )
            )
            now = datetime.now(UTC)
            transaction = LedgerTransaction(
                id=uuid4(),
                organization_id=command.organization_id,
                actor_id=command.actor_id,
                reason_code=command.reason_code,
                business_reference=command.business_reference,
                idempotency_key=command.idempotency_key,
                occurred_at=now,
                lines=lines,
            )
            position = InventoryPosition(
                key=command.stock_key,
                on_hand=next_on_hand,
                reserved=current_reserved,
                version=current_version + 1,
                updated_at=now,
            )
            event = OutboxEvent(
                id=uuid4(),
                organization_id=command.organization_id,
                topic="inventory.ledger_posted",
                aggregate_id=transaction.id,
                correlation_id=command.correlation_id,
                occurred_at=now,
                payload={
                    "transaction_id": str(transaction.id),
                    "product_id": str(command.stock_key.product_id),
                    "quantity_delta": str(command.quantity_delta),
                    "position_version": str(position.version),
                },
            )
            self._transactions.append(transaction)
            self._positions[command.stock_key] = position
            self._events.append(event)
            result = AdjustmentResult(transaction, self._snapshot(position), False)
            self._idempotency[idempotency_scope] = (fingerprint, result)
            return result

    @staticmethod
    def _snapshot(position: InventoryPosition) -> InventoryPosition:
        return InventoryPosition(
            key=position.key,
            on_hand=position.on_hand,
            reserved=position.reserved,
            version=position.version,
            updated_at=position.updated_at,
        )

    @property
    def outbox_events(self) -> tuple[OutboxEvent, ...]:
        return tuple(self._events)
