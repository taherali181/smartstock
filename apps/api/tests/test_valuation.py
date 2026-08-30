from decimal import Decimal
from uuid import uuid4

from smartstock_api.domain.valuation import (
    CostLayer,
    allocate_landed_cost,
    consume_fifo,
    weighted_average_cost,
)


def test_weighted_average_uses_decimal_money() -> None:
    assert weighted_average_cost(
        Decimal("10"), Decimal("2.00"), Decimal("5"), Decimal("5.00")
    ) == Decimal("3.00")


def test_fifo_consumes_oldest_layers_without_mutating_inputs() -> None:
    first, second = uuid4(), uuid4()
    consumed, total = consume_fifo(
        [CostLayer(first, Decimal("3"), Decimal("2")), CostLayer(second, Decimal("5"), Decimal("4"))],
        Decimal("6"),
    )
    assert [(item.layer_id, item.quantity) for item in consumed] == [
        (first, Decimal("3")),
        (second, Decimal("3")),
    ]
    assert total == Decimal("18")


def test_landed_cost_allocation_conserves_exact_total() -> None:
    first, second, third = uuid4(), uuid4(), uuid4()
    allocations = allocate_landed_cost(
        Decimal("10"), {first: Decimal("1"), second: Decimal("2"), third: Decimal("3")}
    )
    assert sum(allocations.values(), Decimal("0")) == Decimal("10")
