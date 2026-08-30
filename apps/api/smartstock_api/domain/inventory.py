from __future__ import annotations

from dataclasses import dataclass, field, replace
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
    InvalidQuantity,
    ResourceNotFound,
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


class ReservationStatus(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"
    CONSUMED = "consumed"
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
    average_unit_cost: Decimal = ZERO
    inventory_value: Decimal = ZERO
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
    unit_cost: Decimal | None = None
    currency: str | None = None

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
            self.unit_cost,
            self.currency,
        )
        return sha256(repr(values).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class AdjustmentResult:
    transaction: LedgerTransaction
    position: InventoryPosition
    replayed: bool


@dataclass(frozen=True, slots=True)
class Reservation:
    id: UUID
    organization_id: UUID
    stock_key: StockKey
    source_type: str
    source_id: UUID
    quantity: Decimal
    status: ReservationStatus
    version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReserveCommand:
    organization_id: UUID
    actor_id: UUID
    stock_key: StockKey
    source_type: str
    source_id: UUID
    quantity: Decimal
    expected_position_version: int
    idempotency_key: str
    correlation_id: UUID

    def fingerprint(self) -> str:
        return sha256(repr(self).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ReservationResult:
    reservation: Reservation
    position: InventoryPosition
    replayed: bool


@dataclass(frozen=True, slots=True)
class ConsumeReservationCommand:
    organization_id: UUID
    actor_id: UUID
    reservation_id: UUID
    expected_reservation_version: int
    expected_position_version: int
    idempotency_key: str
    correlation_id: UUID

    def fingerprint(self) -> str:
        return sha256(repr(self).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ConsumptionResult:
    reservation: Reservation
    position: InventoryPosition
    transaction: LedgerTransaction
    replayed: bool


@dataclass(frozen=True, slots=True)
class ReleaseReservationCommand:
    organization_id: UUID
    actor_id: UUID
    reservation_id: UUID
    expected_reservation_version: int
    idempotency_key: str
    correlation_id: UUID

    def fingerprint(self) -> str:
        return sha256(repr(self).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    stock_key: StockKey
    projected_on_hand: Decimal
    ledger_on_hand: Decimal
    projected_reserved: Decimal
    reservation_total: Decimal

    @property
    def reconciled(self) -> bool:
        return (
            self.projected_on_hand == self.ledger_on_hand
            and self.projected_reserved == self.reservation_total
        )


@dataclass(frozen=True, slots=True)
class TransferCommand:
    organization_id: UUID
    actor_id: UUID
    transfer_number: str
    source_key: StockKey
    destination_key: StockKey
    quantity: Decimal
    expected_source_version: int
    expected_destination_version: int
    idempotency_key: str
    correlation_id: UUID

    def fingerprint(self) -> str:
        return sha256(repr(self).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class TransferResult:
    transfer_id: UUID
    transaction: LedgerTransaction
    source_position: InventoryPosition
    destination_position: InventoryPosition
    replayed: bool


@dataclass(frozen=True, slots=True)
class TransferShipmentCommand:
    organization_id: UUID
    actor_id: UUID
    transfer_id: UUID
    transfer_number: str
    source_key: StockKey
    destination_key: StockKey
    quantity: Decimal
    expected_source_version: int
    idempotency_key: str
    correlation_id: UUID

    def fingerprint(self) -> str:
        return sha256(repr(self).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class TransferShipmentResult:
    transfer_id: UUID
    transaction: LedgerTransaction
    source_position: InventoryPosition
    destination_position: InventoryPosition
    quantity: Decimal
    replayed: bool


@dataclass(frozen=True, slots=True)
class TransferReceiptCommand:
    organization_id: UUID
    actor_id: UUID
    transfer_id: UUID
    received_quantity: Decimal
    expected_destination_version: int
    idempotency_key: str
    correlation_id: UUID

    def fingerprint(self) -> str:
        return sha256(repr(self).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class TransferReceiptResult:
    transfer_id: UUID
    transaction: LedgerTransaction
    destination_position: InventoryPosition
    shipped_quantity: Decimal
    received_quantity: Decimal
    discrepancy_quantity: Decimal
    state: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class StagedTransferRecord:
    command: TransferShipmentCommand
    unit_cost: Decimal
    received: bool = False


@dataclass(frozen=True, slots=True)
class CountCommand:
    organization_id: UUID
    actor_id: UUID
    count_number: str
    stock_key: StockKey
    counted_quantity: Decimal
    expected_position_version: int
    idempotency_key: str
    correlation_id: UUID

    def fingerprint(self) -> str:
        return sha256(repr(self).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CountResult:
    cycle_count_id: UUID
    transaction: LedgerTransaction | None
    snapshot_quantity: Decimal
    counted_quantity: Decimal
    variance_quantity: Decimal
    position: InventoryPosition
    replayed: bool


class InventoryStore(Protocol):
    def positions_for(
        self, organization_id: UUID, actor_id: UUID | None = None
    ) -> list[InventoryPosition]: ...

    def adjust(self, command: AdjustmentCommand) -> AdjustmentResult: ...

    def reserve(self, command: ReserveCommand) -> ReservationResult: ...

    def release_reservation(self, command: ReleaseReservationCommand) -> ReservationResult: ...

    def consume_reservation(self, command: ConsumeReservationCommand) -> ConsumptionResult: ...

    def reconcile(self, organization_id: UUID, actor_id: UUID) -> list[ReconciliationResult]: ...

    def transfer(self, command: TransferCommand) -> TransferResult: ...

    def ship_transfer(self, command: TransferShipmentCommand) -> TransferShipmentResult: ...

    def receive_transfer(self, command: TransferReceiptCommand) -> TransferReceiptResult: ...

    def post_count(self, command: CountCommand) -> CountResult: ...


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
        self._reservation_idempotency: dict[
            tuple[UUID, str], tuple[str, ReservationResult]
        ] = {}
        self._consumption_idempotency: dict[
            tuple[UUID, str], tuple[str, ConsumptionResult]
        ] = {}
        self._reservations: dict[tuple[UUID, UUID], Reservation] = {}
        self._transfer_idempotency: dict[tuple[UUID, str], tuple[str, TransferResult]] = {}
        self._shipment_idempotency: dict[
            tuple[UUID, str], tuple[str, TransferShipmentResult]
        ] = {}
        self._receipt_idempotency: dict[
            tuple[UUID, str], tuple[str, TransferReceiptResult]
        ] = {}
        self._staged_transfers: dict[tuple[UUID, UUID], StagedTransferRecord] = {}
        self._count_idempotency: dict[tuple[UUID, str], tuple[str, CountResult]] = {}
        self._command_fingerprints: dict[tuple[UUID, str], str] = {}
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
        if command.unit_cost is not None and command.unit_cost < ZERO:
            raise InvalidQuantity("unit cost cannot be negative")

        idempotency_scope = (command.organization_id, command.idempotency_key)
        fingerprint = command.fingerprint()
        with self._lock:
            prior = self._idempotency.get(idempotency_scope)
            if idempotency_scope in self._command_fingerprints and prior is None:
                raise IdempotencyConflict("idempotency key was already used by another command")
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
            if command.stock_key.serial_id is not None and next_on_hand not in (ZERO, Decimal("1")):
                raise InvalidQuantity("a serial-number position must contain zero or one unit")
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
                average_unit_cost=self._next_average_cost(
                    current_on_hand,
                    current.average_unit_cost if current else ZERO,
                    command.quantity_delta,
                    command.unit_cost,
                ),
                version=current_version + 1,
                updated_at=now,
            )
            position.inventory_value = position.on_hand * position.average_unit_cost
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
            self._command_fingerprints[idempotency_scope] = fingerprint
            return result

    def reserve(self, command: ReserveCommand) -> ReservationResult:
        if command.stock_key.organization_id != command.organization_id:
            raise TenantBoundaryViolation("stock key belongs to a different organization")
        if command.quantity <= ZERO:
            raise UnbalancedPosting("reservation quantity must be positive")
        if command.stock_key.condition != StockCondition.SELLABLE:
            raise InsufficientStock("only sellable stock can be reserved")
        scope = (command.organization_id, command.idempotency_key)
        fingerprint = command.fingerprint()
        with self._lock:
            prior = self._reservation_idempotency.get(scope)
            if scope in self._command_fingerprints and prior is None:
                raise IdempotencyConflict("idempotency key was already used by another command")
            if prior:
                if prior[0] != fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different command")
                result = prior[1]
                return ReservationResult(
                    result.reservation, self._snapshot(result.position), replayed=True
                )
            current = self._positions.get(command.stock_key)
            if current is None or current.version != command.expected_position_version:
                actual = 0 if current is None else current.version
                raise ConcurrencyConflict(
                    f"expected position version {command.expected_position_version}, got {actual}"
                )
            if current.available < command.quantity:
                raise InsufficientStock("reservation exceeds available sellable inventory")
            now = datetime.now(UTC)
            reservation = Reservation(
                id=uuid4(),
                organization_id=command.organization_id,
                stock_key=command.stock_key,
                source_type=command.source_type,
                source_id=command.source_id,
                quantity=command.quantity,
                status=ReservationStatus.ACTIVE,
                version=1,
                created_at=now,
            )
            position = self._snapshot(current)
            position.reserved += command.quantity
            position.version += 1
            position.updated_at = now
            self._positions[command.stock_key] = position
            self._reservations[(command.organization_id, reservation.id)] = reservation
            self._events.append(
                OutboxEvent(
                    id=uuid4(),
                    organization_id=command.organization_id,
                    topic="inventory.reservation_created",
                    aggregate_id=reservation.id,
                    correlation_id=command.correlation_id,
                    occurred_at=now,
                    payload={
                        "reservation_id": str(reservation.id),
                        "quantity": str(reservation.quantity),
                        "position_version": str(position.version),
                    },
                )
            )
            result = ReservationResult(reservation, self._snapshot(position), replayed=False)
            self._reservation_idempotency[scope] = (fingerprint, result)
            self._command_fingerprints[scope] = fingerprint
            return result

    def consume_reservation(self, command: ConsumeReservationCommand) -> ConsumptionResult:
        scope = (command.organization_id, command.idempotency_key)
        fingerprint = command.fingerprint()
        with self._lock:
            prior = self._consumption_idempotency.get(scope)
            if prior:
                if prior[0] != fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different command")
                result = prior[1]
                return ConsumptionResult(
                    result.reservation, self._snapshot(result.position), result.transaction, True
                )
            reservation = self._reservations.get((command.organization_id, command.reservation_id))
            if reservation is None:
                from .errors import ResourceNotFound
                raise ResourceNotFound("reservation not found")
            position = self._positions[reservation.stock_key]
            if reservation.version != command.expected_reservation_version:
                raise ConcurrencyConflict("reservation version changed")
            if position.version != command.expected_position_version:
                raise ConcurrencyConflict("inventory position version changed")
            if reservation.status != ReservationStatus.ACTIVE:
                raise ConcurrencyConflict("reservation is no longer active")
            now = datetime.now(UTC)
            consumed = replace(reservation, status=ReservationStatus.CONSUMED,
                               version=reservation.version + 1)
            next_position = self._snapshot(position)
            next_position.on_hand -= reservation.quantity
            next_position.reserved -= reservation.quantity
            next_position.version += 1
            next_position.updated_at = now
            next_position.inventory_value = (
                next_position.on_hand * next_position.average_unit_cost
            )
            transaction = LedgerTransaction(
                uuid4(), command.organization_id, command.actor_id, "shipment",
                str(reservation.source_id), command.idempotency_key, now,
                assert_balanced((
                    LedgerLine(InventoryAccount.ON_HAND, -reservation.quantity,
                               reservation.stock_key),
                    LedgerLine(InventoryAccount.EXTERNAL, reservation.quantity),
                )),
            )
            self._reservations[(command.organization_id, reservation.id)] = consumed
            self._positions[position.key] = next_position
            self._transactions.append(transaction)
            self._events.append(OutboxEvent(
                uuid4(), command.organization_id, "inventory.ledger_posted", transaction.id,
                command.correlation_id, now,
                {"transaction_id": str(transaction.id), "quantity_delta": str(-reservation.quantity)},
            ))
            result = ConsumptionResult(consumed, self._snapshot(next_position), transaction, False)
            self._consumption_idempotency[scope] = (fingerprint, result)
            self._command_fingerprints[scope] = fingerprint
            return result

    def release_reservation(self, command: ReleaseReservationCommand) -> ReservationResult:
        scope = (command.organization_id, command.idempotency_key)
        fingerprint = command.fingerprint()
        with self._lock:
            prior = self._reservation_idempotency.get(scope)
            if scope in self._command_fingerprints and prior is None:
                raise IdempotencyConflict("idempotency key was already used by another command")
            if prior:
                if prior[0] != fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different command")
                result = prior[1]
                return ReservationResult(
                    result.reservation, self._snapshot(result.position), replayed=True
                )
            current_reservation = self._reservations.get(
                (command.organization_id, command.reservation_id)
            )
            if current_reservation is None:
                from .errors import ResourceNotFound

                raise ResourceNotFound("reservation not found")
            if current_reservation.version != command.expected_reservation_version:
                raise ConcurrencyConflict(
                    "reservation version does not match the approval snapshot"
                )
            if current_reservation.status != ReservationStatus.ACTIVE:
                raise ConcurrencyConflict("reservation is no longer active")
            current_position = self._positions[current_reservation.stock_key]
            now = datetime.now(UTC)
            reservation = Reservation(
                id=current_reservation.id,
                organization_id=current_reservation.organization_id,
                stock_key=current_reservation.stock_key,
                source_type=current_reservation.source_type,
                source_id=current_reservation.source_id,
                quantity=current_reservation.quantity,
                status=ReservationStatus.RELEASED,
                version=current_reservation.version + 1,
                created_at=current_reservation.created_at,
            )
            position = self._snapshot(current_position)
            position.reserved -= reservation.quantity
            position.version += 1
            position.updated_at = now
            self._positions[position.key] = position
            self._reservations[(command.organization_id, reservation.id)] = reservation
            self._events.append(
                OutboxEvent(
                    id=uuid4(),
                    organization_id=command.organization_id,
                    topic="inventory.reservation_released",
                    aggregate_id=reservation.id,
                    correlation_id=command.correlation_id,
                    occurred_at=now,
                    payload={
                        "reservation_id": str(reservation.id),
                        "position_version": str(position.version),
                    },
                )
            )
            result = ReservationResult(reservation, self._snapshot(position), replayed=False)
            self._reservation_idempotency[scope] = (fingerprint, result)
            self._command_fingerprints[scope] = fingerprint
            return result

    def reconcile(self, organization_id: UUID, actor_id: UUID) -> list[ReconciliationResult]:
        del actor_id
        with self._lock:
            results: list[ReconciliationResult] = []
            for key, position in self._positions.items():
                if key.organization_id != organization_id:
                    continue
                ledger_total = sum(
                    (
                        line.quantity
                        for transaction in self._transactions
                        if transaction.organization_id == organization_id
                        for line in transaction.lines
                        if line.account == InventoryAccount.ON_HAND and line.stock_key == key
                    ),
                    ZERO,
                )
                reservation_total = sum(
                    (
                        reservation.quantity
                        for (tenant_id, _), reservation in self._reservations.items()
                        if tenant_id == organization_id
                        and reservation.stock_key == key
                        and reservation.status == ReservationStatus.ACTIVE
                    ),
                    ZERO,
                )
                results.append(
                    ReconciliationResult(
                        stock_key=key,
                        projected_on_hand=position.on_hand,
                        ledger_on_hand=ledger_total,
                        projected_reserved=position.reserved,
                        reservation_total=reservation_total,
                    )
                )
            return results

    def transfer(self, command: TransferCommand) -> TransferResult:
        self._validate_transfer(command)
        scope = (command.organization_id, command.idempotency_key)
        fingerprint = command.fingerprint()
        with self._lock:
            prior = self._transfer_idempotency.get(scope)
            if scope in self._command_fingerprints and prior is None:
                raise IdempotencyConflict("idempotency key was already used by another command")
            if prior:
                if prior[0] != fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different command")
                result = prior[1]
                return TransferResult(
                    result.transfer_id,
                    result.transaction,
                    self._snapshot(result.source_position),
                    self._snapshot(result.destination_position),
                    True,
                )
            source = self._positions.get(command.source_key)
            destination = self._positions.get(command.destination_key)
            source_version = source.version if source else 0
            destination_version = destination.version if destination else 0
            if source_version != command.expected_source_version:
                raise ConcurrencyConflict(
                    f"expected source version {command.expected_source_version}, got {source_version}"
                )
            if destination_version != command.expected_destination_version:
                raise ConcurrencyConflict(
                    "destination position changed after the transfer snapshot"
                )
            if source is None or source.available < command.quantity:
                raise InsufficientStock("transfer exceeds available source inventory")
            now = datetime.now(UTC)
            destination = destination or InventoryPosition(command.destination_key)
            source_next = self._snapshot(source)
            destination_next = self._snapshot(destination)
            moved_value = command.quantity * source.average_unit_cost
            source_next.on_hand -= command.quantity
            source_next.inventory_value -= moved_value
            source_next.average_unit_cost = (
                source_next.inventory_value / source_next.on_hand if source_next.on_hand else ZERO
            )
            source_next.version += 1
            source_next.updated_at = now
            destination_next.on_hand += command.quantity
            destination_next.inventory_value += moved_value
            destination_next.average_unit_cost = (
                destination_next.inventory_value / destination_next.on_hand
            )
            destination_next.version += 1
            destination_next.updated_at = now
            lines = assert_balanced(
                (
                    LedgerLine(InventoryAccount.ON_HAND, -command.quantity, command.source_key),
                    LedgerLine(InventoryAccount.IN_TRANSIT, command.quantity, command.source_key),
                    LedgerLine(
                        InventoryAccount.IN_TRANSIT, -command.quantity, command.destination_key
                    ),
                    LedgerLine(
                        InventoryAccount.ON_HAND, command.quantity, command.destination_key
                    ),
                )
            )
            transaction = LedgerTransaction(
                uuid4(),
                command.organization_id,
                command.actor_id,
                "transfer",
                command.transfer_number,
                command.idempotency_key,
                now,
                lines,
            )
            transfer_id = uuid4()
            self._transactions.append(transaction)
            self._positions[command.source_key] = source_next
            self._positions[command.destination_key] = destination_next
            self._events.extend(
                (
                    OutboxEvent(
                        uuid4(), command.organization_id, "transfer.shipped", transfer_id,
                        command.correlation_id, now,
                        {"transfer_id": str(transfer_id), "quantity": str(command.quantity)},
                    ),
                    OutboxEvent(
                        uuid4(), command.organization_id, "transfer.received", transfer_id,
                        command.correlation_id, now,
                        {"transfer_id": str(transfer_id), "quantity": str(command.quantity)},
                    ),
                )
            )
            result = TransferResult(
                transfer_id,
                transaction,
                self._snapshot(source_next),
                self._snapshot(destination_next),
                False,
            )
            self._transfer_idempotency[scope] = (fingerprint, result)
            self._command_fingerprints[scope] = fingerprint
            return result

    def ship_transfer(self, command: TransferShipmentCommand) -> TransferShipmentResult:
        self._validate_transfer_dimensions(
            command.organization_id,
            command.source_key,
            command.destination_key,
            command.quantity,
        )
        scope = (command.organization_id, command.idempotency_key)
        fingerprint = command.fingerprint()
        with self._lock:
            prior = self._shipment_idempotency.get(scope)
            if scope in self._command_fingerprints and prior is None:
                raise IdempotencyConflict("idempotency key was already used by another command")
            if prior:
                if prior[0] != fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different command")
                result = prior[1]
                return replace(
                    result,
                    source_position=self._snapshot(result.source_position),
                    destination_position=self._snapshot(result.destination_position),
                    replayed=True,
                )
            if (command.organization_id, command.transfer_id) in self._staged_transfers:
                raise IdempotencyConflict("transfer was already shipped with another command")
            source = self._positions.get(command.source_key)
            source_version = source.version if source else 0
            if source_version != command.expected_source_version:
                raise ConcurrencyConflict(
                    f"expected source version {command.expected_source_version}, got {source_version}"
                )
            if source is None or source.available < command.quantity:
                raise InsufficientStock("transfer exceeds available source inventory")
            destination = self._positions.get(command.destination_key) or InventoryPosition(
                command.destination_key
            )
            now = datetime.now(UTC)
            moved_value = command.quantity * source.average_unit_cost
            source_next = self._snapshot(source)
            source_next.on_hand -= command.quantity
            source_next.inventory_value -= moved_value
            source_next.average_unit_cost = (
                source_next.inventory_value / source_next.on_hand if source_next.on_hand else ZERO
            )
            source_next.version += 1
            source_next.updated_at = now
            transaction = LedgerTransaction(
                uuid4(),
                command.organization_id,
                command.actor_id,
                "transfer_shipment",
                command.transfer_number,
                command.idempotency_key,
                now,
                assert_balanced(
                    (
                        LedgerLine(
                            InventoryAccount.ON_HAND, -command.quantity, command.source_key
                        ),
                        LedgerLine(
                            InventoryAccount.IN_TRANSIT, command.quantity, command.source_key
                        ),
                    )
                ),
            )
            self._positions[command.source_key] = source_next
            self._transactions.append(transaction)
            self._staged_transfers[(command.organization_id, command.transfer_id)] = (
                StagedTransferRecord(command, source.average_unit_cost)
            )
            self._events.append(
                OutboxEvent(
                    uuid4(),
                    command.organization_id,
                    "transfer.shipped",
                    command.transfer_id,
                    command.correlation_id,
                    now,
                    {"transfer_id": str(command.transfer_id), "quantity": str(command.quantity)},
                )
            )
            result = TransferShipmentResult(
                command.transfer_id,
                transaction,
                self._snapshot(source_next),
                self._snapshot(destination),
                command.quantity,
                False,
            )
            self._shipment_idempotency[scope] = (fingerprint, result)
            self._command_fingerprints[scope] = fingerprint
            return result

    def receive_transfer(self, command: TransferReceiptCommand) -> TransferReceiptResult:
        scope = (command.organization_id, command.idempotency_key)
        fingerprint = command.fingerprint()
        with self._lock:
            prior = self._receipt_idempotency.get(scope)
            if scope in self._command_fingerprints and prior is None:
                raise IdempotencyConflict("idempotency key was already used by another command")
            if prior:
                if prior[0] != fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different command")
                result = prior[1]
                return replace(
                    result,
                    destination_position=self._snapshot(result.destination_position),
                    replayed=True,
                )
            record = self._staged_transfers.get((command.organization_id, command.transfer_id))
            if record is None:
                raise ResourceNotFound("shipped transfer not found")
            if record.received:
                raise InvalidQuantity("transfer was already received")
            shipped = record.command.quantity
            if command.received_quantity < ZERO or command.received_quantity > shipped:
                raise InvalidQuantity("received quantity must be between zero and shipped quantity")
            destination = self._positions.get(record.command.destination_key) or InventoryPosition(
                record.command.destination_key
            )
            if destination.version != command.expected_destination_version:
                raise ConcurrencyConflict("destination position changed after the receipt snapshot")
            now = datetime.now(UTC)
            received = command.received_quantity
            discrepancy = shipped - received
            destination_next = self._snapshot(destination)
            if received:
                destination_next.on_hand += received
                destination_next.inventory_value += received * record.unit_cost
                destination_next.average_unit_cost = (
                    destination_next.inventory_value / destination_next.on_hand
                )
                destination_next.version += 1
                destination_next.updated_at = now
                self._positions[record.command.destination_key] = destination_next
            lines = [
                LedgerLine(
                    InventoryAccount.IN_TRANSIT, -shipped, record.command.source_key
                )
            ]
            if received:
                lines.append(
                    LedgerLine(
                        InventoryAccount.ON_HAND, received, record.command.destination_key
                    )
                )
            if discrepancy:
                lines.append(LedgerLine(InventoryAccount.DISCREPANCY, discrepancy))
            transaction = LedgerTransaction(
                uuid4(),
                command.organization_id,
                command.actor_id,
                "transfer_receipt",
                record.command.transfer_number,
                command.idempotency_key,
                now,
                assert_balanced(lines),
            )
            self._transactions.append(transaction)
            self._staged_transfers[(command.organization_id, command.transfer_id)] = replace(
                record, received=True
            )
            state = "received" if discrepancy == ZERO else "discrepancy_review"
            self._events.append(
                OutboxEvent(
                    uuid4(),
                    command.organization_id,
                    "transfer.received",
                    command.transfer_id,
                    command.correlation_id,
                    now,
                    {
                        "transfer_id": str(command.transfer_id),
                        "received_quantity": str(received),
                        "discrepancy_quantity": str(discrepancy),
                        "state": state,
                    },
                )
            )
            result = TransferReceiptResult(
                command.transfer_id,
                transaction,
                self._snapshot(destination_next),
                shipped,
                received,
                discrepancy,
                state,
                False,
            )
            self._receipt_idempotency[scope] = (fingerprint, result)
            self._command_fingerprints[scope] = fingerprint
            return result

    def post_count(self, command: CountCommand) -> CountResult:
        if command.stock_key.organization_id != command.organization_id:
            raise TenantBoundaryViolation("stock key belongs to a different organization")
        if command.counted_quantity < ZERO:
            raise UnbalancedPosting("counted quantity cannot be negative")
        if command.stock_key.serial_id is not None and command.counted_quantity not in (
            ZERO,
            Decimal("1"),
        ):
            raise InvalidQuantity("a serial-number count must be zero or one")
        scope = (command.organization_id, command.idempotency_key)
        fingerprint = command.fingerprint()
        with self._lock:
            prior = self._count_idempotency.get(scope)
            if scope in self._command_fingerprints and prior is None:
                raise IdempotencyConflict("idempotency key was already used by another command")
            if prior:
                if prior[0] != fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with a different command")
                result = prior[1]
                return CountResult(
                    result.cycle_count_id,
                    result.transaction,
                    result.snapshot_quantity,
                    result.counted_quantity,
                    result.variance_quantity,
                    self._snapshot(result.position),
                    True,
                )
            current = self._positions.get(command.stock_key) or InventoryPosition(command.stock_key)
            if current.version != command.expected_position_version:
                raise ConcurrencyConflict("position changed after the count snapshot")
            if command.counted_quantity < current.reserved:
                raise InsufficientStock("count would reduce stock below active reservations")
            variance = command.counted_quantity - current.on_hand
            now = datetime.now(UTC)
            transaction: LedgerTransaction | None = None
            if variance:
                lines = assert_balanced(
                    (
                        LedgerLine(InventoryAccount.ON_HAND, variance, command.stock_key),
                        LedgerLine(InventoryAccount.DISCREPANCY, -variance),
                    )
                )
                transaction = LedgerTransaction(
                    uuid4(), command.organization_id, command.actor_id, "cycle_count",
                    command.count_number, command.idempotency_key, now, lines,
                )
                self._transactions.append(transaction)
            position = self._snapshot(current)
            position.on_hand = command.counted_quantity
            position.inventory_value = position.on_hand * position.average_unit_cost
            position.version += 1
            position.updated_at = now
            self._positions[command.stock_key] = position
            count_id = uuid4()
            self._events.append(
                OutboxEvent(
                    uuid4(), command.organization_id, "inventory.count_posted", count_id,
                    command.correlation_id, now,
                    {"count_id": str(count_id), "variance": str(variance)},
                )
            )
            result = CountResult(
                count_id,
                transaction,
                current.on_hand,
                command.counted_quantity,
                variance,
                self._snapshot(position),
                False,
            )
            self._count_idempotency[scope] = (fingerprint, result)
            self._command_fingerprints[scope] = fingerprint
            return result

    @staticmethod
    def _validate_transfer(command: TransferCommand) -> None:
        InventoryLedger._validate_transfer_dimensions(
            command.organization_id,
            command.source_key,
            command.destination_key,
            command.quantity,
        )

    @staticmethod
    def _validate_transfer_dimensions(
        organization_id: UUID,
        source_key: StockKey,
        destination_key: StockKey,
        quantity: Decimal,
    ) -> None:
        if (
            source_key.organization_id != organization_id
            or destination_key.organization_id != organization_id
        ):
            raise TenantBoundaryViolation("transfer stock belongs to a different organization")
        if quantity <= ZERO:
            raise UnbalancedPosting("transfer quantity must be positive")
        if source_key.serial_id is not None and quantity != Decimal("1"):
            raise InvalidQuantity("a serial-number transfer must move exactly one unit")
        if source_key == destination_key:
            raise UnbalancedPosting("transfer source and destination must differ")
        if (
            source_key.product_id != destination_key.product_id
            or source_key.uom != destination_key.uom
            or source_key.lot_id != destination_key.lot_id
            or source_key.serial_id != destination_key.serial_id
            or source_key.condition != destination_key.condition
            or source_key.ownership != destination_key.ownership
        ):
            raise UnbalancedPosting("transfer dimensions must match except for destination")

    @staticmethod
    def _next_average_cost(
        current_quantity: Decimal,
        current_unit_cost: Decimal,
        quantity_delta: Decimal,
        receipt_unit_cost: Decimal | None,
    ) -> Decimal:
        if quantity_delta <= ZERO:
            return current_unit_cost
        unit_cost = receipt_unit_cost if receipt_unit_cost is not None else current_unit_cost
        if unit_cost < ZERO:
            raise UnbalancedPosting("unit cost cannot be negative")
        next_quantity = current_quantity + quantity_delta
        if next_quantity == ZERO:
            return ZERO
        return (
            (current_quantity * current_unit_cost) + (quantity_delta * unit_cost)
        ) / next_quantity

    @staticmethod
    def _snapshot(position: InventoryPosition) -> InventoryPosition:
        return InventoryPosition(
            key=position.key,
            on_hand=position.on_hand,
            reserved=position.reserved,
            average_unit_cost=position.average_unit_cost,
            inventory_value=position.inventory_value,
            version=position.version,
            updated_at=position.updated_at,
        )

    @property
    def outbox_events(self) -> tuple[OutboxEvent, ...]:
        return tuple(self._events)
