import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from smartstock_api.domain.catalog import (
    BinLocation,
    Customer,
    KitComponent,
    Lot,
    PriceBreak,
    Product,
    ProductSupplier,
    ProductVariant,
    SerialNumber,
    Supplier,
    UomConversion,
    Warehouse,
)
from smartstock_api.domain.errors import ConcurrencyConflict, InsufficientStock
from smartstock_api.domain.inventory import (
    AdjustmentCommand,
    CountCommand,
    ReserveCommand,
    StockKey,
    TransferCommand,
)
from smartstock_api.domain.operations import (
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

DATABASE_URL = os.getenv("SMARTSTOCK_TEST_DATABASE_URL")
pytestmark = pytest.mark.postgres


@pytest.fixture
def phase2_database():
    if not DATABASE_URL:
        pytest.skip("SMARTSTOCK_TEST_DATABASE_URL is not configured")
    admin_engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    role_name = f"smartstock_phase2_test_{uuid4().hex}"
    role_password = uuid4().hex
    organization_id, actor_id = uuid4(), uuid4()
    product_id = uuid4()
    source_warehouse, destination_warehouse = uuid4(), uuid4()
    source_location, destination_location = uuid4(), uuid4()
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE ROLE {role_name} LOGIN PASSWORD '{role_password}' "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
        )
        connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {role_name}")
        connection.exec_driver_sql(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role_name}"
        )
        connection.exec_driver_sql(
            f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {role_name}"
        )
        parameters = {
            "actor_id": actor_id,
            "email": f"{actor_id}@example.test",
            "organization_id": organization_id,
            "slug": f"phase2-{organization_id}",
            "product_id": product_id,
            "sku": f"SKU-{product_id}",
            "source_warehouse": source_warehouse,
            "destination_warehouse": destination_warehouse,
            "source_code": f"SRC-{source_warehouse}",
            "destination_code": f"DST-{destination_warehouse}",
            "source_location": source_location,
            "destination_location": destination_location,
        }
        statements = (
            """INSERT INTO users (id, email, display_name, email_verified)
               VALUES (:actor_id, :email, 'Phase 2 User', true)""",
            """INSERT INTO organizations (id, slug, name, currency)
               VALUES (:organization_id, :slug, 'Phase 2 Organization', 'USD')""",
            """INSERT INTO memberships (organization_id, user_id, role)
               VALUES (:organization_id, :actor_id, 'owner')""",
            """INSERT INTO products (organization_id, id, sku, name, base_uom)
               VALUES (:organization_id, :product_id, :sku, 'Phase 2 Product', 'ea')""",
            """INSERT INTO warehouses (organization_id, id, code, name, timezone)
               VALUES
                 (:organization_id, :source_warehouse, :source_code, 'Source', 'UTC'),
                 (:organization_id, :destination_warehouse, :destination_code, 'Destination', 'UTC')""",
            """INSERT INTO locations (organization_id, id, warehouse_id, code, location_type)
               VALUES
                 (:organization_id, :source_location, :source_warehouse, 'A-01', 'bin'),
                 (:organization_id, :destination_location, :destination_warehouse, 'B-01', 'bin')""",
        )
        for statement in statements:
            connection.execute(text(statement), parameters)
    application_url = make_url(DATABASE_URL).set(username=role_name, password=role_password)
    engine = create_engine(application_url, pool_size=4, max_overflow=0, pool_pre_ping=True)
    source_key = StockKey(
        organization_id, product_id, source_warehouse, source_location, "ea"
    )
    destination_key = StockKey(
        organization_id, product_id, destination_warehouse, destination_location, "ea"
    )
    yield engine, organization_id, actor_id, source_key, destination_key
    engine.dispose()
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f"DROP OWNED BY {role_name}")
        connection.exec_driver_sql(f"DROP ROLE {role_name}")
    admin_engine.dispose()


def stock(store, organization_id, actor_id, source_key, quantity="10"):
    return store.adjust(
        AdjustmentCommand(
            organization_id,
            actor_id,
            source_key,
            Decimal(quantity),
            "receipt",
            "RCPT-PHASE2",
            f"receipt-{uuid4()}",
            uuid4(),
            0,
            unit_cost=Decimal("2.50"),
            currency="USD",
        )
    )


