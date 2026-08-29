from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class Availability:
    sellable_on_hand: Decimal
    reserved: Decimal
    committed: Decimal
    eligible_incoming: Decimal
    safety_stock: Decimal

    def __post_init__(self) -> None:
        if any(
            value < ZERO
            for value in (
                self.sellable_on_hand,
                self.reserved,
                self.committed,
                self.eligible_incoming,
                self.safety_stock,
            )
        ):
            raise ValueError("availability inputs cannot be negative")

    @property
    def available(self) -> Decimal:
        return self.sellable_on_hand - self.reserved

    @property
    def unreserved_committed(self) -> Decimal:
        return max(ZERO, self.committed - self.reserved)

    @property
    def backordered(self) -> Decimal:
        return max(ZERO, self.committed - self.available)

    @property
    def atp(self) -> Decimal:
        return (
            self.available
            + self.eligible_incoming
            - self.unreserved_committed
            - self.safety_stock
        )
