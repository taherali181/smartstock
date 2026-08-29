from datetime import timedelta
from uuid import uuid4

import pytest

from smartstock_api.domain.errors import TenantBoundaryViolation
from smartstock_api.infrastructure.tenant_resources import TenantCache, TenantObjectStore


class FakeRedis:
    def __init__(self) -> None:
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


class FakeS3:
    def __init__(self) -> None:
        self.calls = []

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.calls.append((operation, Params, ExpiresIn))
        return f"signed:{operation}:{Params['Key']}"

    def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs, None))


def test_cache_adapter_cannot_collide_between_tenants() -> None:
    redis = FakeRedis()
    cache = TenantCache(redis)
    org_a, org_b = uuid4(), uuid4()
    cache.set(org_a, "positions", "SKU-1", b"alpha", timedelta(minutes=1))
    cache.set(org_b, "positions", "SKU-1", b"bravo", timedelta(minutes=1))
    assert cache.get(org_a, "positions", "SKU-1") == b"alpha"
    assert cache.get(org_b, "positions", "SKU-1") == b"bravo"
    assert len(redis.values) == 2


def test_object_adapter_derives_key_and_metadata_from_authenticated_tenant() -> None:
    s3 = FakeS3()
    store = TenantObjectStore(s3, "private")
    organization_id = uuid4()
    _, key, url = store.create_upload(
        organization_id, "document", "supplier terms.pdf", "application/pdf"
    )
    assert key.startswith(str(organization_id) + "/document/")
    assert url.startswith("signed:put_object:")
    assert s3.calls[0][1]["Metadata"]["organization-id"] == str(organization_id)


def test_object_download_and_delete_reject_another_tenant() -> None:
    store = TenantObjectStore(FakeS3(), "private")
    org_a, org_b = uuid4(), uuid4()
    _, key, _ = store.create_upload(org_a, "export", "inventory.csv", "text/csv")
    with pytest.raises(TenantBoundaryViolation):
        store.create_download(org_b, key)
    with pytest.raises(TenantBoundaryViolation):
        store.delete(org_b, key)
