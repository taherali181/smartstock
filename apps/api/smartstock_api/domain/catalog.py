from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from threading import RLock
from typing import Protocol
from uuid import UUID

from .errors import (
    DuplicateResource,
    IdempotencyConflict,
    InvalidQuantity,
    ResourceNotFound,
    TenantBoundaryViolation,
)


class LifecycleState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISCONTINUED = "discontinued"
    ARCHIVED = "archived"


class TrackingMode(StrEnum):
    NONE = "none"
    LOT = "lot"
    SERIAL = "serial"


@dataclass(frozen=True, slots=True)
class Product:
    id: UUID
    organization_id: UUID
    sku: str
    name: str
    base_uom: str
    tracking_mode: TrackingMode = TrackingMode.NONE
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    description: str | None = None
    custom_fields: dict[str, object] = field(default_factory=dict)
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ProductVariant:
    id: UUID
    organization_id: UUID
    product_id: UUID
    sku: str
    name: str
    attributes: dict[str, object] = field(default_factory=dict)
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    version: int = 1


@dataclass(frozen=True, slots=True)
class Warehouse:
    id: UUID
    organization_id: UUID
    code: str
    name: str
    timezone: str
    active: bool = True
    version: int = 1


@dataclass(frozen=True, slots=True)
class BinLocation:
    id: UUID
    organization_id: UUID
    warehouse_id: UUID
    code: str
    location_type: str = "bin"
    active: bool = True
    pick_sequence: int = 0
    version: int = 1


@dataclass(frozen=True, slots=True)
class UomConversion:
    id: UUID
    organization_id: UUID
    product_id: UUID
    from_uom: str
    to_uom: str
    factor: Decimal
    version: int = 1

    def convert(self, quantity: Decimal, from_uom: str, to_uom: str) -> Decimal:
        if from_uom == self.from_uom and to_uom == self.to_uom:
            return quantity * self.factor
        if from_uom == self.to_uom and to_uom == self.from_uom:
            return quantity / self.factor
        raise InvalidQuantity("conversion does not match the requested UOM pair")


@dataclass(frozen=True, slots=True)
class Supplier:
    id: UUID
    organization_id: UUID
    code: str
    name: str
    currency: str
    active: bool = True
    version: int = 1


@dataclass(frozen=True, slots=True)
class PriceBreak:
    minimum_quantity: Decimal
    unit_price: Decimal


@dataclass(frozen=True, slots=True)
class ProductSupplier:
    id: UUID
    organization_id: UUID
    product_id: UUID
    supplier_id: UUID
    purchase_uom: str
    currency: str
    supplier_sku: str | None = None
    minimum_order_quantity: Decimal = Decimal("0")
    case_pack: Decimal = Decimal("1")
    lead_time_days: int | None = None
    preferred: bool = False
    last_unit_cost: Decimal | None = None
    price_breaks: tuple[PriceBreak, ...] = ()
    version: int = 1


@dataclass(frozen=True, slots=True)
class Customer:
    id: UUID
    organization_id: UUID
    code: str
    name: str
    currency: str
    active: bool = True
    version: int = 1


@dataclass(frozen=True, slots=True)
class KitComponent:
    product_id: UUID
    quantity: Decimal
    uom: str


@dataclass(frozen=True, slots=True)
class Lot:
    id: UUID
    organization_id: UUID
    product_id: UUID
    lot_number: str
    manufactured_on: date | None = None
    expires_on: date | None = None
    status: str = "active"
    version: int = 1


@dataclass(frozen=True, slots=True)
class SerialNumber:
    id: UUID
    organization_id: UUID
    product_id: UUID
    serial_number: str
    status: str = "available"
    version: int = 1


