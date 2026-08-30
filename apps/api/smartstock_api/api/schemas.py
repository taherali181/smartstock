from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from smartstock_api.domain.inventory import (
    CountResult,
    InventoryPosition,
    ReservationResult,
    StockCondition,
    TransferResult,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InventoryAdjustmentRequest(StrictModel):
    product_id: UUID
    warehouse_id: UUID
    location_id: UUID
    quantity_delta: Decimal
    uom: str = Field(min_length=1, max_length=16)
    condition: StockCondition = StockCondition.SELLABLE
    lot_id: UUID | None = None
    serial_id: UUID | None = None
    ownership: str = Field(default="owned", min_length=1, max_length=32)
    reason_code: str = Field(min_length=1, max_length=64)
    business_reference: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=0)
    allow_negative: bool = False
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=28, decimal_places=9)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")

    @field_validator("quantity_delta")
    @classmethod
    def nonzero_quantity(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("quantity_delta must be nonzero")
        return value


class InventoryPositionResponse(StrictModel):
    product_id: UUID
    warehouse_id: UUID
    location_id: UUID
    uom: str
    condition: StockCondition
    lot_id: UUID | None
    serial_id: UUID | None
    ownership: str
    on_hand: Decimal
    reserved: Decimal
    available: Decimal
    average_unit_cost: Decimal
    inventory_value: Decimal
    version: int
    updated_at: datetime

    @classmethod
    def from_domain(cls, position: InventoryPosition) -> "InventoryPositionResponse":
        key = position.key
        return cls(
            product_id=key.product_id,
            warehouse_id=key.warehouse_id,
            location_id=key.location_id,
            uom=key.uom,
            condition=key.condition,
            lot_id=key.lot_id,
            serial_id=key.serial_id,
            ownership=key.ownership,
            on_hand=position.on_hand,
            reserved=position.reserved,
            available=position.available,
            average_unit_cost=position.average_unit_cost,
            inventory_value=position.inventory_value,
            version=position.version,
            updated_at=position.updated_at,
        )


class AdjustmentResponse(StrictModel):
    transaction_id: UUID
    replayed: bool
    position: InventoryPositionResponse


class PositionListResponse(StrictModel):
    items: list[InventoryPositionResponse]
    next_cursor: str | None = None


class ReservationCreateRequest(StrictModel):
    product_id: UUID
    warehouse_id: UUID
    location_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=28, decimal_places=9)
    uom: str = Field(min_length=1, max_length=16)
    source_type: str = Field(min_length=1, max_length=64)
    source_id: UUID
    expected_position_version: int = Field(ge=0)
    condition: StockCondition = StockCondition.SELLABLE
    lot_id: UUID | None = None
    serial_id: UUID | None = None
    ownership: str = Field(default="owned", min_length=1, max_length=32)


class ReservationResponse(StrictModel):
    id: UUID
    source_type: str
    source_id: UUID
    quantity: Decimal
    status: str
    version: int
    replayed: bool
    position: InventoryPositionResponse

    @classmethod
    def from_domain(cls, result: ReservationResult) -> "ReservationResponse":
        reservation = result.reservation
        return cls(
            id=reservation.id,
            source_type=reservation.source_type,
            source_id=reservation.source_id,
            quantity=reservation.quantity,
            status=reservation.status.value,
            version=reservation.version,
            replayed=result.replayed,
            position=InventoryPositionResponse.from_domain(result.position),
        )


class ReconciliationItemResponse(StrictModel):
    product_id: UUID
    warehouse_id: UUID
    location_id: UUID
    projected_on_hand: Decimal
    ledger_on_hand: Decimal
    projected_reserved: Decimal
    reservation_total: Decimal
    reconciled: bool


class ReconciliationResponse(StrictModel):
    items: list[ReconciliationItemResponse]
    exact: bool


class TransferCreateRequest(StrictModel):
    transfer_number: str = Field(min_length=1, max_length=128)
    product_id: UUID
    source_warehouse_id: UUID
    source_location_id: UUID
    destination_warehouse_id: UUID
    destination_location_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=28, decimal_places=9)
    uom: str = Field(min_length=1, max_length=16)
    condition: StockCondition = StockCondition.SELLABLE
    ownership: str = Field(default="owned", min_length=1, max_length=32)
    lot_id: UUID | None = None
    serial_id: UUID | None = None
    expected_source_version: int = Field(ge=0)
    expected_destination_version: int = Field(ge=0)


class TransferResponse(StrictModel):
    id: UUID
    transaction_id: UUID
    replayed: bool
    source_position: InventoryPositionResponse
    destination_position: InventoryPositionResponse

    @classmethod
    def from_domain(cls, result: TransferResult) -> "TransferResponse":
        return cls(
            id=result.transfer_id,
            transaction_id=result.transaction.id,
            replayed=result.replayed,
            source_position=InventoryPositionResponse.from_domain(result.source_position),
            destination_position=InventoryPositionResponse.from_domain(
                result.destination_position
            ),
        )


class CountPostRequest(StrictModel):
    count_number: str = Field(min_length=1, max_length=128)
    product_id: UUID
    warehouse_id: UUID
    location_id: UUID
    counted_quantity: Decimal = Field(ge=0, max_digits=28, decimal_places=9)
    uom: str = Field(min_length=1, max_length=16)
    condition: StockCondition = StockCondition.SELLABLE
    ownership: str = Field(default="owned", min_length=1, max_length=32)
    lot_id: UUID | None = None
    serial_id: UUID | None = None
    expected_position_version: int = Field(ge=0)


class CountPostResponse(StrictModel):
    id: UUID
    transaction_id: UUID | None
    snapshot_quantity: Decimal
    counted_quantity: Decimal
    variance_quantity: Decimal
    replayed: bool
    position: InventoryPositionResponse

    @classmethod
    def from_domain(cls, result: CountResult) -> "CountPostResponse":
        return cls(
            id=result.cycle_count_id,
            transaction_id=result.transaction.id if result.transaction else None,
            snapshot_quantity=result.snapshot_quantity,
            counted_quantity=result.counted_quantity,
            variance_quantity=result.variance_quantity,
            replayed=result.replayed,
            position=InventoryPositionResponse.from_domain(result.position),
        )