def test_postgres_concurrent_reservations_cannot_oversell(phase2_database) -> None:
    engine, organization_id, actor_id, source_key, _ = phase2_database
    store = PostgresInventoryStore(TenantSessionFactory(engine))
    stock(store, organization_id, actor_id, source_key)

    def attempt(index: int) -> str:
        try:
            store.reserve(
                ReserveCommand(
                    organization_id,
                    actor_id,
                    source_key,
                    "sales_order",
                    uuid4(),
                    Decimal("7"),
                    1,
                    f"concurrent-reservation-{index}-{uuid4()}",
                    uuid4(),
                )
            )
            return "reserved"
        except (ConcurrencyConflict, InsufficientStock):
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, range(2)))

    assert sorted(outcomes) == ["rejected", "reserved"]
    [position] = store.positions_for(organization_id, actor_id)
    assert position.on_hand == Decimal("10")
    assert position.reserved == Decimal("7")
    assert position.available == Decimal("3")
    assert all(item.reconciled for item in store.reconcile(organization_id, actor_id))


def test_postgres_transfer_count_and_retry_reconcile_exactly(phase2_database) -> None:
    engine, organization_id, actor_id, source_key, destination_key = phase2_database
    store = PostgresInventoryStore(TenantSessionFactory(engine))
    stock(store, organization_id, actor_id, source_key)
    command = TransferCommand(
        organization_id,
        actor_id,
        f"TR-{uuid4()}",
        source_key,
        destination_key,
        Decimal("4"),
        1,
        0,
        f"transfer-{uuid4()}",
        uuid4(),
    )
    transferred = store.transfer(command)
    replay = store.transfer(command)
    assert replay.replayed is True
    assert replay.transaction.id == transferred.transaction.id
    assert transferred.source_position.on_hand == Decimal("6")
    assert transferred.destination_position.on_hand == Decimal("4")

    counted = store.post_count(
        CountCommand(
            organization_id,
            actor_id,
            f"COUNT-{uuid4()}",
            destination_key,
            Decimal("3.5"),
            1,
            f"count-{uuid4()}",
            uuid4(),
        )
    )
    assert counted.variance_quantity == Decimal("-0.5")
    assert all(item.reconciled for item in store.reconcile(organization_id, actor_id))


def test_postgres_catalog_commands_are_tenant_scoped_and_idempotent(
    phase2_database,
) -> None:
    engine, organization_id, actor_id, source_key, _ = phase2_database
    store = PostgresCatalogStore(TenantSessionFactory(engine))
    correlation_id = uuid4()
    kit = Product(uuid4(), organization_id, f"KIT-{uuid4()}", "Database Kit", "ea")
    assert store.create_product(kit, actor_id, correlation_id) == kit
    assert store.create_product(kit, actor_id, correlation_id).id == kit.id

    variant = ProductVariant(
        uuid4(), organization_id, kit.id, f"VAR-{uuid4()}", "Blue Kit", {"color": "blue"}
    )
    assert store.create_variant(variant, actor_id, correlation_id) == variant
    conversion = UomConversion(
        uuid4(), organization_id, kit.id, "case", "ea", Decimal("6")
    )
    assert store.add_conversion(conversion, actor_id, correlation_id) == conversion

    supplier = Supplier(
        uuid4(), organization_id, f"SUP-{uuid4()}", "Phase 2 Supplier", "USD"
    )
    assert store.create_supplier(supplier, actor_id, correlation_id) == supplier
    product_supplier = ProductSupplier(
        id=uuid4(),
        organization_id=organization_id,
        product_id=kit.id,
        supplier_id=supplier.id,
        purchase_uom="case",
        currency="USD",
        minimum_order_quantity=Decimal("6"),
        case_pack=Decimal("6"),
        lead_time_days=5,
        preferred=True,
        last_unit_cost=Decimal("8.25"),
        price_breaks=(PriceBreak(Decimal("12"), Decimal("7.75")),),
    )
    assert store.add_product_supplier(product_supplier, actor_id, correlation_id) == product_supplier
    customer = Customer(
        uuid4(), organization_id, f"CUS-{uuid4()}", "Phase 2 Customer", "USD"
    )
    assert store.create_customer(customer, actor_id, correlation_id) == customer

    warehouse = Warehouse(
        uuid4(), organization_id, f"WH-{uuid4()}", "Catalog Warehouse", "UTC"
    )
    assert store.create_warehouse(warehouse, actor_id, correlation_id) == warehouse
    location = BinLocation(
        uuid4(), organization_id, warehouse.id, f"BIN-{uuid4()}", pick_sequence=10
    )
    assert store.create_bin(location, actor_id, correlation_id) == location

    lot = Lot(uuid4(), organization_id, kit.id, f"LOT-{uuid4()}")
    serial = SerialNumber(uuid4(), organization_id, kit.id, f"SER-{uuid4()}")
    assert store.create_lot(lot, actor_id, correlation_id) == lot
    assert store.create_serial(serial, actor_id, correlation_id) == serial

    components = (KitComponent(source_key.product_id, Decimal("2"), "ea"),)
    assert store.define_kit(
        organization_id,
        kit.id,
        components,
        actor_id,
        correlation_id,
        f"kit-{uuid4()}",
    ) == components
    replay_key = f"kit-replay-{uuid4()}"
    assert store.define_kit(
        organization_id, kit.id, components, actor_id, correlation_id, replay_key
    ) == components
    assert store.define_kit(
        organization_id, kit.id, components, actor_id, correlation_id, replay_key
    ) == components

    assert kit.id in {product.id for product in store.products_for(organization_id, actor_id)}
    assert warehouse.id in {
        item.id for item in store.warehouses_for(organization_id, actor_id)
    }


