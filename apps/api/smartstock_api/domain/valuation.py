from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from .errors import InsufficientStock, InvalidQuantity

ZERO = Decimal("0")


class ValuationMethod(StrEnum):
    WEIGHTED_AVERAGE = "weighted_average"
    FIFO = "fifo"


@dataclass(frozen=True, slots=True)
class CostLayer:
    id: UUID
    remaining_quantity: Decimal
    unit_cost: Decimal


@dataclass(frozen=True, slots=True)
class LayerConsumption:
    layer_id: UUID
    quantity: Decimal
    unit_cost: Decimal

    @property
    def total_cost(self) -> Decimal:
        return self.quantity * self.unit_cost


def weighted_average_cost(
    current_quantity: Decimal,
    current_unit_cost: Decimal,
    receipt_quantity: Decimal,
    receipt_unit_cost: Decimal,
) -> Decimal:
    if current_quantity < ZERO or receipt_quantity <= ZERO:
        raise InvalidQuantity("weighted-average inputs require nonnegative stock and a receipt")
    if current_unit_cost < ZERO or receipt_unit_cost < ZERO:
        raise InvalidQuantity("unit cost cannot be negative")
    total_quantity = current_quantity + receipt_quantity
    return (
        (current_quantity * current_unit_cost) + (receipt_quantity * receipt_unit_cost)
    ) / total_quantity


def consume_fifo(
    layers: list[CostLayer], requested_quantity: Decimal
) -> tuple[tuple[LayerConsumption, ...], Decimal]:
    if requested_quantity <= ZERO:
        raise InvalidQuantity("FIFO consumption quantity must be positive")
    remaining = requested_quantity
    consumed: list[LayerConsumption] = []
    for layer in layers:
        if layer.remaining_quantity <= ZERO:
            continue
        quantity = min(layer.remaining_quantity, remaining)
        consumed.append(LayerConsumption(layer.id, quantity, layer.unit_cost))
        remaining -= quantity
        if remaining == ZERO:
            break
    if remaining > ZERO:
        raise InsufficientStock("FIFO cost layers do not cover the requested quantity")
    total_cost = sum((item.total_cost for item in consumed), ZERO)
    return tuple(consumed), total_cost


def allocate_landed_cost(
    total_cost: Decimal, weights: dict[UUID, Decimal]
) -> dict[UUID, Decimal]:
    if total_cost < ZERO or not weights or any(value < ZERO for value in weights.values()):
        raise InvalidQuantity("landed-cost allocation requires nonnegative cost and weights")
    denominator = sum(weights.values(), ZERO)
    if denominator <= ZERO:
        raise InvalidQuantity("landed-cost allocation weights must total more than zero")
    allocations: dict[UUID, Decimal] = {}
    allocated = ZERO
    items = list(weights.items())
    for item_id, weight in items[:-1]:
        share = total_cost * weight / denominator
        allocations[item_id] = share
        allocated += share
    allocations[items[-1][0]] = total_cost - allocated
    return allocations
