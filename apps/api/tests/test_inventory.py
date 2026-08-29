from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from smartstock_api.domain.errors import (
    ConcurrencyConflict,
    IdempotencyConflict,
    InsufficientStock,
    TenantBoundaryViolation,
    UnbalancedPosting,
)
from smartstock_api.domain.inventory import (
    AdjustmentCommand,
    InventoryAccount,
    InventoryLedger,
    LedgerLine,
    StockKey,
    assert_balanced,
)


ORG = UUID("00000000-0000-0000-0000-000000000001")
ACTOR = UUID("00000000-0000-0000-0000-000000000002")
WAREHOUSE = UUID("00000000-0000-0000-0000-000000000003")
LOCATION = UUID("00000000-0000-0000-0000-000000000004")
PRODUCT = UUID("00000000-0000-0000-0000-000000000005")


def command(
    delta: str,
    *,
    version: int = 0,
    key: str = "adjustment-0001",
    organization_id: UUID = ORG,
    stock_organization_id: UUID = ORG,
) -> AdjustmentCommand:
    return AdjustmentCommand(
        organization_id=organization_id,
        actor_id=ACTOR,
        stock_key=StockKey(
            organization_id=stock_organization_id,
            product_id=PRODUCT,
            warehouse_id=WAREHOUSE,
            location_id=LOCATION,
            uom="ea",
        ),
        quantity_delta=Decimal(delta),
        reason_code="initial_receipt",
        business_reference="RCPT-1001",
        idempotency_key=key,
        correlation_id=uuid4(),
        expected_version=version,
    )


def test_adjustment_is_balanced_and_updates_projection() -> None:
    ledger = InventoryLedger()
    result = ledger.adjust(command("12.5"))

    assert result.position.on_hand == Decimal("12.5")
    assert result.position.available == Decimal("12.5")
    assert result.position.version == 1
    assert sum((line.quantity for line in result.transaction.lines), Decimal("0")) == 0
    assert ledger.outbox_events[0].topic == "inventory.ledger_posted"


def test_identical_retry_replays_without_duplicate_posting() -> None:
    ledger = InventoryLedger()
    first_command = command("5")

    first = ledger.adjust(first_command)
    replay = ledger.adjust(first_command)

    assert replay.replayed is True
    assert replay.transaction.id == first.transaction.id
    assert len(ledger.outbox_events) == 1


def test_idempotency_key_cannot_hide_a_different_command() -> None:
    ledger = InventoryLedger()
    ledger.adjust(command("5"))

    with pytest.raises(IdempotencyConflict):
        ledger.adjust(command("6"))


def test_stale_version_is_rejected() -> None:
    ledger = InventoryLedger()
    ledger.adjust(command("5"))

    with pytest.raises(ConcurrencyConflict):
        ledger.adjust(command("2", version=0, key="adjustment-0002"))


def test_negative_stock_policy_protects_physical_stock() -> None:
    ledger = InventoryLedger()

    with pytest.raises(InsufficientStock):
        ledger.adjust(command("-1"))


def test_tenant_mismatch_is_rejected_before_posting() -> None:
    ledger = InventoryLedger()

    with pytest.raises(TenantBoundaryViolation):
        ledger.adjust(command("1", stock_organization_id=uuid4()))


def test_unbalanced_or_single_line_posting_is_invalid() -> None:
    with pytest.raises(UnbalancedPosting):
        assert_balanced((LedgerLine(InventoryAccount.EXTERNAL, Decimal("1")),))
