from decimal import Decimal
from uuid import uuid4

import pytest

from smartstock_api.api.catalog_schemas import WarehouseCreateRequest
from smartstock_api.domain.catalog import (
    BinLocation,
    InMemoryCatalogStore,
    KitComponent,
    Product,
    PriceBreak,
    ProductSupplier,
    Supplier,
    TrackingMode,
    UomConversion,
    Warehouse,
)
from smartstock_api.domain.errors import DuplicateResource, IdempotencyConflict, InvalidQuantity


def test_catalog_uniqueness_is_tenant_scoped_and_uom_conversion_is_reversible() -> None:
    store = InMemoryCatalogStore()
    organization_id, actor_id, correlation_id = uuid4(), uuid4(), uuid4()
    product = Product(
        uuid4(), organization_id, "CASE-01", "Case Item", "ea", TrackingMode.LOT
    )
    store.create_product(product, actor_id, correlation_id)
    conversion = UomConversion(
        uuid4(), organization_id, product.id, "case", "ea", Decimal("12")
    )
    store.add_conversion(conversion, actor_id, correlation_id)

    assert conversion.convert(Decimal("2.5"), "case", "ea") == Decimal("30.0")
    assert conversion.convert(Decimal("30"), "ea", "case") == Decimal("2.5")

    with pytest.raises(DuplicateResource):
        store.create_product(
            Product(uuid4(), organization_id, "case-01", "Duplicate", "ea"),
            actor_id,
            correlation_id,
        )


def test_kit_rejects_cycles_and_requires_same_tenant_components() -> None:
    store = InMemoryCatalogStore()
    organization_id, actor_id, correlation_id = uuid4(), uuid4(), uuid4()
    kit = Product(uuid4(), organization_id, "KIT", "Kit", "ea")
    component = Product(uuid4(), organization_id, "PART", "Part", "ea")
    store.create_product(kit, actor_id, correlation_id)
    store.create_product(component, actor_id, correlation_id)

    result = store.define_kit(
        organization_id,
        kit.id,
        (KitComponent(component.id, Decimal("2.5"), "ea"),),
        actor_id,
        correlation_id,
        "define-kit-1",
    )
    assert result[0].quantity == Decimal("2.5")

    with pytest.raises(InvalidQuantity):
        store.define_kit(
            organization_id,
            kit.id,
            (KitComponent(kit.id, Decimal("1"), "ea"),),
            actor_id,
            correlation_id,
            "define-kit-cycle",
        )

    assert store.define_kit(
        organization_id,
        kit.id,
        (KitComponent(component.id, Decimal("2.5"), "ea"),),
        actor_id,
        correlation_id,
        "define-kit-1",
    ) == result

    with pytest.raises(IdempotencyConflict, match="idempotency key"):
        store.define_kit(
            organization_id,
            kit.id,
            (KitComponent(component.id, Decimal("3"), "ea"),),
            actor_id,
            correlation_id,
            "define-kit-1",
        )


def test_bin_codes_are_unique_within_warehouse_only() -> None:
    store = InMemoryCatalogStore()
    organization_id, actor_id, correlation_id = uuid4(), uuid4(), uuid4()
    first = Warehouse(uuid4(), organization_id, "EAST", "East", "America/New_York")
    second = Warehouse(uuid4(), organization_id, "WEST", "West", "America/Los_Angeles")
    store.create_warehouse(first, actor_id, correlation_id)
    store.create_warehouse(second, actor_id, correlation_id)
    west_bin = BinLocation(
        uuid4(), organization_id, second.id, "A-01", pick_sequence=20
    )
    east_late = BinLocation(
        uuid4(), organization_id, first.id, "A-02", pick_sequence=20
    )
    east_early = BinLocation(
        uuid4(), organization_id, first.id, "A-01", pick_sequence=10
    )
    for location in (west_bin, east_late, east_early):
        store.create_bin(location, actor_id, correlation_id)

    assert len(store.warehouses_for(organization_id, actor_id)) == 2
    assert store.bins_for(organization_id, actor_id, first.id) == [east_early, east_late]


def test_product_supports_multiple_supplier_constraints_and_price_breaks() -> None:
    store = InMemoryCatalogStore()
    organization_id, actor_id, correlation_id = uuid4(), uuid4(), uuid4()
    product = Product(uuid4(), organization_id, "BUY-1", "Purchased Item", "ea")
    supplier = Supplier(uuid4(), organization_id, "SUP-1", "Supplier", "USD")
    store.create_product(product, actor_id, correlation_id)
    store.create_supplier(supplier, actor_id, correlation_id)
    source = ProductSupplier(
        id=uuid4(),
        organization_id=organization_id,
        product_id=product.id,
        supplier_id=supplier.id,
        purchase_uom="case",
        currency="USD",
        minimum_order_quantity=Decimal("12"),
        case_pack=Decimal("6"),
        lead_time_days=8,
        preferred=True,
        price_breaks=(
            PriceBreak(Decimal("12"), Decimal("4.50")),
            PriceBreak(Decimal("60"), Decimal("4.10")),
        ),
    )
    assert store.add_product_supplier(source, actor_id, correlation_id) == source


def test_warehouse_request_defaults_to_utc() -> None:
    request = WarehouseCreateRequest(code="MAIN", name="Main Warehouse")

    assert request.timezone == "UTC"
