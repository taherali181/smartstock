from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from smartstock_api.domain.inventory import InventoryPosition, StockCondition


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
