from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from smartstock_api.domain.errors import TenantBoundaryViolation


@dataclass(frozen=True, slots=True)
class JobEnvelope:
    job_id: UUID
    organization_id: UUID
    actor_id: UUID
    correlation_id: UUID
    job_type: str
    payload: dict[str, Any]
    issued_at: datetime

    def canonical(self) -> bytes:
        values = asdict(self)
        values["job_id"] = str(self.job_id)
        values["organization_id"] = str(self.organization_id)
        values["actor_id"] = str(self.actor_id)
        values["correlation_id"] = str(self.correlation_id)
        values["issued_at"] = self.issued_at.astimezone(UTC).isoformat()
        return json.dumps(values, sort_keys=True, separators=(",", ":")).encode()


def sign(envelope: JobEnvelope, secret: str) -> str:
    return hmac.new(secret.encode(), envelope.canonical(), hashlib.sha256).hexdigest()


def verify(envelope: JobEnvelope, signature: str, secret: str) -> None:
    expected = sign(envelope, secret)
    if not hmac.compare_digest(expected, signature):
        raise TenantBoundaryViolation("job envelope signature is invalid")
    payload_tenant = envelope.payload.get("organization_id")
    if payload_tenant is not None and str(payload_tenant) != str(envelope.organization_id):
        raise TenantBoundaryViolation("job payload conflicts with signed tenant context")