class CatalogStore(Protocol):
    def create_product(self, product: Product, actor_id: UUID, correlation_id: UUID) -> Product: ...

    def products_for(self, organization_id: UUID, actor_id: UUID) -> list[Product]: ...

    def create_variant(
        self, variant: ProductVariant, actor_id: UUID, correlation_id: UUID
    ) -> ProductVariant: ...

    def add_conversion(
        self, conversion: UomConversion, actor_id: UUID, correlation_id: UUID
    ) -> UomConversion: ...

    def create_warehouse(
        self, warehouse: Warehouse, actor_id: UUID, correlation_id: UUID
    ) -> Warehouse: ...

    def warehouses_for(self, organization_id: UUID, actor_id: UUID) -> list[Warehouse]: ...

    def create_bin(
        self, location: BinLocation, actor_id: UUID, correlation_id: UUID
    ) -> BinLocation: ...

    def create_supplier(
        self, supplier: Supplier, actor_id: UUID, correlation_id: UUID
    ) -> Supplier: ...

    def suppliers_for(self, organization_id: UUID, actor_id: UUID) -> list[Supplier]: ...

    def add_product_supplier(
        self, source: ProductSupplier, actor_id: UUID, correlation_id: UUID
    ) -> ProductSupplier: ...

    def create_customer(
        self, customer: Customer, actor_id: UUID, correlation_id: UUID
    ) -> Customer: ...

    def customers_for(self, organization_id: UUID, actor_id: UUID) -> list[Customer]: ...

    def define_kit(
        self, organization_id: UUID, product_id: UUID, components: tuple[KitComponent, ...],
        actor_id: UUID, correlation_id: UUID, idempotency_key: str
    ) -> tuple[KitComponent, ...]: ...

    def create_lot(self, lot: Lot, actor_id: UUID, correlation_id: UUID) -> Lot: ...

    def create_serial(
        self, serial: SerialNumber, actor_id: UUID, correlation_id: UUID
    ) -> SerialNumber: ...


