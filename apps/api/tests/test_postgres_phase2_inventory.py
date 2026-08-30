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
    StockCondition,
    StockKey,
    TransferCommand,
)
from smartstock_api.domain.operations import (
    AllocationPostingLine,
    OperationalOrder,
    OrderKind,
    OrderLine,
    ReceiptPostingLine,
    ReturnAuthorization,
    ReturnLine,
    ReturnReceiptLine,
    ShipmentPostingLine,
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


def test_postgres_task_bound_transfer_ships_and_receives_with_discrepancy(
    phase2_database,
) -> None:
    engine, organization_id, actor_id, source_key, destination_key = phase2_database
    sessions = TenantSessionFactory(engine)
    inventory = PostgresInventoryStore(sessions)
    operations = PostgresOperationsStore(sessions)
    stock(inventory, organization_id, actor_id, source_key)
    correlation_id = uuid4()
    task = WarehouseTask(
        id=uuid4(), organization_id=organization_id,
        task_number=f"TR-TASK-{uuid4()}", task_type=WarehouseTaskType.TRANSFER,
        warehouse_id=source_key.warehouse_id,
        destination_warehouse_id=destination_key.warehouse_id,
        source_location_id=source_key.location_id,
        destination_location_id=destination_key.location_id,
        product_id=source_key.product_id, quantity=Decimal("4"), uom=source_key.uom,
        expected_position_version=1,
    )
    stored, _ = operations.create_task(
        task, actor_id, correlation_id, f"create-transfer-task-{uuid4()}"
    )
    started, _ = operations.transition_task(
        organization_id, actor_id, stored.id, WarehouseTaskState.IN_PROGRESS,
        stored.version, correlation_id, f"start-transfer-task-{uuid4()}",
    )
    ship_key = f"ship-transfer-task-{uuid4()}"
    shipped = operations.ship_transfer_task(
        organization_id, actor_id, task.id, started.version, correlation_id, ship_key,
    )
    assert shipped.task.state == WarehouseTaskState.COMPLETED
    assert shipped.shipment.source_position.on_hand == Decimal("6")
    assert shipped.shipment.destination_position.on_hand == Decimal("0")
    assert operations.ship_transfer_task(
        organization_id, actor_id, task.id, started.version, correlation_id, ship_key,
    ).replayed is True

    receipt_started, _ = operations.transition_task(
        organization_id, actor_id, shipped.receipt_task.id,
        WarehouseTaskState.IN_PROGRESS, shipped.receipt_task.version,
        correlation_id, f"start-transfer-receipt-{uuid4()}",
    )
    receive_key = f"receive-transfer-task-{uuid4()}"
    received = operations.receive_transfer_task(
        organization_id, actor_id, receipt_started.id, Decimal("3.5"),
        receipt_started.version, correlation_id, receive_key,
    )
    assert received.task.state == WarehouseTaskState.COMPLETED
    assert received.receipt.state == "discrepancy_review"
    assert received.receipt.discrepancy_quantity == Decimal("0.5")
    assert received.receipt.destination_position.on_hand == Decimal("3.5")
    assert operations.receive_transfer_task(
        organization_id, actor_id, receipt_started.id, Decimal("3.5"),
        receipt_started.version, correlation_id, receive_key,
    ).replayed is True
    assert all(item.reconciled for item in inventory.reconcile(organization_id, actor_id))


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
    version = submitted.version
    for target in ("approved", "sent", "acknowledged"):
        transitioned, _ = store.transition_order(
            organization_id,
            actor_id,
            OrderKind.PURCHASE,
            created.id,
            target,
            version,
            correlation_id,
            f"order-{target}-{uuid4()}",
        )
        version = transitioned.version
    automatic_tasks = store.tasks_for(organization_id, actor_id, source_key.warehouse_id)
    assert any(
        item.task_type == WarehouseTaskType.RECEIVE and item.reference_id == order.id
        for item in automatic_tasks
    )

    receipt_id = uuid4()
    receipt_line = ReceiptPostingLine(
        uuid4(),
        order.lines[0].id,
        source_key.location_id,
        Decimal("4"),
        Decimal("0.5"),
    )
    receipt_key = f"receipt-{uuid4()}"
    received = store.post_receipt(
        organization_id,
        actor_id,
        order.id,
        receipt_id,
        f"RCPT-{uuid4()}",
        (receipt_line,),
        version,
        Decimal("0"),
        correlation_id,
        receipt_key,
    )
    assert received.order.state == "partially_received"
    assert received.order.lines[0].received_or_shipped_quantity == Decimal("4.5")
    replayed_receipt = store.post_receipt(
        organization_id,
        actor_id,
        order.id,
        receipt_id,
        received.receipt.receipt_number,
        (receipt_line,),
        version,
        Decimal("0"),
        correlation_id,
        receipt_key,
    )
    assert replayed_receipt.replayed is True

    inventory = PostgresInventoryStore(TenantSessionFactory(engine))
    quarantined_key = StockKey(
        organization_id,
        source_key.product_id,
        source_key.warehouse_id,
        source_key.location_id,
        source_key.uom,
        condition=StockCondition.QUARANTINED,
    )
    positions = {position.key: position for position in inventory.positions_for(
        organization_id, actor_id
    )}
    assert positions[source_key].on_hand == Decimal("4")
    assert positions[quarantined_key].on_hand == Decimal("0.5")
    assert all(item.reconciled for item in inventory.reconcile(organization_id, actor_id))
    receipt_tasks = store.tasks_for(organization_id, actor_id, source_key.warehouse_id)
    assert any(
        item.task_type == WarehouseTaskType.PUTAWAY and item.reference_id == receipt_id
        for item in receipt_tasks
    )

    final_receipt = store.post_receipt(
        organization_id,
        actor_id,
        order.id,
        uuid4(),
        f"RCPT-{uuid4()}",
        (
            ReceiptPostingLine(
                uuid4(),
                order.lines[0].id,
                source_key.location_id,
                Decimal("1.25"),
                Decimal("0"),
                expected_sellable_version=1,
            ),
        ),
        received.order.version,
        Decimal("10"),
        correlation_id,
        f"receipt-{uuid4()}",
    )
    assert final_receipt.order.state == "received"
    assert final_receipt.order.lines[0].received_or_shipped_quantity == Decimal("5.75")
    assert final_receipt.order.lines[0].open_quantity == Decimal("0")

    customer = Customer(
        uuid4(), organization_id, f"OPS-CUS-{uuid4()}", "Operations Customer", "USD"
    )
    catalog.create_customer(customer, actor_id, correlation_id)
    sales_order = OperationalOrder(
        id=uuid4(), organization_id=organization_id, kind=OrderKind.SALES,
        order_number=f"SO-{uuid4()}", party_id=customer.id,
        warehouse_id=source_key.warehouse_id, state="quote",
        lines=(OrderLine(
            uuid4(), source_key.product_id, Decimal("3"), "ea", Decimal("8"), "USD"
        ),), currency="USD",
    )
    created_sales, _ = store.create_order(
        sales_order, actor_id, correlation_id, f"create-sales-{uuid4()}"
    )
    sales_version = created_sales.version
    for target in ("draft", "confirmed"):
        transitioned_sales, _ = store.transition_order(
            organization_id, actor_id, OrderKind.SALES, sales_order.id, target,
            sales_version, correlation_id, f"sales-{target}-{uuid4()}",
        )
        sales_version = transitioned_sales.version
    allocation_id = uuid4()
    allocation_line = AllocationPostingLine(
        uuid4(), sales_order.lines[0].id, source_key.location_id, Decimal("3"), 2
    )
    allocation_key = f"allocation-{uuid4()}"
    allocated = store.allocate_sales_order(
        organization_id, actor_id, sales_order.id, allocation_id, (allocation_line,),
        sales_version, correlation_id, allocation_key,
    )
    assert allocated.order.state == "allocated"
    allocation_replay = store.allocate_sales_order(
        organization_id, actor_id, sales_order.id, allocation_id, (allocation_line,),
        sales_version, correlation_id, allocation_key,
    )
    assert allocation_replay.replayed is True
    allocated_positions = {
        position.key: position for position in inventory.positions_for(organization_id, actor_id)
    }
    assert allocated_positions[source_key].reserved == Decimal("3")
    assert all(item.reconciled for item in inventory.reconcile(organization_id, actor_id))
    assert any(
        item.task_type == WarehouseTaskType.PICK and item.reference_id == allocation_id
        for item in store.tasks_for(organization_id, actor_id, source_key.warehouse_id)
    )
    picking_sales, _ = store.transition_order(
        organization_id, actor_id, OrderKind.SALES, sales_order.id, "picking",
        allocated.order.version, correlation_id, f"start-picking-{uuid4()}",
    )
    shipment_id = uuid4()
    shipment_line = ShipmentPostingLine(
        uuid4(), sales_order.lines[0].id, allocated.allocation.reservation_ids[0], 1, 3
    )
    shipment_key = f"shipment-{uuid4()}"
    shipped = store.post_shipment(
        organization_id, actor_id, sales_order.id, shipment_id, (shipment_line,),
        picking_sales.version, correlation_id, shipment_key,
    )
    assert shipped.order.state == "shipped"
    shipped_replay = store.post_shipment(
        organization_id, actor_id, sales_order.id, shipment_id, (shipment_line,),
        picking_sales.version, correlation_id, shipment_key,
    )
    assert shipped_replay.replayed is True
    shipped_positions = {
        position.key: position for position in inventory.positions_for(organization_id, actor_id)
    }
    assert shipped_positions[source_key].on_hand == Decimal("2.25")
    assert shipped_positions[source_key].reserved == Decimal("0")
    assert all(item.reconciled for item in inventory.reconcile(organization_id, actor_id))
    return_id = uuid4()
    rma = ReturnAuthorization(
        return_id, organization_id, f"RMA-{uuid4()}", sales_order.id,
        source_key.warehouse_id, "requested",
        (ReturnLine(
            uuid4(), sales_order.lines[0].id, source_key.product_id,
            Decimal("1"), "ea", "damaged"
        ),),
    )
    created_return, _ = store.create_return(
        rma, actor_id, correlation_id, f"create-return-{uuid4()}"
    )
    authorized_return, _ = store.transition_return(
        organization_id, actor_id, return_id, "authorized", created_return.version,
        correlation_id, f"authorize-return-{uuid4()}",
    )
    return_receipt_key = f"receive-return-{uuid4()}"
    received_return = store.receive_return(
        organization_id, actor_id, return_id,
        (ReturnReceiptLine(rma.lines[0].id, source_key.location_id, 1),),
        authorized_return.version, correlation_id, return_receipt_key,
    )
    assert received_return.return_authorization.state == "received"
    return_replay = store.receive_return(
        organization_id, actor_id, return_id,
        (ReturnReceiptLine(rma.lines[0].id, source_key.location_id, 1),),
        authorized_return.version, correlation_id, return_receipt_key,
    )
    assert return_replay.replayed is True
    returned_positions = {
        position.key: position for position in inventory.positions_for(organization_id, actor_id)
    }
    assert returned_positions[quarantined_key].on_hand == Decimal("1.5")
    assert all(item.reconciled for item in inventory.reconcile(organization_id, actor_id))

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
    assert task.id in {
        item.id for item in store.tasks_for(organization_id, actor_id, source_key.warehouse_id)
    }

    count_position = {
        position.key: position for position in inventory.positions_for(organization_id, actor_id)
    }[source_key]
    count_task = WarehouseTask(
        id=uuid4(),
        organization_id=organization_id,
        task_number=f"COUNT-{uuid4()}",
        task_type=WarehouseTaskType.COUNT,
        warehouse_id=source_key.warehouse_id,
        source_location_id=source_key.location_id,
        product_id=source_key.product_id,
        uom=source_key.uom,
        expected_position_version=count_position.version,
    )
    stored_count_task, _ = store.create_task(
        count_task, actor_id, correlation_id, f"create-count-task-{uuid4()}"
    )
    started_count_task, _ = store.transition_task(
        organization_id,
        actor_id,
        stored_count_task.id,
        WarehouseTaskState.IN_PROGRESS,
        stored_count_task.version,
        correlation_id,
        f"start-count-task-{uuid4()}",
    )
    count_key = f"complete-count-task-{uuid4()}"
    counted_quantity = count_position.on_hand + Decimal("0.25")
    completed_count = store.complete_count_task(
        organization_id,
        actor_id,
        stored_count_task.id,
        counted_quantity,
        started_count_task.version,
        correlation_id,
        count_key,
    )
    assert completed_count.task.state == WarehouseTaskState.COMPLETED
    assert completed_count.count.variance_quantity == Decimal("0.25")
    replayed_count = store.complete_count_task(
        organization_id,
        actor_id,
        stored_count_task.id,
        counted_quantity,
        started_count_task.version,
        correlation_id,
        count_key,
    )
    assert replayed_count.replayed is True
    assert replayed_count.count.position.on_hand == counted_quantity

    stale_snapshot = replayed_count.count.position
    stale_task = WarehouseTask(
        id=uuid4(),
        organization_id=organization_id,
        task_number=f"COUNT-{uuid4()}",
        task_type=WarehouseTaskType.COUNT,
        warehouse_id=source_key.warehouse_id,
        source_location_id=source_key.location_id,
        product_id=source_key.product_id,
        uom=source_key.uom,
        expected_position_version=stale_snapshot.version,
    )
    stored_stale_task, _ = store.create_task(
        stale_task, actor_id, correlation_id, f"create-stale-count-{uuid4()}"
    )
    started_stale_task, _ = store.transition_task(
        organization_id,
        actor_id,
        stored_stale_task.id,
        WarehouseTaskState.IN_PROGRESS,
        stored_stale_task.version,
        correlation_id,
        f"start-stale-count-{uuid4()}",
    )
    changed = inventory.adjust(
        AdjustmentCommand(
            organization_id,
            actor_id,
            source_key,
            Decimal("1"),
            "late_adjustment",
            stale_task.task_number,
            f"late-adjustment-{uuid4()}",
            correlation_id,
            stale_snapshot.version,
        )
    )
    with pytest.raises(ConcurrencyConflict):
        store.complete_count_task(
            organization_id,
            actor_id,
            stale_task.id,
            changed.position.on_hand,
            started_stale_task.version,
            correlation_id,
            f"complete-stale-count-{uuid4()}",
        )
    assert store.task(organization_id, actor_id, stale_task.id).state == (
        WarehouseTaskState.IN_PROGRESS
    )
    assert all(item.reconciled for item in inventory.reconcile(organization_id, actor_id))
