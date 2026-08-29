from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from smartstock_api.config import Settings, get_settings
from smartstock_api.infrastructure.authorization import AuthorizationRecord


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    organization_id: UUID
    permissions: frozenset[str]
    warehouse_grants: frozenset[UUID]

    def require(self, permission: str) -> None:
        if permission not in self.permissions and "*" not in self.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"missing permission: {permission}",
            )


bearer = HTTPBearer(auto_error=False)


def _uuid_claim(claims: dict[str, object], name: str) -> UUID:
    value = claims.get(name)
    if not isinstance(value, str):
        raise HTTPException(status_code=401, detail=f"missing token claim: {name}")
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token claim: {name}") from exc


def _from_oidc(
    credentials: HTTPAuthorizationCredentials | None,
    settings: Settings,
    request: Request,
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="bearer token required")
    issuer = str(settings.oidc_issuer).rstrip("/")
    try:
        signing_key = PyJWKClient(f"{issuer}/protocol/openid-connect/certs").get_signing_key_from_jwt(
            credentials.credentials
        )
        claims = jwt.decode(
            credentials.credentials,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=settings.oidc_audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "organization_id"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid access token") from exc

    user_id = _uuid_claim(claims, "sub")
    organization_id = _uuid_claim(claims, "organization_id")
    directory = getattr(request.app.state, "authorization_directory", None)
    if directory is None:
        raise HTTPException(status_code=503, detail="authorization directory unavailable")
    authorization: AuthorizationRecord | None = directory.resolve(organization_id, user_id)
    if authorization is None:
        raise HTTPException(status_code=403, detail="active organization membership required")
    return Principal(
        user_id=user_id,
        organization_id=organization_id,
        permissions=authorization.permissions,
        warehouse_grants=authorization.warehouse_grants,
    )


def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
    development_user: str | None = Header(default=None, alias="X-Development-User"),
    development_organization: str | None = Header(
        default=None, alias="X-Development-Organization"
    ),
) -> Principal:
    if settings.auth_mode != "development":
        return _from_oidc(credentials, settings, request)
    if settings.environment == "production":
        raise HTTPException(status_code=500, detail="development auth is forbidden in production")
    try:
        return Principal(
            user_id=UUID(development_user or "00000000-0000-0000-0000-000000000001"),
            organization_id=UUID(
                development_organization or "00000000-0000-0000-0000-000000000001"
            ),
            permissions=frozenset({"*"}),
            warehouse_grants=frozenset(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid development identity headers") from exc


PrincipalDependency = Depends(get_principal)
