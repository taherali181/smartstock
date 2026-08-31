from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from smartstock_api.api.auth import Principal, get_principal
from smartstock_api.api.problem import domain_problem
from smartstock_api.api.routes import operations as operations_routes
from smartstock_api.domain.catalog import Customer, Supplier
from smartstock_api.domain.errors import DomainError
from smartstock_api.domain.operations import WarehouseTaskType
from smartstock_api.infrastructure.database import TenantSessionFactory
from smartstock_api.infrastructure.postgres_catalog import PostgresCatalogStore
from smartstock_api.infrastructure.postgres_inventory import PostgresInventoryStore
from smartstock_api.infrastructure.postgres_operations import PostgresOperationsStore
from test_postgres_phase2_inventory import phase2_database, stock

pytestmark = pytest.mark.postgres


def _client(
    operations: PostgresOperationsStore,
    organization_id: UUID,
    actor_id: UUID,
) -> TestClient:
    app = FastAPI()
    app.state.operations_store = operations
    app.add_exception_handler(DomainError, domain_problem)
    app.include_router(operations_routes.router)
    app.dependency_overrides[get_principal] = lambda: Principal(
        actor_id,
        organization_id,
        frozenset({"*"}),
        frozenset(),
    )

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        request.state.correlation_id = uuid4()
        return await call_next(request)

    return TestClient(app)


