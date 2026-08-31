from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import create_engine, text

from smartstock_api.config import get_settings
from smartstock_api.domain.catalog import (
    BinLocation,
    Customer,
    Lot,
    PriceBreak,
    Product,
    ProductSupplier,
    SerialNumber,
    Supplier,
    TrackingMode,
    Warehouse,
)
from smartstock_api.domain.inventory import AdjustmentCommand, StockKey
from smartstock_api.domain.operations import (
    AllocationPostingLine,
    OperationalOrder,
    OrderKind,
    OrderLine,
    WarehouseTask,
    WarehouseTaskState,
    WarehouseTaskType,
)
from smartstock_api.infrastructure.database import TenantSessionFactory
from smartstock_api.infrastructure.postgres_catalog import PostgresCatalogStore
from smartstock_api.infrastructure.postgres_inventory import PostgresInventoryStore
from smartstock_api.infrastructure.postgres_operations import PostgresOperationsStore

DEMO_ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000001")
DEMO_USER_ID = UUID("00000000-0000-0000-0000-000000000001")
DEMO_INSTANT = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)

PRODUCT_NAMES = (
    "Classic Cotton Tee",
    "Performance Polo",
    "Fleece Zip Hoodie",
    "Canvas Work Jacket",
    "Stretch Chino Pant",
    "Everyday Denim Jean",
    "Merino Crew Sweater",
    "Quilted Field Vest",
    "Waterproof Shell",
    "Packable Rain Pant",
    "Trail Running Shoe",
    "Leather Work Boot",
    "Court Sneaker",
    "Wool Beanie",
    "Canvas Cap",
    "Leather Belt",
    "Insulated Travel Bottle",
    "Stainless Camp Mug",
    "Canvas Daypack",
    "Rolling Duffel",
    "Organic Hand Soap",
    "Cedar Shampoo",
    "Mineral Sunscreen",
    "First Aid Kit",
    "LED Headlamp",
    "Compact Lantern",
    "USB-C Power Bank",
    "Braided Charging Cable",
    "Wireless Barcode Scanner",
    "Thermal Label Roll",
    "Shipping Carton Medium",
    "Recycled Packing Paper",
    "Roasted Coffee Beans",
    "Herbal Tea Sachets",
    "Dark Chocolate Bar",
    "Trail Mix Pouch",
    "Rugged Tablet",
    "Mobile Receipt Printer",
    "Handheld Inventory Terminal",
    "Digital Shipping Scale",
)

SUPPLIER_DATA = (
    ("ACME", "Acme Supply Co.", "USD", 5),
    ("NORTHSTAR", "Northstar Wholesale", "USD", 8),
    ("MAPLE", "Maple Trade Partners", "CAD", 10),
    ("PACIFIC", "Pacific Goods Group", "USD", 14),
    ("SUMMIT", "Summit Industrial", "USD", 6),
    ("HARBOR", "Harbor Packaging", "USD", 3),
)

CUSTOMER_DATA = (
    ("CUST-001", "Bluebird Outfitters"),
    ("CUST-002", "Cedar & Main"),
    ("CUST-003", "Evergreen General Store"),
    ("CUST-004", "Frontier Supply House"),
    ("CUST-005", "Great Lakes Retail"),
    ("CUST-006", "Northern Trail Co."),
    ("CUST-007", "Prairie Market"),
    ("CUST-008", "Redwood Mercantile"),
)

WAREHOUSE_DATA = (
    ("WH-MAIN", "Baltimore Main Distribution", "America/New_York"),
    ("WH-EAST", "Toronto East Distribution", "America/Toronto"),
    ("WH-WEST", "Reno West Distribution", "America/Los_Angeles"),
)


def stable_id(kind: str, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"smartstock-demo:{kind}:{key}")