class InMemoryCatalogStore:
    def __init__(self) -> None:
        self._products: dict[tuple[UUID, UUID], Product] = {}
        self._product_skus: dict[tuple[UUID, str], UUID] = {}
        self._variants: dict[tuple[UUID, UUID], ProductVariant] = {}
        self._variant_skus: set[tuple[UUID, str]] = set()
        self._warehouses: dict[tuple[UUID, UUID], Warehouse] = {}
        self._warehouse_codes: dict[tuple[UUID, str], UUID] = {}
        self._bins: dict[tuple[UUID, UUID], BinLocation] = {}
        self._bin_codes: set[tuple[UUID, UUID, str]] = set()
        self._conversions: dict[tuple[UUID, UUID, str, str], UomConversion] = {}
        self._suppliers: dict[tuple[UUID, UUID], Supplier] = {}
        self._supplier_codes: set[tuple[UUID, str]] = set()
        self._product_suppliers: dict[tuple[UUID, UUID, UUID], ProductSupplier] = {}
        self._customers: dict[tuple[UUID, UUID], Customer] = {}
        self._customer_codes: set[tuple[UUID, str]] = set()
        self._kits: dict[tuple[UUID, UUID], tuple[KitComponent, ...]] = {}
        self._kit_commands: dict[
            tuple[UUID, str], tuple[UUID, tuple[KitComponent, ...]]
        ] = {}
        self._lots: dict[tuple[UUID, UUID], Lot] = {}
        self._lot_numbers: set[tuple[UUID, UUID, str]] = set()
        self._serials: dict[tuple[UUID, UUID], SerialNumber] = {}
        self._serial_numbers: set[tuple[UUID, str]] = set()
        self._lock = RLock()

    def create_product(self, product: Product, actor_id: UUID, correlation_id: UUID) -> Product:
        del actor_id, correlation_id
        with self._lock:
            sku_key = (product.organization_id, product.sku.casefold())
            existing = self._products.get((product.organization_id, product.id))
            if existing is not None:
                if (
                    existing.sku == product.sku
                    and existing.name == product.name
                    and existing.base_uom == product.base_uom
                    and existing.tracking_mode == product.tracking_mode
                    and existing.lifecycle_state == product.lifecycle_state
                    and existing.description == product.description
                    and existing.custom_fields == product.custom_fields
                ):
                    return existing
                raise DuplicateResource("idempotency key was reused with different product data")
            if sku_key in self._product_skus:
                raise DuplicateResource("SKU already exists in this organization")
            self._products[(product.organization_id, product.id)] = product
            self._product_skus[sku_key] = product.id
            return product

    def products_for(self, organization_id: UUID, actor_id: UUID) -> list[Product]:
        del actor_id
        with self._lock:
            return [p for (tenant_id, _), p in self._products.items() if tenant_id == organization_id]

    def create_variant(
        self, variant: ProductVariant, actor_id: UUID, correlation_id: UUID
    ) -> ProductVariant:
        del actor_id, correlation_id
        if (variant.organization_id, variant.product_id) not in self._products:
            raise ResourceNotFound("parent product not found")
        key = (variant.organization_id, variant.sku.casefold())
        with self._lock:
            existing = self._variants.get((variant.organization_id, variant.id))
            if existing is not None:
                if existing == variant:
                    return existing
                raise DuplicateResource("idempotency key was reused with different variant data")
            if key in self._variant_skus:
                raise DuplicateResource("variant SKU already exists")
            self._variants[(variant.organization_id, variant.id)] = variant
            self._variant_skus.add(key)
        return variant

    def add_conversion(
        self, conversion: UomConversion, actor_id: UUID, correlation_id: UUID
    ) -> UomConversion:
        del actor_id, correlation_id
        if conversion.factor <= 0 or conversion.from_uom == conversion.to_uom:
            raise InvalidQuantity("UOM conversion must have a positive factor between different UOMs")
        if (conversion.organization_id, conversion.product_id) not in self._products:
            raise ResourceNotFound("product not found")
        key = (
            conversion.organization_id,
            conversion.product_id,
            conversion.from_uom,
            conversion.to_uom,
        )
        with self._lock:
            if key in self._conversions:
                raise DuplicateResource("UOM conversion already exists")
            self._conversions[key] = conversion
        return conversion

    def create_warehouse(
        self, warehouse: Warehouse, actor_id: UUID, correlation_id: UUID
    ) -> Warehouse:
        del actor_id, correlation_id
        with self._lock:
            code_key = (warehouse.organization_id, warehouse.code.casefold())
            existing = self._warehouses.get((warehouse.organization_id, warehouse.id))
            if existing is not None:
                if (
                    existing.code == warehouse.code
                    and existing.name == warehouse.name
                    and existing.timezone == warehouse.timezone
                ):
                    return existing
                raise DuplicateResource("idempotency key was reused with different warehouse data")
            if code_key in self._warehouse_codes:
                raise DuplicateResource("warehouse code already exists")
            self._warehouses[(warehouse.organization_id, warehouse.id)] = warehouse
            self._warehouse_codes[code_key] = warehouse.id
        return warehouse

    def warehouses_for(self, organization_id: UUID, actor_id: UUID) -> list[Warehouse]:
        del actor_id
        with self._lock:
            return [w for (tenant_id, _), w in self._warehouses.items() if tenant_id == organization_id]

    def create_bin(
        self, location: BinLocation, actor_id: UUID, correlation_id: UUID
    ) -> BinLocation:
        del actor_id, correlation_id
        if (location.organization_id, location.warehouse_id) not in self._warehouses:
            raise ResourceNotFound("warehouse not found")
        key = (location.organization_id, location.warehouse_id, location.code.casefold())
        with self._lock:
            if key in self._bin_codes:
                raise DuplicateResource("bin code already exists in this warehouse")
            self._bins[(location.organization_id, location.id)] = location
            self._bin_codes.add(key)
        return location

    def create_supplier(
        self, supplier: Supplier, actor_id: UUID, correlation_id: UUID
    ) -> Supplier:
        del actor_id, correlation_id
        key = (supplier.organization_id, supplier.code.casefold())
        with self._lock:
            if key in self._supplier_codes:
                raise DuplicateResource("supplier code already exists")
            self._suppliers[(supplier.organization_id, supplier.id)] = supplier
            self._supplier_codes.add(key)
        return supplier

    def add_product_supplier(
        self, source: ProductSupplier, actor_id: UUID, correlation_id: UUID
    ) -> ProductSupplier:
        del actor_id, correlation_id
        if (source.organization_id, source.product_id) not in self._products:
            raise ResourceNotFound("product not found")
        if (source.organization_id, source.supplier_id) not in self._suppliers:
            raise ResourceNotFound("supplier not found")
        if source.minimum_order_quantity < 0 or source.case_pack <= 0:
            raise InvalidQuantity("MOQ cannot be negative and case pack must be positive")
        previous_minimum = Decimal("-1")
        for price_break in source.price_breaks:
            if (
                price_break.minimum_quantity <= previous_minimum
                or price_break.minimum_quantity <= 0
                or price_break.unit_price < 0
            ):
                raise InvalidQuantity("price breaks must be positive and strictly increasing")
            previous_minimum = price_break.minimum_quantity
        key = (source.organization_id, source.product_id, source.supplier_id)
        with self._lock:
            existing = self._product_suppliers.get(key)
            if existing is not None:
                if existing == source:
                    return existing
                raise DuplicateResource("supplier is already assigned to this product")
            self._product_suppliers[key] = source
        return source

    def suppliers_for(self, organization_id: UUID, actor_id: UUID) -> list[Supplier]:
        del actor_id
        with self._lock:
            return [
                supplier
                for (tenant_id, _), supplier in self._suppliers.items()
                if tenant_id == organization_id
            ]

    def create_customer(
        self, customer: Customer, actor_id: UUID, correlation_id: UUID
    ) -> Customer:
        del actor_id, correlation_id
        key = (customer.organization_id, customer.code.casefold())
        with self._lock:
            if key in self._customer_codes:
                raise DuplicateResource("customer code already exists")
            self._customers[(customer.organization_id, customer.id)] = customer
            self._customer_codes.add(key)
        return customer

    def customers_for(self, organization_id: UUID, actor_id: UUID) -> list[Customer]:
        del actor_id
        with self._lock:
            return [
                customer
                for (tenant_id, _), customer in self._customers.items()
                if tenant_id == organization_id
            ]

    def define_kit(
        self, organization_id: UUID, product_id: UUID, components: tuple[KitComponent, ...],
        actor_id: UUID, correlation_id: UUID, idempotency_key: str
    ) -> tuple[KitComponent, ...]:
        del actor_id, correlation_id
        if (organization_id, product_id) not in self._products:
            raise ResourceNotFound("kit product not found")
        if not components:
            raise InvalidQuantity("a kit requires at least one component")
        for component in components:
            if component.product_id == product_id:
                raise InvalidQuantity("a kit cannot contain itself")
            if component.quantity <= 0:
                raise InvalidQuantity("kit component quantities must be positive")
            if (organization_id, component.product_id) not in self._products:
                raise TenantBoundaryViolation("kit component is not in this organization")
        with self._lock:
            command_key = (organization_id, idempotency_key)
            prior = self._kit_commands.get(command_key)
            command = (product_id, components)
            if prior is not None:
                if prior != command:
                    raise IdempotencyConflict(
                        "idempotency key was reused with a different kit definition"
                    )
                return components
            self._kits[(organization_id, product_id)] = components
            self._kit_commands[command_key] = command
        return components

    def create_lot(self, lot: Lot, actor_id: UUID, correlation_id: UUID) -> Lot:
        del actor_id, correlation_id
        if (lot.organization_id, lot.product_id) not in self._products:
            raise ResourceNotFound("product not found")
        if lot.manufactured_on and lot.expires_on and lot.expires_on < lot.manufactured_on:
            raise InvalidQuantity("lot expiry cannot precede manufacture date")
        key = (lot.organization_id, lot.product_id, lot.lot_number.casefold())
        with self._lock:
            existing = self._lots.get((lot.organization_id, lot.id))
            if existing is not None:
                if existing == lot:
                    return existing
                raise DuplicateResource("idempotency key was reused with different lot data")
            if key in self._lot_numbers:
                raise DuplicateResource("lot number already exists for this product")
            self._lots[(lot.organization_id, lot.id)] = lot
            self._lot_numbers.add(key)
        return lot

    def create_serial(
        self, serial: SerialNumber, actor_id: UUID, correlation_id: UUID
    ) -> SerialNumber:
        del actor_id, correlation_id
        if (serial.organization_id, serial.product_id) not in self._products:
            raise ResourceNotFound("product not found")
        key = (serial.organization_id, serial.serial_number.casefold())
        with self._lock:
            existing = self._serials.get((serial.organization_id, serial.id))
            if existing is not None:
                if existing == serial:
                    return existing
                raise DuplicateResource("idempotency key was reused with different serial data")
            if key in self._serial_numbers:
                raise DuplicateResource("serial number already exists in this organization")
            self._serials[(serial.organization_id, serial.id)] = serial
            self._serial_numbers.add(key)
        return serial


def kit_availability(
    components: tuple[KitComponent, ...], available_by_product: dict[UUID, Decimal]
) -> Decimal:
    if not components:
        return Decimal("0")
    availability: list[Decimal] = []
    for component in components:
        if component.quantity <= 0:
            raise InvalidQuantity("kit component quantities must be positive")
        availability.append(
            available_by_product.get(component.product_id, Decimal("0")) / component.quantity
        )
    return min(availability)
