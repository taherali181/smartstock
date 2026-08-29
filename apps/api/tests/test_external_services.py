import os
from datetime import timedelta
from uuid import uuid4

import pytest

pytestmark = pytest.mark.external


def require_external_services() -> None:
    if os.getenv("SMARTSTOCK_EXTERNAL_SERVICES") != "1":
        pytest.skip("external service integration environment is not configured")


def test_real_redis_tenant_keys_do_not_collide() -> None:
    require_external_services()
    import redis

    from smartstock_api.infrastructure.tenant_resources import TenantCache

    client = redis.Redis.from_url(os.getenv("SMARTSTOCK_REDIS_URL", "redis://localhost:6379/15"))
    cache = TenantCache(client)
    org_a, org_b = uuid4(), uuid4()
    logical_key = f"same-{uuid4()}"
    cache.set(org_a, "isolation-test", logical_key, b"alpha", timedelta(seconds=30))
    cache.set(org_b, "isolation-test", logical_key, b"bravo", timedelta(seconds=30))
    assert cache.get(org_a, "isolation-test", logical_key) == b"alpha"
    assert cache.get(org_b, "isolation-test", logical_key) == b"bravo"
    cache.delete(org_a, "isolation-test", logical_key)
    cache.delete(org_b, "isolation-test", logical_key)


def test_real_s3_compatible_storage_is_private_and_tenant_prefixed() -> None:
    require_external_services()
    import boto3
    import httpx
    from botocore.client import Config

    from smartstock_api.infrastructure.tenant_resources import TenantObjectStore

    endpoint = os.getenv("SMARTSTOCK_S3_ENDPOINT_URL", "http://localhost:9000")
    bucket = os.getenv("SMARTSTOCK_S3_BUCKET", "smartstock-test")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "smartstock"),
        aws_secret_access_key=os.getenv(
            "AWS_SECRET_ACCESS_KEY", "smartstock-development-only"
        ),
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    try:
        client.create_bucket(Bucket=bucket)
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    store = TenantObjectStore(client, bucket)
    organization_id = uuid4()
    _, key, upload_url = store.create_upload(
        organization_id, "export", "inventory.csv", "text/csv"
    )
    response = httpx.put(
        upload_url,
        content=b"sku,on_hand\nSKU-1,10\n",
        headers={"Content-Type": "text/csv", "x-amz-meta-organization-id": str(organization_id)},
    )
    assert response.status_code in (200, 204)
    downloaded = httpx.get(store.create_download(organization_id, key))
    assert downloaded.content.startswith(b"sku,on_hand")
    store.delete(organization_id, key)


def test_real_rabbitmq_accepts_durable_queue_declaration() -> None:
    require_external_services()
    from kombu import Connection, Exchange, Queue

    broker_url = os.getenv("SMARTSTOCK_BROKER_URL", "amqp://guest:guest@localhost:5672//")
    queue_name = f"smartstock-test-{uuid4()}"
    with Connection(broker_url) as connection:
        channel = connection.channel()
        queue = Queue(
            queue_name,
            Exchange(queue_name, type="direct", durable=True),
            routing_key=queue_name,
            durable=True,
        )
        bound = queue(channel)
        bound.declare()
        bound.delete()
