import os

os.environ["SMARTSTOCK_AUTH_MODE"] = "development"
os.environ["SMARTSTOCK_ENVIRONMENT"] = "test"

from smartstock_api.api.routes.health import liveness
from smartstock_api.config import get_settings
from smartstock_api.main import create_app

get_settings.cache_clear()


def test_health_contract() -> None:
    assert liveness() == {"status": "ok"}


def test_openapi_exposes_versioned_idempotent_command_contract() -> None:
    schema = create_app().openapi()
    operation = schema["paths"]["/v1/inventory/adjustments"]["post"]
    headers = {item["name"]: item for item in operation["parameters"] if item["in"] == "header"}

    assert operation["responses"]["201"]
    assert headers["Idempotency-Key"]["required"] is True
    assert "If-Match" in headers
    assert schema["info"]["version"] == "0.1.0"
    assert "/v1/organizations/current" in schema["paths"]
    assert "/v1/approval-policies" in schema["paths"]
    assert "/v1/feature-flags" in schema["paths"]
    assert "/v1/products" in schema["paths"]
    assert "/v1/products/{product_id}/variants" in schema["paths"]
    assert "/v1/products/{product_id}/suppliers" in schema["paths"]
    assert "/v1/warehouses" in schema["paths"]
    assert "/v1/warehouses/{warehouse_id}/bins" in schema["paths"]
    assert "get" in schema["paths"]["/v1/warehouses/{warehouse_id}/bins"]
    assert "/v1/inventory/reservations" in schema["paths"]
    assert "/v1/inventory/transfers" in schema["paths"]
    assert "/v1/inventory/counts" in schema["paths"]
    assert "/v1/inventory/reconciliation" in schema["paths"]
    assert "/v1/inventory/lots" in schema["paths"]
    assert "/v1/inventory/serials" in schema["paths"]
    assert "/v1/purchase-orders" in schema["paths"]
    assert "/v1/purchase-orders/{order_id}/commands/{command}" in schema["paths"]
    assert "/v1/purchase-orders/{order_id}/receipts" in schema["paths"]
    assert "/v1/sales-orders" in schema["paths"]
    assert "/v1/sales-orders/{order_id}/allocations" in schema["paths"]
    assert "/v1/sales-orders/{order_id}/shipments" in schema["paths"]
    assert "/v1/returns" in schema["paths"]
    assert "/v1/returns/{return_id}/receipt" in schema["paths"]
    assert "/v1/warehouse-tasks" in schema["paths"]
    assert "/v1/warehouse-tasks/{task_id}/commands/{command}" in schema["paths"]
    assert "/v1/warehouse-tasks/{task_id}/receipt" in schema["paths"]
    assert "/v1/warehouse-tasks/{task_id}/count" in schema["paths"]
    assert "/v1/warehouse-tasks/{task_id}/transfer/ship" in schema["paths"]
    assert "/v1/warehouse-tasks/{task_id}/transfer/receive" in schema["paths"]

    for path in (
        "/v1/products",
        "/v1/warehouses",
        "/v1/inventory/reservations",
        "/v1/inventory/transfers",
        "/v1/inventory/counts",
        "/v1/inventory/lots",
        "/v1/inventory/serials",
        "/v1/purchase-orders",
        "/v1/purchase-orders/{order_id}/receipts",
        "/v1/sales-orders",
        "/v1/sales-orders/{order_id}/allocations",
        "/v1/sales-orders/{order_id}/shipments",
        "/v1/returns",
        "/v1/returns/{return_id}/receipt",
        "/v1/warehouse-tasks",
        "/v1/warehouse-tasks/{task_id}/receipt",
        "/v1/warehouse-tasks/{task_id}/count",
        "/v1/warehouse-tasks/{task_id}/transfer/ship",
        "/v1/warehouse-tasks/{task_id}/transfer/receive",
    ):
        parameters = schema["paths"][path]["post"]["parameters"]
        assert any(
            item["name"] == "Idempotency-Key" and item["required"] is True
            for item in parameters
        )
