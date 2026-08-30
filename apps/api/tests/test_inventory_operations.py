from decimal import Decimal
from uuid import UUID, uuid4

from smartstock_api.domain.inventory import (
    AdjustmentCommand,
    CountCommand,
    InventoryAccount,
    InventoryLedger,
    StockKey,
    TransferCommand,
)


ORG = UUID("20000000-0000-0000-0000-000000000001")
ACTOR = UUID("20000000-0000-0000-0000-000000000002")
PRODUCT = uuid4()
SOURCE = StockKey(ORG, PRODUCT, uuid4(), uuid4(), "ea")
DESTINATION = StockKey(ORG, PRODUCT, uuid4(), uuid4(), "ea")


def ledger_with_stock() -> InventoryLedger:
    ledger = InventoryLedger()
    ledger.adjust(
        AdjustmentCommand(
            ORG,
            ACTOR,
            SOURCE,
            Decimal("10"),
            "receipt",
            "RCPT-1",
            "receipt-operation-key",
            uuid4(),
            0,
            unit_cost=Decimal("3.50"),
            currency="USD",
        )
    )
    return ledger


def test_transfer_posts_balanced_in_transit_lines_and_preserves_value() -> None:
    ledger = ledger_with_stock()
    command = TransferCommand(
        ORG,
        ACTOR,
        "TR-100",
        SOURCE,
        DESTINATION,
        Decimal("4"),
        1,
        0,
        "transfer-key",
        uuid4(),
    )
    result = ledger.transfer(command)
    replay = ledger.transfer(command)

    assert replay.replayed is True
    assert result.source_position.on_hand == Decimal("6")
    assert result.destination_position.on_hand == Decimal("4")
    assert result.source_position.inventory_value == Decimal("21.00")
    assert result.destination_position.inventory_value == Decimal("14.00")
    assert [line.account for line in result.transaction.lines] == [
        InventoryAccount.ON_HAND,
        InventoryAccount.IN_TRANSIT,
        InventoryAccount.IN_TRANSIT,
        InventoryAccount.ON_HAND,
    ]
    assert all(item.reconciled for item in ledger.reconcile(ORG, ACTOR))


def test_approved_count_posts_only_variance_to_discrepancy() -> None:
    ledger = ledger_with_stock()
    result = ledger.post_count(
        CountCommand(
            ORG,
            ACTOR,
            "COUNT-1",
            SOURCE,
            Decimal("8.5"),
            1,
            "count-key",
            uuid4(),
        )
    )

    assert result.snapshot_quantity == Decimal("10")
    assert result.variance_quantity == Decimal("-1.5")
    assert result.transaction is not None
    assert result.transaction.lines[1].account == InventoryAccount.DISCREPANCY
    assert result.position.inventory_value == Decimal("29.750")
    assert all(item.reconciled for item in ledger.reconcile(ORG, ACTOR))