def _bootstrap_tenant(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.organization_id', :value, true)"),
            {"value": str(DEMO_ORGANIZATION_ID)},
        )
        connection.execute(
            text("SELECT set_config('app.user_id', :value, true)"),
            {"value": str(DEMO_USER_ID)},
        )
        connection.execute(
            text(
                """
                INSERT INTO users (id,email,display_name,email_verified)
                VALUES (:id,'demo@smartstock.local','SmartStock Demo Owner',true)
                ON CONFLICT (id) DO UPDATE SET
                  display_name=EXCLUDED.display_name,email_verified=true,disabled_at=NULL
                """
            ),
            {"id": DEMO_USER_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO organizations (id,slug,name,currency,valuation_method)
                VALUES (:id,'smartstock-demo','SmartStock Demo Company','USD','weighted_average')
                ON CONFLICT (id) DO UPDATE SET
                  slug=EXCLUDED.slug,name=EXCLUDED.name,currency=EXCLUDED.currency
                """
            ),
            {"id": DEMO_ORGANIZATION_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO memberships (organization_id,user_id,role,active)
                VALUES (:organization_id,:user_id,'owner',true)
                ON CONFLICT (organization_id,user_id) DO UPDATE SET role='owner',active=true
                """
            ),
            {"organization_id": DEMO_ORGANIZATION_ID, "user_id": DEMO_USER_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO feature_flags (organization_id,key,enabled,configuration,updated_by)
                VALUES
                  (:organization_id,'rag.enabled',true,'{}'::jsonb,:user_id),
                  (:organization_id,'warehouse.pwa.enabled',true,'{}'::jsonb,:user_id)
                ON CONFLICT (organization_id,key) DO UPDATE SET
                  enabled=EXCLUDED.enabled,updated_by=EXCLUDED.updated_by,updated_at=now()
                """
            ),
            {"organization_id": DEMO_ORGANIZATION_ID, "user_id": DEMO_USER_ID},
        )


def _seed_catalog(sessions: TenantSessionFactory) -> tuple[dict[str, UUID], dict[str, UUID]]:
    catalog = PostgresCatalogStore(sessions)
    correlation_id = stable_id("correlation", "catalog")
    warehouse_ids: dict[str, UUID] = {}
    location_ids: dict[str, UUID] = {}

    for warehouse_index, (code, name, timezone) in enumerate(WAREHOUSE_DATA, start=1):
        warehouse_id = stable_id("warehouse", code)
        warehouse_ids[code] = warehouse_id
        catalog.create_warehouse(
            Warehouse(
                warehouse_id,
                DEMO_ORGANIZATION_ID,
                code,
                name,
                timezone,
            ),
            DEMO_USER_ID,
            correlation_id,
        )
        for location_index, (location_code, location_type) in enumerate(
            (
                ("RECEIVING", "receiving"),
                ("A-01", "bin"),
                ("B-01", "bin"),
                ("PICK-01", "bin"),
                ("SHIPPING", "shipping"),
            ),
            start=1,
        ):
            location_id = stable_id("location", f"{code}:{location_code}")
            location_ids[f"{code}:{location_code}"] = location_id
            catalog.create_bin(
                BinLocation(
                    location_id,
                    DEMO_ORGANIZATION_ID,
                    warehouse_id,
                    location_code,
                    location_type,
                    pick_sequence=warehouse_index * 100 + location_index,
                ),
                DEMO_USER_ID,
                correlation_id,
            )

    with sessions.session(DEMO_ORGANIZATION_ID, DEMO_USER_ID) as session:
        for warehouse_code, warehouse_id in warehouse_ids.items():
            zones = (
                ("RECV", "Receiving", "receiving", "RECEIVING"),
                ("STORAGE", "Bulk Storage", "storage", "A-01"),
                ("PICK", "Forward Pick", "picking", "B-01"),
                ("PACK", "Packing and Shipping", "packing", "PICK-01"),
            )
            for code, name, zone_type, location_code in zones:
                zone_id = stable_id("zone", f"{warehouse_code}:{code}")
                session.execute(
                    text(
                        """
                        INSERT INTO warehouse_zones (
                          organization_id,id,warehouse_id,code,name,zone_type
                        ) VALUES (
                          :organization_id,:id,:warehouse_id,:code,:name,:zone_type
                        ) ON CONFLICT (organization_id,id) DO UPDATE SET
                          name=EXCLUDED.name,zone_type=EXCLUDED.zone_type,active=true
                        """
                    ),
                    {
                        "organization_id": DEMO_ORGANIZATION_ID,
                        "id": zone_id,
                        "warehouse_id": warehouse_id,
                        "code": code,
                        "name": name,
                        "zone_type": zone_type,
                    },
                )
                session.execute(
                    text(
                        """
                        UPDATE locations SET zone_id=:zone_id
                        WHERE organization_id=:organization_id AND id=:location_id
                        """
                    ),
                    {
                        "organization_id": DEMO_ORGANIZATION_ID,
                        "zone_id": zone_id,
                        "location_id": location_ids[f"{warehouse_code}:{location_code}"],
                    },
                )

    product_ids: dict[str, UUID] = {}
    for index, name in enumerate(PRODUCT_NAMES, start=1):
        sku = f"SKU-{1000 + index}"
        product_id = stable_id("product", sku)
        product_ids[sku] = product_id
        tracking_mode = (
            TrackingMode.LOT
            if 33 <= index <= 36
            else TrackingMode.SERIAL if index >= 37 else TrackingMode.NONE
        )
        catalog.create_product(
            Product(
                id=product_id,
                organization_id=DEMO_ORGANIZATION_ID,
                sku=sku,
                name=name,
                base_uom="ea",
                tracking_mode=tracking_mode,
                description=f"Demo catalog item {sku}: {name}.",
                custom_fields={
                    "reorder_point": 40 if sku == "SKU-1017" else 20 + index % 15,
                    "safety_stock": 12 if sku == "SKU-1017" else 5 + index % 6,
                    "target_stock": 220 if sku == "SKU-1017" else 80 + index,
                },
                created_at=DEMO_INSTANT,
                updated_at=DEMO_INSTANT,
            ),
            DEMO_USER_ID,
            correlation_id,
        )

    supplier_ids: dict[str, UUID] = {}
    for code, name, currency, _ in SUPPLIER_DATA:
        supplier_id = stable_id("supplier", code)
        supplier_ids[code] = supplier_id
        catalog.create_supplier(
            Supplier(supplier_id, DEMO_ORGANIZATION_ID, code, name, currency),
            DEMO_USER_ID,
            correlation_id,
        )
    for index, sku in enumerate(product_ids, start=1):
        supplier_code, _, currency, lead_time = SUPPLIER_DATA[(index - 1) % len(SUPPLIER_DATA)]
        if sku == "SKU-1017":
            supplier_code, currency, lead_time = "ACME", "USD", 5
        catalog.add_product_supplier(
            ProductSupplier(
                id=stable_id("product-supplier", f"{sku}:{supplier_code}"),
                organization_id=DEMO_ORGANIZATION_ID,
                product_id=product_ids[sku],
                supplier_id=supplier_ids[supplier_code],
                purchase_uom="ea",
                currency=currency,
                supplier_sku=f"{supplier_code}-{sku[4:]}",
                minimum_order_quantity=Decimal("10"),
                case_pack=Decimal("10"),
                lead_time_days=lead_time,
                preferred=True,
                last_unit_cost=Decimal("6") + Decimal(index) / Decimal("2"),
                price_breaks=(
                    PriceBreak(Decimal("50"), Decimal("5.75") + Decimal(index) / 2),
                    PriceBreak(Decimal("200"), Decimal("5.25") + Decimal(index) / 2),
                ),
            ),
            DEMO_USER_ID,
            correlation_id,
        )

    for code, name in CUSTOMER_DATA:
        catalog.create_customer(
            Customer(
                stable_id("customer", code),
                DEMO_ORGANIZATION_ID,
                code,
                name,
                "USD",
            ),
            DEMO_USER_ID,
            correlation_id,
        )

    return warehouse_ids | location_ids, product_ids | supplier_ids


def _seed_stock(
    sessions: TenantSessionFactory,
    resource_ids: dict[str, UUID],
    party_and_product_ids: dict[str, UUID],
) -> dict[StockKey, int]:
    catalog = PostgresCatalogStore(sessions)
    inventory = PostgresInventoryStore(sessions)
    position_versions: dict[StockKey, int] = {}

    for index in range(1, 41):
        sku = f"SKU-{1000 + index}"
        product_id = party_and_product_ids[sku]
        lot_id = None
        if 33 <= index <= 36:
            lot_id = stable_id("lot", f"{sku}:LOT-2026-01")
            catalog.create_lot(
                Lot(
                    lot_id,
                    DEMO_ORGANIZATION_ID,
                    product_id,
                    "LOT-2026-01",
                    date(2026, 7, 1),
                    date(2027, 7, 1),
                ),
                DEMO_USER_ID,
                stable_id("correlation", f"lot:{sku}"),
            )
        for warehouse_offset, (warehouse_code, _, _) in enumerate(WAREHOUSE_DATA):
            warehouse_id = resource_ids[warehouse_code]
            location_code = "A-01" if index % 2 else "B-01"
            location_id = resource_ids[f"{warehouse_code}:{location_code}"]
            serial_id = None
            if index >= 37:
                serial_number = f"SN-{sku[4:]}-{warehouse_code[-4:]}"
                serial_id = stable_id("serial", serial_number)
                catalog.create_serial(
                    SerialNumber(
                        serial_id,
                        DEMO_ORGANIZATION_ID,
                        product_id,
                        serial_number,
                    ),
                    DEMO_USER_ID,
                    stable_id("correlation", f"serial:{serial_number}"),
                )
            quantity = (
                Decimal("1")
                if serial_id
                else Decimal("12")
                if sku == "SKU-1017" and warehouse_code == "WH-MAIN"
                else Decimal("4")
                if sku == "SKU-1017"
                else Decimal(24 + (index * 7 + warehouse_offset * 13) % 70)
            )
            key = StockKey(
                DEMO_ORGANIZATION_ID,
                product_id,
                warehouse_id,
                location_id,
                "ea",
                lot_id=lot_id,
                serial_id=serial_id,
            )
            result = inventory.adjust(
                AdjustmentCommand(
                    organization_id=DEMO_ORGANIZATION_ID,
                    actor_id=DEMO_USER_ID,
                    stock_key=key,
                    quantity_delta=quantity,
                    reason_code="opening_stock",
                    business_reference=f"DEMO-{sku}-{warehouse_code}",
                    idempotency_key=f"demo-opening-{sku.lower()}-{warehouse_code.lower()}",
                    correlation_id=stable_id(
                        "correlation", f"opening:{sku}:{warehouse_code}"
                    ),
                    expected_version=0,
                    unit_cost=Decimal("5") + Decimal(index) / Decimal("2"),
                    currency="USD",
                )
            )
            position_versions[key] = result.position.version

    return position_versions


def _order(
    number: str,
    kind: OrderKind,
    party_id: UUID,
    warehouse_id: UUID,
    lines: tuple[tuple[str, UUID, Decimal, Decimal], ...],
) -> OperationalOrder:
    return OperationalOrder(
        id=stable_id("order", number),
        organization_id=DEMO_ORGANIZATION_ID,
        kind=kind,
        order_number=number,
        party_id=party_id,
        warehouse_id=warehouse_id,
        state="draft" if kind == OrderKind.PURCHASE else "quote",
        lines=tuple(
            OrderLine(
                stable_id("order-line", f"{number}:{line_number}"),
                product_id,
                quantity,
                "ea",
                unit_price,
                "USD",
            )
            for line_number, (_, product_id, quantity, unit_price) in enumerate(lines, start=1)
        ),
        currency="USD",
        expected_on=DEMO_INSTANT.date() + timedelta(days=10),
        notes="Deterministic SmartStock demo order",
        created_at=DEMO_INSTANT,
        updated_at=DEMO_INSTANT,
    )


def _advance_order(
    store: PostgresOperationsStore,
    order: OperationalOrder,
    targets: tuple[str, ...],
) -> OperationalOrder:
    current = store.order(
        DEMO_ORGANIZATION_ID, DEMO_USER_ID, order.kind, order.id
    )
    ordered_states = (order.state,) + targets
    if current.state not in ordered_states:
        return current
    for target in targets:
        if current.state == target:
            continue
        if (
            current.state in ordered_states
            and ordered_states.index(current.state) > ordered_states.index(target)
        ):
            continue
        current, _ = store.transition_order(
            DEMO_ORGANIZATION_ID,
            DEMO_USER_ID,
            order.kind,
            order.id,
            target,
            current.version,
            stable_id("correlation", f"order:{order.order_number}:{target}"),
            f"demo-order-{order.order_number.lower()}-{target}",
        )
    return current


def _seed_operations(
    sessions: TenantSessionFactory,
    resource_ids: dict[str, UUID],
    party_and_product_ids: dict[str, UUID],
    position_versions: dict[StockKey, int],
) -> None:
    store = PostgresOperationsStore(sessions)
    main_warehouse = resource_ids["WH-MAIN"]

    purchase_orders = (
        (
            _order(
                "PO-2001",
                OrderKind.PURCHASE,
                party_and_product_ids["ACME"],
                main_warehouse,
                (
                    (
                        "SKU-1017",
                        party_and_product_ids["SKU-1017"],
                        Decimal("200"),
                        Decimal("18.50"),
                    ),
                ),
            ),
            ("pending_approval", "approved", "sent", "acknowledged"),
        ),
        (
            _order(
                "PO-2002",
                OrderKind.PURCHASE,
                party_and_product_ids["NORTHSTAR"],
                resource_ids["WH-EAST"],
                (("SKU-1024", party_and_product_ids["SKU-1024"], Decimal("80"), Decimal("14.25")),),
            ),
            ("pending_approval", "approved"),
        ),
    )
    for order, targets in purchase_orders:
        store.create_order(
            order,
            DEMO_USER_ID,
            stable_id("correlation", f"create:{order.order_number}"),
            f"demo-create-{order.order_number.lower()}",
        )
        _advance_order(store, order, targets)

    sales_orders = (
        _order(
            "SO-1001",
            OrderKind.SALES,
            stable_id("customer", "CUST-001"),
            main_warehouse,
            (("SKU-1002", party_and_product_ids["SKU-1002"], Decimal("10"), Decimal("29.00")),),
        ),
        _order(
            "SO-1002",
            OrderKind.SALES,
            stable_id("customer", "CUST-002"),
            main_warehouse,
            (("SKU-1003", party_and_product_ids["SKU-1003"], Decimal("3"), Decimal("64.00")),),
        ),
        _order(
            "SO-1004",
            OrderKind.SALES,
            stable_id("customer", "CUST-004"),
            main_warehouse,
            (("SKU-1017", party_and_product_ids["SKU-1017"], Decimal("50"), Decimal("42.00")),),
        ),
    )
    for order in sales_orders:
        store.create_order(
            order,
            DEMO_USER_ID,
            stable_id("correlation", f"create:{order.order_number}"),
            f"demo-create-{order.order_number.lower()}",
        )
    allocated_order = _advance_order(store, sales_orders[1], ("draft", "confirmed"))
    _advance_order(store, sales_orders[2], ("draft", "confirmed"))

    if allocated_order.state == "confirmed":
        allocation_key = next(
            key
            for key in position_versions
            if key.product_id == party_and_product_ids["SKU-1003"]
            and key.warehouse_id == main_warehouse
        )
        store.allocate_sales_order(
            DEMO_ORGANIZATION_ID,
            DEMO_USER_ID,
            allocated_order.id,
            stable_id("allocation", "SO-1002"),
            (
                AllocationPostingLine(
                    stable_id("allocation-line", "SO-1002:1"),
                    allocated_order.lines[0].id,
                    allocation_key.location_id,
                    Decimal("3"),
                    position_versions[allocation_key],
                ),
            ),
            allocated_order.version,
            stable_id("correlation", "allocate:SO-1002"),
            "demo-allocate-so-1002",
        )

    count_key = next(
        key
        for key in position_versions
        if key.product_id == party_and_product_ids["SKU-1005"]
        and key.warehouse_id == main_warehouse
    )
    store.create_task(
        WarehouseTask(
            id=stable_id("task", "COUNT-WH-MAIN-001"),
            organization_id=DEMO_ORGANIZATION_ID,
            task_number="COUNT-WH-MAIN-001",
            task_type=WarehouseTaskType.COUNT,
            warehouse_id=main_warehouse,
            source_location_id=count_key.location_id,
            product_id=count_key.product_id,
            quantity=Decimal("1"),
            uom="ea",
            expected_position_version=position_versions[count_key],
            reference_type="cycle_count",
            reference_id=stable_id("count", "COUNT-WH-MAIN-001"),
            priority=20,
            created_at=DEMO_INSTANT,
            updated_at=DEMO_INSTANT,
        ),
        DEMO_USER_ID,
        stable_id("correlation", "task:COUNT-WH-MAIN-001"),
        "demo-task-count-wh-main-001",
    )

    transfer_key = next(
        key
        for key in position_versions
        if key.product_id == party_and_product_ids["SKU-1008"]
        and key.warehouse_id == main_warehouse
    )
    destination_key = next(
        key
        for key in position_versions
        if key.product_id == transfer_key.product_id
        and key.warehouse_id == resource_ids["WH-EAST"]
    )
    store.create_task(
        WarehouseTask(
            id=stable_id("task", "XFER-WH-MAIN-EAST-001"),
            organization_id=DEMO_ORGANIZATION_ID,
            task_number="XFER-WH-MAIN-EAST-001",
            task_type=WarehouseTaskType.TRANSFER,
            warehouse_id=main_warehouse,
            destination_warehouse_id=resource_ids["WH-EAST"],
            source_location_id=transfer_key.location_id,
            destination_location_id=destination_key.location_id,
            product_id=transfer_key.product_id,
            quantity=Decimal("5"),
            uom="ea",
            expected_position_version=position_versions[transfer_key],
            reference_type="replenishment_transfer",
            reference_id=stable_id("transfer", "XFER-WH-MAIN-EAST-001"),
            priority=30,
            created_at=DEMO_INSTANT,
            updated_at=DEMO_INSTANT,
        ),
        DEMO_USER_ID,
        stable_id("correlation", "task:XFER-WH-MAIN-EAST-001"),
        "demo-task-xfer-wh-main-east-001",
    )


def seed_database(database_url: str) -> dict[str, int]:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        _bootstrap_tenant(engine)
        sessions = TenantSessionFactory(engine)
        resource_ids, party_and_product_ids = _seed_catalog(sessions)
        position_versions = _seed_stock(sessions, resource_ids, party_and_product_ids)
        _seed_operations(
            sessions,
            resource_ids,
            party_and_product_ids,
            position_versions,
        )
        with sessions.session(DEMO_ORGANIZATION_ID, DEMO_USER_ID) as session:
            scope = {"organization_id": DEMO_ORGANIZATION_ID}
            return {
                "warehouses": session.execute(
                    text("SELECT count(*) FROM warehouses WHERE organization_id=:organization_id"),
                    scope,
                ).scalar_one(),
                "zones": session.execute(
                    text(
                        """
                        SELECT count(*) FROM warehouse_zones
                        WHERE organization_id=:organization_id
                        """
                    ),
                    scope,
                ).scalar_one(),
                "locations": session.execute(
                    text("SELECT count(*) FROM locations WHERE organization_id=:organization_id"),
                    scope,
                ).scalar_one(),
                "products": session.execute(
                    text("SELECT count(*) FROM products WHERE organization_id=:organization_id"),
                    scope,
                ).scalar_one(),
                "suppliers": session.execute(
                    text("SELECT count(*) FROM suppliers WHERE organization_id=:organization_id"),
                    scope,
                ).scalar_one(),
                "customers": session.execute(
                    text("SELECT count(*) FROM customers WHERE organization_id=:organization_id"),
                    scope,
                ).scalar_one(),
                "positions": session.execute(
                    text(
                        """
                        SELECT count(*) FROM inventory_positions
                        WHERE organization_id=:organization_id
                        """
                    ),
                    scope,
                ).scalar_one(),
                "purchase_orders": session.execute(
                    text(
                        """
                        SELECT count(*) FROM operational_orders
                        WHERE organization_id=:organization_id AND kind='purchase'
                        """
                    ),
                    scope,
                ).scalar_one(),
                "sales_orders": session.execute(
                    text(
                        """
                        SELECT count(*) FROM operational_orders
                        WHERE organization_id=:organization_id AND kind='sales'
                        """
                    ),
                    scope,
                ).scalar_one(),
                "active_tasks": session.execute(
                    text(
                        """
                        SELECT count(*) FROM warehouse_tasks
                        WHERE organization_id=:organization_id
                          AND state IN ('open','assigned','in_progress','exception')
                        """
                    ),
                    scope,
                ).scalar_one(),
            }
    finally:
        engine.dispose()


def main() -> None:
    summary = seed_database(get_settings().database_url)
    print(
        "SmartStock demo seed ready: "
        + ", ".join(f"{name}={count}" for name, count in summary.items())
    )


if __name__ == "__main__":
    main()