def test_postgres_golden_purchase_and_sales_paths_through_http(
    phase2_database,
) -> None:
    engine, organization_id, actor_id, source_key, _ = phase2_database
    sessions = TenantSessionFactory(engine)
    catalog = PostgresCatalogStore(sessions)
    inventory = PostgresInventoryStore(sessions)
    operations = PostgresOperationsStore(sessions)
    correlation_id = uuid4()
    supplier = Supplier(
        uuid4(),
        organization_id,
        f"GOLD-SUP-{uuid4()}",
        "Golden Supplier",
        "USD",
    )
    customer = Customer(
        uuid4(),
        organization_id,
        f"GOLD-CUS-{uuid4()}",
        "Golden Customer",
        "USD",
    )
    catalog.create_supplier(supplier, actor_id, correlation_id)
    catalog.create_customer(customer, actor_id, correlation_id)
    opening = stock(inventory, organization_id, actor_id, source_key, "10")
    client = _client(operations, organization_id, actor_id)

    purchase = client.post(
        "/v1/purchase-orders",
        headers={"Idempotency-Key": f"gold-create-po-{uuid4()}"},
        json={
            "order_number": f"PO-GOLD-{uuid4()}",
            "party_id": str(supplier.id),
            "warehouse_id": str(source_key.warehouse_id),
            "currency": "USD",
            "lines": [
                {
                    "product_id": str(source_key.product_id),
                    "quantity": "10",
                    "uom": source_key.uom,
                    "unit_price": "4.25",
                    "currency": "USD",
                }
            ],
        },
    )
    assert purchase.status_code == 201
    purchase_body = purchase.json()
    for command in ("submit", "approve", "send", "acknowledge"):
        purchase = client.post(
            f"/v1/purchase-orders/{purchase_body['id']}/commands/{command}",
            headers={"Idempotency-Key": f"gold-po-{command}-{uuid4()}"},
            json={"expected_version": purchase_body["version"]},
        )
        assert purchase.status_code == 200
        purchase_body = purchase.json()

    receive_task = next(
        task
        for task in operations.tasks_for(
            organization_id,
            actor_id,
            source_key.warehouse_id,
        )
        if task.reference_id == UUID(purchase_body["id"])
        and task.task_type == WarehouseTaskType.RECEIVE
    )
    started = client.post(
        f"/v1/warehouse-tasks/{receive_task.id}/commands/start",
        headers={"Idempotency-Key": f"gold-start-receive-{uuid4()}"},
        json={"expected_version": receive_task.version, "assigned_to": None},
    )
    assert started.status_code == 200
    receipt = client.post(
        f"/v1/warehouse-tasks/{receive_task.id}/receipt",
        headers={"Idempotency-Key": f"gold-receipt-{uuid4()}"},
        json={
            "receipt_number": f"RCPT-GOLD-{uuid4()}",
            "expected_order_version": purchase_body["version"],
            "expected_task_version": started.json()["version"],
            "over_receipt_tolerance_percent": "0",
            "lines": [
                {
                    "order_line_id": purchase_body["lines"][0]["id"],
                    "location_id": str(source_key.location_id),
                    "accepted_quantity": "4",
                    "rejected_quantity": "0",
                    "expected_sellable_version": opening.position.version,
                    "expected_quarantine_version": 0,
                }
            ],
        },
    )
    assert receipt.status_code == 201
    receipt_body = receipt.json()
    assert receipt_body["task"]["state"] == "completed"
    assert receipt_body["receipt"]["order"]["state"] == "partially_received"
    assert receipt_body["follow_up_task"]["task_type"] == "receive"
    assert any(
        task.task_type == WarehouseTaskType.PUTAWAY
        and task.reference_id == UUID(receipt_body["receipt"]["id"])
        for task in operations.tasks_for(
            organization_id,
            actor_id,
            source_key.warehouse_id,
        )
    )
    after_receipt = next(
        position
        for position in inventory.positions_for(organization_id, actor_id)
        if position.key == source_key
    )
    assert after_receipt.on_hand == Decimal("14")

    sales = client.post(
        "/v1/sales-orders",
        headers={"Idempotency-Key": f"gold-create-so-{uuid4()}"},
        json={
            "order_number": f"SO-GOLD-{uuid4()}",
            "party_id": str(customer.id),
            "warehouse_id": str(source_key.warehouse_id),
            "currency": "USD",
            "lines": [
                {
                    "product_id": str(source_key.product_id),
                    "quantity": "15",
                    "uom": source_key.uom,
                    "unit_price": "9.00",
                    "currency": "USD",
                }
            ],
        },
    )
    assert sales.status_code == 201
    sales_body = sales.json()
    for command in ("convert-to-draft", "confirm"):
        sales = client.post(
            f"/v1/sales-orders/{sales_body['id']}/commands/{command}",
            headers={"Idempotency-Key": f"gold-so-{command}-{uuid4()}"},
            json={"expected_version": sales_body["version"]},
        )
        assert sales.status_code == 200
        sales_body = sales.json()

    over_allocation = client.post(
        f"/v1/sales-orders/{sales_body['id']}/allocations",
        headers={"Idempotency-Key": f"gold-over-allocate-{uuid4()}"},
        json={
            "expected_order_version": sales_body["version"],
            "lines": [
                {
                    "order_line_id": sales_body["lines"][0]["id"],
                    "location_id": str(source_key.location_id),
                    "quantity": "15",
                    "expected_position_version": after_receipt.version,
                }
            ],
        },
    )
    assert over_allocation.status_code == 409
    assert over_allocation.headers["content-type"].startswith(
        "application/problem+json"
    )
    problem = over_allocation.json()
    assert problem["status"] == 409
    assert problem["type"].endswith("/insufficient_stock")
    assert problem["instance"].endswith("/allocations")

    sales = client.post(
        "/v1/sales-orders",
        headers={"Idempotency-Key": f"gold-create-fulfillable-so-{uuid4()}"},
        json={
            "order_number": f"SO-GOLD-FULFILLABLE-{uuid4()}",
            "party_id": str(customer.id),
            "warehouse_id": str(source_key.warehouse_id),
            "currency": "USD",
            "lines": [
                {
                    "product_id": str(source_key.product_id),
                    "quantity": "6",
                    "uom": source_key.uom,
                    "unit_price": "9.00",
                    "currency": "USD",
                }
            ],
        },
    )
    assert sales.status_code == 201
    sales_body = sales.json()
    for command in ("convert-to-draft", "confirm"):
        sales = client.post(
            f"/v1/sales-orders/{sales_body['id']}/commands/{command}",
            headers={"Idempotency-Key": f"gold-fulfillable-so-{command}-{uuid4()}"},
            json={"expected_version": sales_body["version"]},
        )
        assert sales.status_code == 200
        sales_body = sales.json()

    allocation = client.post(
        f"/v1/sales-orders/{sales_body['id']}/allocations",
        headers={"Idempotency-Key": f"gold-allocate-{uuid4()}"},
        json={
            "expected_order_version": sales_body["version"],
            "lines": [
                {
                    "order_line_id": sales_body["lines"][0]["id"],
                    "location_id": str(source_key.location_id),
                    "quantity": "6",
                    "expected_position_version": after_receipt.version,
                }
            ],
        },
    )
    assert allocation.status_code == 201
    allocation_body = allocation.json()
    picking = client.post(
        f"/v1/sales-orders/{sales_body['id']}/commands/start-picking",
        headers={"Idempotency-Key": f"gold-start-picking-{uuid4()}"},
        json={"expected_version": allocation_body["order"]["version"]},
    )
    assert picking.status_code == 200
    shipment = client.post(
        f"/v1/sales-orders/{sales_body['id']}/shipments",
        headers={"Idempotency-Key": f"gold-ship-{uuid4()}"},
        json={
            "expected_order_version": picking.json()["version"],
            "lines": [
                {
                    "order_line_id": sales_body["lines"][0]["id"],
                    "reservation_id": allocation_body["reservation_ids"][0],
                    "expected_reservation_version": 1,
                    "expected_position_version": after_receipt.version + 1,
                }
            ],
        },
    )
    assert shipment.status_code == 201
    assert shipment.json()["order"]["state"] == "shipped"
    after_shipment = next(
        position
        for position in inventory.positions_for(organization_id, actor_id)
        if position.key == source_key
    )
    assert after_shipment.on_hand == Decimal("8")
    assert after_shipment.reserved == Decimal("0")
    assert all(
        result.reconciled
        for result in inventory.reconcile(organization_id, actor_id)
    )
    with sessions.session(organization_id, actor_id) as session:
        reservation_status = session.execute(
            text(
                """
                SELECT status FROM reservations
                WHERE organization_id=:organization_id AND id=:reservation_id
                """
            ),
            {
                "organization_id": organization_id,
                "reservation_id": allocation_body["reservation_ids"][0],
            },
        ).scalar_one()
        reasons = set(
            session.execute(
                text(
                    """
                    SELECT reason_code FROM inventory_transactions
                    WHERE organization_id=:organization_id
                    """
                ),
                {"organization_id": organization_id},
            ).scalars()
        )
    assert reservation_status == "consumed"
    assert {"receipt", "shipment"}.issubset(reasons)
