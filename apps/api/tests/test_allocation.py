from datetime import date
from decimal import Decimal
from uuid import uuid4

from smartstock_api.domain.allocation import AllocationCandidate, allocate_fefo
from smartstock_api.domain.catalog import KitComponent, kit_availability
from smartstock_api.domain.inventory import StockKey


def test_fefo_allocates_earliest_eligible_expiry_first() -> None:
    organization_id, product_id, warehouse_id, location_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    later = StockKey(
        organization_id, product_id, warehouse_id, location_id, "ea", lot_id=uuid4()
    )
    sooner = StockKey(
        organization_id, product_id, warehouse_id, location_id, "ea", lot_id=uuid4()
    )
    allocations = allocate_fefo(
        [
            AllocationCandidate(later, Decimal("10"), date(2027, 2, 1)),
            AllocationCandidate(sooner, Decimal("3"), date(2027, 1, 1)),
        ],
        Decimal("5"),
        as_of=date(2026, 8, 30),
    )
    assert [(item.stock_key, item.quantity) for item in allocations] == [
        (sooner, Decimal("3")),
        (later, Decimal("2")),
    ]


def test_kit_availability_is_limited_by_scarcest_component() -> None:
    first, second = uuid4(), uuid4()
    assert kit_availability(
        (KitComponent(first, Decimal("2"), "ea"), KitComponent(second, Decimal("1"), "ea")),
        {first: Decimal("7"), second: Decimal("10")},
    ) == Decimal("3.5")
