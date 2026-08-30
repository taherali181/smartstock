from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .errors import InsufficientStock, InvalidQuantity
from .inventory import StockKey


@dataclass(frozen=True, slots=True)
class AllocationCandidate:
    stock_key: StockKey
    available: Decimal
    expires_on: date | None


@dataclass(frozen=True, slots=True)
class Allocation:
    stock_key: StockKey
    quantity: Decimal


def allocate_fefo(
    candidates: list[AllocationCandidate], quantity: Decimal, *, as_of: date
) -> tuple[Allocation, ...]:
    if quantity <= 0:
        raise InvalidQuantity("allocation quantity must be positive")
    eligible = [
        candidate
        for candidate in candidates
        if candidate.available > 0
        and (candidate.expires_on is None or candidate.expires_on >= as_of)
    ]
    eligible.sort(
        key=lambda candidate: (
            candidate.expires_on is None,
            candidate.expires_on or date.max,
            str(candidate.stock_key.lot_id or candidate.stock_key.serial_id or ""),
        )
    )
    remaining = quantity
    allocations: list[Allocation] = []
    for candidate in eligible:
        allocated = min(remaining, candidate.available)
        allocations.append(Allocation(candidate.stock_key, allocated))
        remaining -= allocated
        if remaining == 0:
            return tuple(allocations)
    raise InsufficientStock("eligible FEFO inventory cannot cover the requested quantity")
