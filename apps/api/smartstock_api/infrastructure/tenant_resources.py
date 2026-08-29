from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from smartstock_api.domain.platform import TenantKeyspace


class TenantCache:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, organization_id: UUID, namespace: str, key: str) -> bytes | None:
        return self._client.get(TenantKeyspace.cache(organization_id, namespace, key))

    def set(
        self,
        organization_id: UUID,
        namespace: str,
        key: str,
        value: bytes,
        ttl: timedelta,
    ) -> None:
        self._client.set(
            TenantKeyspace.cache(organization_id, namespace, key),
            value,
            ex=max(1, int(ttl.total_seconds())),
        )

    def delete(self, organization_id: UUID, namespace: str, key: str) -> None:
        self._client.delete(TenantKeyspace.cache(organization_id, namespace, key))


class TenantObjectStore:
    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def create_upload(
        self,
        organization_id: UUID,
        purpose: str,
        filename: str,
        content_type: str,
        expires_seconds: int = 900,
    ) -> tuple[UUID, str, str]:
        object_id = uuid4()
        object_key = TenantKeyspace.object(organization_id, purpose, object_id, filename)
        url = self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": object_key,
                "ContentType": content_type,
                "Metadata": {"organization-id": str(organization_id)},
            },
            ExpiresIn=expires_seconds,
        )
        return object_id, object_key, url

    def create_download(
        self, organization_id: UUID, object_key: str, expires_seconds: int = 300
    ) -> str:
        TenantKeyspace.assert_owned(organization_id, object_key)
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": object_key},
            ExpiresIn=expires_seconds,
        )

    def delete(self, organization_id: UUID, object_key: str) -> None:
        TenantKeyspace.assert_owned(organization_id, object_key)
        self._client.delete_object(Bucket=self._bucket, Key=object_key)
