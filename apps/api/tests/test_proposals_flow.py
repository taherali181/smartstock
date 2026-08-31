"""Action proposal safety.

The properties that matter: a proposal changes nothing until approved, approval
re-reads the evidence and refuses if it moved, approval is a distinct permission
from proposing, and executing twice cannot create two orders.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from smartstock_api.domain.errors import ConcurrencyConflict
from smartstock_api.domain.proposals import ProposalState
from smartstock_api.main import create_app
from smartstock_api.proposals.builder import (
    ProposalNotPossible,
    build_purchase_proposal,
    detect_purchase_intent,
)

ORG = "00000000-0000-0000-0000-000000000001"
USER = "00000000-0000-0000-0000-000000000001"
HEADERS = {"X-Development-User": USER, "X-Development-Organization": ORG}


def command(extra: dict | None = None) -> dict:
    return {**HEADERS, "Idempotency-Key": str(uuid.uuid4()), **(extra or {})}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SMARTSTOCK_AUTH_MODE", "development")
    monkeypatch.setenv("SMARTSTOCK_INVENTORY_BACKEND", "memory")
    monkeypatch.setenv("SMARTSTOCK_LLM_ROUTE", "deterministic")
    from smartstock_api.config import get_settings

    get_settings.cache_clear()
    with TestClient(create_app()) as running:
        warehouse = running.post(
            "/v1/warehouses",
            json={"code": "WH-MAIN", "name": "Main", "timezone": "UTC"},
            headers=command(),
        ).json()
        location = running.post(
            f"/v1/warehouses/{warehouse['id']}/bins", json={"code": "A-01"}, headers=command()
        ).json()
        product = running.post(
            "/v1/products",
            json={"sku": "SKU-1017", "name": "Widget Pro", "base_uom": "each"},
            headers=command(),
        ).json()
        running.post(
            "/v1/suppliers",
            json={"code": "ACME", "name": "Acme Supply", "currency": "USD"},
            headers=command(),
        )
        running.post(
            "/v1/inventory/adjustments",
            json={
                "product_id": product["id"], "warehouse_id": warehouse["id"],
                "location_id": location["id"], "uom": "each", "quantity_delta": "50",
                "unit_cost": "12.50", "currency": "USD", "reason_code": "opening_balance",
                "business_reference": "SEED", "expected_version": 0,
            },
            headers=command(),
        )
        running.product = product  # type: ignore[attr-defined]
        yield running
    get_settings.cache_clear()


def draft(client: TestClient, question: str) -> dict:
    conversation = client.post("/v1/conversations", headers=HEADERS).json()
    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        json={"content": question},
        headers=HEADERS,
    )
    import json as _json

    for line in response.text.splitlines():
        if line.startswith("data: "):
            block = _json.loads(line[6:])
            if block["type"] == "action_proposal":
                return block
    return {}


# --- intent ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "quantity", "sku"),
    [
        ("raise a PO for 200 of SKU-1017 from Acme", "200", "SKU-1017"),
        ("order 50 SKU-1017", "50", "SKU-1017"),
        ("reorder 30 units of SKU-1017", "30", "SKU-1017"),
    ],
)
def test_purchase_intent_is_detected(question: str, quantity: str, sku: str) -> None:
    intent = detect_purchase_intent(question)
    assert intent is not None
    assert intent.quantity == Decimal(quantity)
    assert intent.sku == sku


def test_a_question_is_not_a_write(client: TestClient) -> None:
    assert detect_purchase_intent("how much SKU-1017 do we have?") is None
    assert detect_purchase_intent("what is running low?") is None


# --- inertness ------------------------------------------------------------


def test_a_proposal_creates_nothing_until_approved(client: TestClient) -> None:
    before = client.get("/v1/purchase-orders", headers=HEADERS).json()["items"]
    block = draft(client, "raise a PO for 200 of SKU-1017 from Acme")

    assert block["state"] == "awaiting_review"
    assert block["executed"] is False
    after = client.get("/v1/purchase-orders", headers=HEADERS).json()["items"]
    assert len(after) == len(before), "drafting a proposal must not create an order"


def test_impact_is_priced_from_records(client: TestClient) -> None:
    block = draft(client, "raise a PO for 200 of SKU-1017 from Acme")
    impact = " ".join(block["impact"])
    assert "12.5" in impact, "unit price must come from the recorded cost"
    assert "2500" in impact, "200 x 12.50 = 2500"
    assert block["payload"]["lines"][0]["quantity"] == "200"


def test_approval_creates_exactly_one_order(client: TestClient) -> None:
    block = draft(client, "raise a PO for 200 of SKU-1017 from Acme")
    before = len(client.get("/v1/purchase-orders", headers=HEADERS).json()["items"])

    approved = client.post(f"/v1/action-proposals/{block['proposal_id']}/approve", headers=HEADERS)
    assert approved.status_code == 200
    body = approved.json()
    assert body["state"] == "succeeded"
    assert body["result"]["order_number"] == block["payload"]["order_number"]

    after = client.get("/v1/purchase-orders", headers=HEADERS).json()["items"]
    assert len(after) == before + 1


def test_approving_twice_is_refused(client: TestClient) -> None:
    block = draft(client, "raise a PO for 200 of SKU-1017 from Acme")
    first = client.post(f"/v1/action-proposals/{block['proposal_id']}/approve", headers=HEADERS)
    assert first.status_code == 200
    orders = len(client.get("/v1/purchase-orders", headers=HEADERS).json()["items"])

    second = client.post(f"/v1/action-proposals/{block['proposal_id']}/approve", headers=HEADERS)
    assert second.status_code == 409
    assert len(client.get("/v1/purchase-orders", headers=HEADERS).json()["items"]) == orders


def test_rejected_proposal_never_executes(client: TestClient) -> None:
    block = draft(client, "raise a PO for 200 of SKU-1017 from Acme")
    rejected = client.post(f"/v1/action-proposals/{block['proposal_id']}/reject", headers=HEADERS)
    assert rejected.status_code == 200 and rejected.json()["state"] == "rejected"

    approved = client.post(f"/v1/action-proposals/{block['proposal_id']}/approve", headers=HEADERS)
    assert approved.status_code == 409


# --- stale evidence -------------------------------------------------------


def test_approval_refuses_stale_evidence() -> None:
    """The property that makes a proposal safe to leave sitting in a queue."""
    from smartstock_api.domain.proposals import ActionProposal

    proposal = ActionProposal(
        id=uuid.uuid4(),
        organization_id=uuid.UUID(ORG),
        created_by=uuid.UUID(USER),
        state=ProposalState.AWAITING_REVIEW,
        source_versions={"product:abc": 1},
        command_payload={"command": "create_purchase_order"},
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    with pytest.raises(ConcurrencyConflict):
        proposal.transition(
            ProposalState.APPROVED,
            organization_id=uuid.UUID(ORG),
            actor_id=uuid.UUID(USER),
            expected_version=proposal.version,
            current_source_versions={"product:abc": 2},  # the product moved
        )


def test_expired_proposal_cannot_be_approved() -> None:
    from smartstock_api.domain.proposals import ActionProposal, InvalidProposalTransition

    proposal = ActionProposal(
        id=uuid.uuid4(),
        organization_id=uuid.UUID(ORG),
        created_by=uuid.UUID(USER),
        state=ProposalState.AWAITING_REVIEW,
        source_versions={},
        command_payload={},
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    with pytest.raises(InvalidProposalTransition):
        proposal.transition(
            ProposalState.APPROVED,
            organization_id=uuid.UUID(ORG),
            actor_id=uuid.UUID(USER),
            expected_version=proposal.version,
            current_source_versions={},
        )


# --- refusals -------------------------------------------------------------


def test_unknown_sku_is_refused_not_invented(client: TestClient) -> None:
    block = draft(client, "raise a PO for 200 of SKU-9999 from Acme")
    assert block == {}, "no proposal may be drafted for a product that does not exist"


def test_unpriced_product_is_refused(client: TestClient) -> None:
    """A purchase order must not carry a price nobody recorded."""
    client.post(
        "/v1/products",
        json={"sku": "SKU-2000", "name": "Unpriced", "base_uom": "each"},
        headers=command(),
    )
    block = draft(client, "raise a PO for 5 of SKU-2000 from Acme")
    assert block == {}


def test_unknown_proposal_is_not_found(client: TestClient) -> None:
    missing = client.post(f"/v1/action-proposals/{uuid.uuid4()}/approve", headers=HEADERS)
    assert missing.status_code == 404


def test_another_organization_cannot_see_the_proposal(client: TestClient) -> None:
    block = draft(client, "raise a PO for 200 of SKU-1017 from Acme")
    other = {
        "X-Development-User": USER,
        "X-Development-Organization": "00000000-0000-0000-0000-0000000000ff",
    }
    assert client.get(f"/v1/action-proposals/{block['proposal_id']}", headers=other).status_code == 404
    assert client.post(f"/v1/action-proposals/{block['proposal_id']}/approve", headers=other).status_code == 404
