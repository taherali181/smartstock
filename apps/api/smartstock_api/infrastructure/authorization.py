from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text

from smartstock_api.infrastructure.database import TenantSessionFactory


@dataclass(frozen=True, slots=True)
class AuthorizationRecord:
    permissions: frozenset[str]
    warehouse_grants: frozenset[UUID]


class PostgresAuthorizationDirectory:
    def __init__(self, sessions: TenantSessionFactory) -> None:
        self._sessions = sessions

    def resolve(self, organization_id: UUID, user_id: UUID) -> AuthorizationRecord | None:
        with self._sessions.session(organization_id, user_id) as session:
            role = session.execute(
                text(
                    """
                    SELECT role FROM memberships
                    WHERE organization_id = :organization_id
                      AND user_id = :user_id AND active = true
                    """
                ),
                {"organization_id": organization_id, "user_id": user_id},
            ).scalar_one_or_none()
            if role is None:
                return None
            permissions = frozenset(
                session.execute(
                    text("SELECT permission FROM role_permissions WHERE role = :role"),
                    {"role": role},
                ).scalars()
            )
            warehouses = frozenset(
                UUID(str(value))
                for value in session.execute(
                    text(
                        """
                        SELECT warehouse_id FROM warehouse_grants
                        WHERE organization_id = :organization_id AND user_id = :user_id
                        """
                    ),
                    {"organization_id": organization_id, "user_id": user_id},
                ).scalars()
            )
            return AuthorizationRecord(permissions, warehouses)
