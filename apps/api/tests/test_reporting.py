from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from smartstock_api.domain.catalog import Product
from smartstock_api.domain.inventory import InventoryPosition, StockCondition, StockKey
from smartstock_api.domain.operations import (
    OperationalOrder,
    OrderKind,
    OrderLine,
    Receipt,
    ReceiptPostingLine,
)
from smartstock_api.domain.reporting import (
    receipt_summaries,
    reorder_suggestions,
    stock_summaries,
)


def test_stock_and_reorder_reports_use_available_and_eligible_incoming() -> None:
    organization_id, product_id, warehouse_id, location_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    now = datetime.now(UTC)
    product = Product(
        product_id,
        organization_id,
        "SKU-1017",
        "Insulated Travel Bottle",
        "ea",
        custom_fields={"reorder_point": 40, "safety_stock": 12, "target_stock": 220},
        updated_at=now,
    )
    position = InventoryPosition(
        StockKey(organization_id, product_id, warehouse_id, location_id, "ea"),
        on_hand=Decimal("20"),
        reserved=Decimal("5"),
        inventory_value=Decimal("360"),
        updated_at=now,
    )
    order = OperationalOrder(
        uuid4(),
        organization_id,
        OrderKind.PURCHASE,
        "PO-2001",
        uuid4(),
        warehouse_id,
        "acknowledged",
        (OrderLine(uuid4(), product_id, Decimal("200"), "ea", Decimal("18"), "USD"),),
        "USD",
    )

    stock = stock_summaries([product], [position], [order])
    assert len(stock) == 1
    assert stock[0].available == Decimal("15")
    assert stock[0].incoming == Decimal("200")

    suggestions = reorder_suggestions([product], [position], [order])
    assert len(suggestions) == 1
    assert suggestions[0].available == Decimal("15")
    assert suggestions[0].reorder_point == Decimal("40")
    assert suggestions[0].suggested_quantity == Decimal("5")


def test_non_sellable_stock_is_not_available_for_reorder() -> None:
    organization_id, product_id, warehouse_id, location_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    product = Product(
        product_id,
        organization_id,
        "SKU-QA",
        "Inspection Stock",
        "ea",
        custom_fields={"reorder_point": 10, "target_stock": 20},
    )
    quarantined = InventoryPosition(
        StockKey(
            organization_id,
            product_id,
            warehouse_id,
            location_id,
            "ea",
            condition=StockCondition.QUARANTINED,
        ),
        on_hand=Decimal("50"),
    )

    suggestion = reorder_suggestions([product], [quarantined], [])[0]
    assert suggestion.available == Decimal("0")
    assert suggestion.suggested_quantity == Decimal("20")


def test_receipts_today_summary_keeps_accepted_and_rejected_separate() -> None:
    receipt = Receipt(
        uuid4(),
        uuid4(),
        "RCPT-1001",
        uuid4(),
        uuid4(),
        (uuid4(),),
        (
            ReceiptPostingLine(
                uuid4(), uuid4(), uuid4(), Decimal("8"), Decimal("1")
            ),
        ),
    )

    summary = receipt_summaries([receipt])[0]
    assert summary.accepted_quantity == Decimal("8")
    assert summary.rejected_quantity == Decimal("1")
