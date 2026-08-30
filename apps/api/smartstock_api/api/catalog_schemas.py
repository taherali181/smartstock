from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from smartstock_api.api.schemas import StrictModel
from smartstock_api.domain.catalog import LifecycleState, Product, TrackingMode, Warehouse


class ProductCreateRequest(StrictModel):
    sku: str = Field(min_length=1, max_length=128, pattern=r"^[^\s]+$")
    name: str = Field(min_length=1, max_length=255)
    base_uom: str = Field(min_length=1, max_length=16)
    tracking_mode: TrackingMode = TrackingMode.NONE
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    description: str | None = Field(default=None, max_length=4000)
    custom_fields: dict[str, object] = Field(default_factory=dict)


class ProductResponse(StrictModel):
    id: UUID
    sku: str
    name: str
    base_uom: str
    tracking_mode: TrackingMode
    lifecycle_state: LifecycleState
    description: str | None
    custom_fields: dict[str, object]
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, product: Product) -> "ProductResponse":
        return cls(
            id=product.id,
            sku=product.sku,
            name=product.name,
            base_uom=product.base_uom,
            tracking_mode=product.tracking_mode,
            lifecycle_state=product.lifecycle_state,
            description=product.description,
            custom_fields=product.custom_fields,
            version=product.version,
            created_at=product.created_at,
            updated_at=product.updated_at,
        )


class ProductListResponse(StrictModel):
    items: list[ProductResponse]
    next_cursor: str | None = None


class VariantCreateRequest(StrictModel):
    sku: str = Field(min_length=1, max_length=128, pattern=r"^[^\s]+$")
    name: str = Field(min_length=1, max_length=255)
    attributes: dict[str, object] = Field(default_factory=dict)
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE


class VariantResponse(StrictModel):
    id: UUID
    product_id: UUID
    sku: str
    name: str
    attributes: dict[str, object]
    lifecycle_state: LifecycleState
    version: int


class UomConversionRequest(StrictModel):
    from_uom: str = Field(min_length=1, max_length=16)
    to_uom: str = Field(min_length=1, max_length=16)
    factor: Decimal = Field(gt=0, max_digits=28, decimal_places=9)

    @field_validator("to_uom")
    @classmethod
    def uoms_differ(cls, value: str, info):
        if value == info.data.get("from_uom"):
            raise ValueError("from_uom and to_uom must differ")
        return value


class UomConversionResponse(StrictModel):
    id: UUID
    product_id: UUID
    from_uom: str
    to_uom: str
    factor: Decimal
    version: int


class WarehouseCreateRequest(StrictModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[^\s]+$")
    name: str = Field(min_length=1, max_length=255)
    timezone: str = Field(min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def valid_iana_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


class WarehouseResponse(StrictModel):
    id: UUID
    code: str
    name: str
    timezone: str
    active: bool
    version: int

    @classmethod
    def from_domain(cls, warehouse: Warehouse) -> "WarehouseResponse":
        return cls(
            id=warehouse.id,
            code=warehouse.code,
            name=warehouse.name,
            timezone=warehouse.timezone,
            active=warehouse.active,
            version=warehouse.version,
        )


class WarehouseListResponse(StrictModel):
    items: list[WarehouseResponse]
    next_cursor: str | None = None


class BinCreateRequest(StrictModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[^\s]+$")
    location_type: str = Field(
        default="bin", pattern=r"^(bin|receiving|shipping|in_transit|discrepancy)$"
    )
    pick_sequence: int = Field(default=0, ge=0)


class BinResponse(StrictModel):
    id: UUID
    warehouse_id: UUID
    code: str
    location_type: str
    active: bool
    pick_sequence: int
    version: int


class PartyCreateRequest(StrictModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[^\s]+$")
    name: str = Field(min_length=1, max_length=255)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")


class PartyResponse(StrictModel):
    id: UUID
    code: str
    name: str
    currency: str
    active: bool
    version: int


class PartyListResponse(StrictModel):
    items: list[PartyResponse]
    next_cursor: str | None = None


class PriceBreakRequest(StrictModel):
    minimum_quantity: Decimal = Field(gt=0, max_digits=28, decimal_places=9)
    unit_price: Decimal = Field(ge=0, max_digits=28, decimal_places=9)


class ProductSupplierRequest(StrictModel):
    supplier_id: UUID
    supplier_sku: str | None = Field(default=None, max_length=128)
    purchase_uom: str = Field(min_length=1, max_length=16)
    minimum_order_quantity: Decimal = Field(default=Decimal("0"), ge=0)
    case_pack: Decimal = Field(default=Decimal("1"), gt=0)
    lead_time_days: int | None = Field(default=None, ge=0)
    preferred: bool = False
    last_unit_cost: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    price_breaks: list[PriceBreakRequest] = Field(default_factory=list, max_length=100)


class ProductSupplierResponse(StrictModel):
    id: UUID
    product_id: UUID
    supplier_id: UUID
    supplier_sku: str | None
    purchase_uom: str
    minimum_order_quantity: Decimal
    case_pack: Decimal
    lead_time_days: int | None
    preferred: bool
    last_unit_cost: Decimal | None
    currency: str
    price_breaks: list[PriceBreakRequest]
    version: int


class KitComponentRequest(StrictModel):
    product_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=28, decimal_places=9)
    uom: str = Field(min_length=1, max_length=16)


class KitDefinitionRequest(StrictModel):
    components: list[KitComponentRequest] = Field(min_length=1, max_length=500)


class KitDefinitionResponse(StrictModel):
    product_id: UUID
    components: list[KitComponentRequest]
    version: int


class LotCreateRequest(StrictModel):
    product_id: UUID
    lot_number: str = Field(min_length=1, max_length=128)
    manufactured_on: date | None = None
    expires_on: date | None = None

    @field_validator("expires_on")
    @classmethod
    def expiry_follows_manufacture(cls, value: date | None, info):
        manufactured_on = info.data.get("manufactured_on")
        if value and manufactured_on and value < manufactured_on:
            raise ValueError("expires_on cannot precede manufactured_on")
        return value


class LotResponse(StrictModel):
    id: UUID
    product_id: UUID
    lot_number: str
    manufactured_on: date | None
    expires_on: date | None
    status: str
    version: int


class SerialCreateRequest(StrictModel):
    product_id: UUID
    serial_number: str = Field(min_length=1, max_length=255)


class SerialResponse(StrictModel):
    id: UUID
    product_id: UUID
    serial_number: str
    status: str
    version: int
