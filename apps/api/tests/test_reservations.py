from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from smartstock_api.domain.errors import ConcurrencyConflict, IdempotencyConflict, InsufficientStock
from smartstock_api.domain.inventory import (
    AdjustmentCommand,
    InventoryLedger,
    ReleaseReservationCommand,
    ReserveCommand,
    StockKey,
)


ORG = UUID("10000000-0000-0000-0000-000000000001")
ACTOR = UUID("10000000-0000-0000-0000-000000000002")
STOCK = StockKey(ORG, uuid4(), uuid4(), uuid4(), "ea")


def stocked_ledger(quantity: str = "10") -> InventoryLedger:
    ledger = InventoryLedger()
    ledger.adjust(
        AdjustmentCommand(
            ORG,
            ACTOR,
            STOCK,
            Decimal(quantity),
            "receipt",
            "RCPT-1",
            "receipt-key",
            uuid4(),
            0,
            unit_cost=Decimal("4.25"),
            currency="USD",
        )
    )
    return ledger


def reserve_command(quantity: str, key: str) -> ReserveCommand:
    return ReserveCommand(
        ORG,
        ACTOR,
        STOCK,
        "sales_order",
        uuid4(),
        Decimal(quantity),
        1,
        key,
        uuid4(),
    )


def test_reservation_retry_release_and_reconciliation_are_exact() -> None:
    ledger = stocked_ledger()
    command = reserve_command("6.5", "reservation-key")
    created = ledger.reserve(command)
    replay = ledger.reserve(command)

    assert replay.replayed is True
    assert replay.reservation.id == created.reservation.id
    assert created.position.available == Decimal("3.5")
    assert all(item.reconciled for item in ledger.reconcile(ORG, ACTOR))

    released = ledger.release_reservation(
        ReleaseReservationCommand(
            ORG, ACTOR, created.reservation.id, 1, "release-key", uuid4()
        )
    )
    assert released.position.reserved == 0
    assert released.position.available == Decimal("10")
    assert all(item.reconciled for item in ledger.reconcile(ORG, ACTOR))


def test_concurrent_reservations_cannot_oversell() -> None:
    ledger = stocked_ledger()
    commands = [reserve_command("7", f"reserve-{index}") for index in range(2)]

    def attempt(command: ReserveCommand) -> str:
        try:
            ledger.reserve(command)
            return "reserved"
        except (ConcurrencyConflict, InsufficientStock):
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, commands))

    assert sorted(outcomes) == ["rejected", "reserved"]
    position = ledger.position(STOCK)
    assert position.reserved == Decimal("7")
    assert position.available == Decimal("3")


def test_idempotency_key_cannot_be_reused_across_command_types() -> None:
    ledger = stocked_ledger()
    command = reserve_command("1", "receipt-key")
    with pytest.raises(IdempotencyConflict):
        ledger.reserve(command)
