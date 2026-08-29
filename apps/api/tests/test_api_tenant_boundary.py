from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from smartstock_api.api.auth import Principal
from smartstock_api.api.routes.inventory import list_positions
from smartstock_api.api.routes.platform import current_organization
from smartstock_api.domain.inventory import AdjustmentCommand, InventoryLedger, StockKey
from smartstock_api.domain.platform import InMemoryPlatformStore, Organization


def post_for(ledger, organization_id, actor_id, product_id):
    ledger.adjust(
        AdjustmentCommand(
            organization_id=organization_id,
            actor_id=actor_id,
            stock_key=StockKey(
                organization_id=organization_id,
                product_id=product_id,
                warehouse_id=uuid4(),
                location_id=uuid4(),
                uom="ea",
            ),
            quantity_delta=Decimal("10"),
            reason_code="test",
            business_reference="TEST",
            idempotency_key=f"test-{uuid4()}",
            correlation_id=uuid4(),
            expected_version=0,
        )
    )


def test_inventory_api_uses_authenticated_tenant_not_request_data() -> None:
    ledger = InventoryLedger()
    org_a, org_b, user_a = uuid4(), uuid4(), uuid4()
    product_a, product_b = uuid4(), uuid4()
    post_for(ledger, org_a, user_a, product_a)
    post_for(ledger, org_b, uuid4(), product_b)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(inventory_ledger=ledger)))
    principal = Principal(user_a, org_a, frozenset({"inventory.view"}), frozenset())

    response = list_positions(request, principal, limit=100)

    assert [item.product_id for item in response.items] == [product_a]
    assert all(item.product_id != product_b for item in response.items)


def test_platform_api_resolves_current_organization_from_principal() -> None:
    store = InMemoryPlatformStore()
    org_a, org_b, user_a = uuid4(), uuid4(), uuid4()
    store.add_organization(Organization(org_a, "alpha", "Alpha", "USD"))
    store.add_organization(Organization(org_b, "bravo", "Bravo", "CAD"))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(platform_store=store)))
    principal = Principal(user_a, org_a, frozenset(), frozenset())

    response = current_organization(request, principal)

    assert response.id == str(org_a)
    assert response.slug == "alpha"
