from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IssuedCredential:
    token: str
    prefix: str
    digest: str


def issue_api_credential(environment: str = "live") -> IssuedCredential:
    key_id = secrets.token_urlsafe(9)
    secret = secrets.token_urlsafe(32)
    prefix = f"ss_{environment}_{key_id}"
    token = f"{prefix}.{secret}"
    return IssuedCredential(token=token, prefix=prefix, digest=_hash(secret))


def verify_api_credential(token: str, expected_prefix: str, expected_digest: str) -> bool:
    try:
        prefix, secret = token.split(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(prefix, expected_prefix):
        return False
    return hmac.compare_digest(_hash(secret), expected_digest)


def _hash(secret: str) -> str:
    # A keyed pepper belongs in KMS/Secrets Manager in production. Scrypt makes
    # a database-only credential leak expensive while keeping verification local.
    salt = b"smartstock-api-client-v1"
    return hashlib.scrypt(secret.encode(), salt=salt, n=2**14, r=8, p=1).hex()
