from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from smartstock_api.domain.errors import TenantBoundaryViolation
from smartstock_api.domain.platform import (
    ApprovalPolicy,
    InMemoryPlatformStore,
    Membership,
    Organization,
    Role,
    TenantKeyspace,
    WarehouseGrant,
)
from smartstock_api.jobs.envelope import JobEnvelope, sign, verify
from smartstock_api.security.credentials import issue_api_credential, verify_api_credential


def fixture_store():
    store = InMemoryPlatformStore()
    org_a, org_b, user_a, user_b = uuid4(), uuid4(), uuid4(), uuid4()
    store.add_organization(Organization(org_a, "alpha", "Alpha", "USD"))
    store.add_organization(Organization(org_b, "bravo", "Bravo", "CAD"))
    store.add_membership(Membership(org_a, user_a, Role.OWNER))
    store.add_membership(Membership(org_b, user_b, Role.OWNER))
    return store, org_a, org_b, user_a, user_b


def test_identical_logical_cache_keys_are_tenant_namespaced() -> None:
    _, org_a, org_b, _, _ = fixture_store()
    key_a = TenantKeyspace.cache(org_a, "inventory", "SKU-1")
    key_b = TenantKeyspace.cache(org_b, "inventory", "SKU-1")
    assert key_a != key_b
    assert str(org_a) in key_a
    assert str(org_b) in key_b


def test_object_keys_reject_traversal_and_cross_tenant_access() -> None:
    _, org_a, org_b, _, _ = fixture_store()
    with pytest.raises(ValueError):
        TenantKeyspace.object(org_a, "exports", uuid4(), "../secret.csv")
    object_key = TenantKeyspace.object(org_a, "exports", uuid4(), "stock.csv")
    with pytest.raises(TenantBoundaryViolation):
        TenantKeyspace.assert_owned(org_b, object_key)


def test_memberships_grants_policies_flags_and_jobs_are_tenant_scoped() -> None:
    store, org_a, org_b, user_a, user_b = fixture_store()
    warehouse_a, warehouse_b = uuid4(), uuid4()
    store.grant_warehouse(WarehouseGrant(org_a, user_a, warehouse_a))
    store.grant_warehouse(WarehouseGrant(org_b, user_b, warehouse_b))
    assert store.warehouses_for(org_a, user_a) == {warehouse_a}
    assert store.warehouses_for(org_a, user_b) == set()

    policy = ApprovalPolicy(
        uuid4(),
        org_a,
        "purchase_order.approve",
        2,
        "purchasing.approve",
        Decimal("5000"),
        "USD",
    )
    store.add_policy(policy)
    assert store.policies_for(org_a, "purchase_order.approve") == [policy]
    assert store.policies_for(org_b, "purchase_order.approve") == []
    assert policy.applies("purchase_order.approve", Decimal("6000"), "USD")
    assert not policy.applies("purchase_order.approve", Decimal("6000"), "CAD")

    store.set_flag(org_a, "rag.enabled", True, {"cohort": "beta"})
    assert store.flag(org_a, "rag.enabled") == (True, {"cohort": "beta"})
    assert store.flag(org_b, "rag.enabled") is None

    job = store.enqueue(org_a, user_a, "exports", "inventory_export", uuid4(), {"sku": "1"})
    assert store.job(org_a, job.id) == job
    assert store.job(org_b, job.id) is None


def test_job_payload_cannot_override_signed_tenant() -> None:
    _, org_a, org_b, user_a, _ = fixture_store()
    envelope = JobEnvelope(
        job_id=uuid4(),
        organization_id=org_a,
        actor_id=user_a,
        correlation_id=uuid4(),
        job_type="inventory_export",
        payload={"organization_id": str(org_b)},
        issued_at=datetime.now(UTC),
    )
    signature = sign(envelope, "test-secret")
    with pytest.raises(TenantBoundaryViolation):
        verify(envelope, signature, "test-secret")


def test_tampered_job_envelope_is_rejected() -> None:
    _, org_a, _, user_a, _ = fixture_store()
    issued = datetime.now(UTC)
    original = JobEnvelope(
        uuid4(), org_a, user_a, uuid4(), "document_index", {"object_id": "1"}, issued
    )
    signature = sign(original, "test-secret")
    tampered = JobEnvelope(
        original.job_id,
        original.organization_id,
        original.actor_id,
        original.correlation_id,
        original.job_type,
        {"object_id": "2"},
        original.issued_at,
    )
    with pytest.raises(TenantBoundaryViolation):
        verify(tampered, signature, "test-secret")


def test_api_credentials_are_one_way_verifiable() -> None:
    issued = issue_api_credential("test")
    assert issued.token.startswith(issued.prefix + ".")
    assert issued.token not in issued.digest
    assert verify_api_credential(issued.token, issued.prefix, issued.digest)
    assert not verify_api_credential(issued.token + "x", issued.prefix, issued.digest)