def test_postgres_operational_orders_and_tasks_are_versioned_and_replayable(
    phase2_database,
) -> None:
    engine, organization_id, actor_id, source_key, _ = phase2_database
    catalog = PostgresCatalogStore(TenantSessionFactory(engine))
    store = PostgresOperationsStore(TenantSessionFactory(engine))
    correlation_id = uuid4()
    supplier = Supplier(
        uuid4(), organization_id, f"OPS-SUP-{uuid4()}", "Operations Supplier", "USD"
    )
    catalog.create_supplier(supplier, actor_id, correlation_id)
    order = OperationalOrder(
        id=uuid4(),
        organization_id=organization_id,
        kind=OrderKind.PURCHASE,
        order_number=f"PO-{uuid4()}",
        party_id=supplier.id,
        warehouse_id=source_key.warehouse_id,
        state="draft",
        lines=(
            OrderLine(
                uuid4(), source_key.product_id, Decimal("5.5"), "ea", Decimal("3.25"), "USD"
            ),
        ),
        currency="USD",
    )
    created, replayed = store.create_order(
        order, actor_id, correlation_id, f"create-order-{uuid4()}"
    )
    assert replayed is False
    submit_key = f"submit-order-{uuid4()}"
    submitted, _ = store.transition_order(
        organization_id,
        actor_id,
        OrderKind.PURCHASE,
        created.id,
        "pending_approval",
        1,
        correlation_id,
        submit_key,
    )
    replay, replayed = store.transition_order(
        organization_id,
        actor_id,
        OrderKind.PURCHASE,
        created.id,
        "pending_approval",
        1,
        correlation_id,
        submit_key,
    )
    assert replayed is True
    assert replay.version == submitted.version == 2

    task = WarehouseTask(
        id=uuid4(),
        organization_id=organization_id,
        task_number=f"PICK-{uuid4()}",
        task_type=WarehouseTaskType.PICK,
        warehouse_id=source_key.warehouse_id,
        source_location_id=source_key.location_id,
        product_id=source_key.product_id,
        quantity=Decimal("1.5"),
        uom="ea",
    )
    stored_task, _ = store.create_task(
        task, actor_id, correlation_id, f"create-task-{uuid4()}"
    )
    assigned, _ = store.transition_task(
        organization_id,
        actor_id,
        stored_task.id,
        WarehouseTaskState.ASSIGNED,
        1,
        correlation_id,
        f"assign-task-{uuid4()}",
        actor_id,
    )
    assert assigned.state == WarehouseTaskState.ASSIGNED
    assert store.tasks_for(organization_id, actor_id, source_key.warehouse_id)[0].id == task.id
