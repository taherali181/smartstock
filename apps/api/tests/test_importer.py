from decimal import Decimal
from uuid import uuid4

from smartstock_api.domain.importer import RestockDemoImporter


def test_restock_demo_import_is_deterministic_and_reconciles() -> None:
    organization_id = uuid4()
    snapshot = {
        "products": [{"id": 1, "sku": "A", "base_uom": "ea"}],
        "warehouses": [{"id": 4, "code": "MAIN"}],
        "bins": [{"id": 7, "warehouse_id": 4, "code": "A-01"}],
        "inventory": [
            {"product_id": 1, "warehouse_id": 4, "bin_id": 7, "quantity": "2.5"},
            {"product_id": 1, "warehouse_id": 4, "bin_id": 7, "quantity": "3.75"},
        ],
    }
    first = RestockDemoImporter.plan(organization_id, snapshot)
    second = RestockDemoImporter.plan(organization_id, snapshot)

    assert first == second
    assert first.reconciled is True
    assert first.imported_quantity == Decimal("6.25")
