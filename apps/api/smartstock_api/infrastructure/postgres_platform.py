from decimal import Decimal
from uuid import UUID

from sqlalchemy import text

from smartstock_api.domain.errors import ResourceNotFound
from smartstock_api.domain.platform import ApprovalPolicy, Organization
from smartstock_api.infrastructure.database import TenantSessionFactory


class PostgresPlatformStore:
    def __init__(self, sessions: TenantSessionFactory) -> None:
        self._sessions = sessions

    def organization(self, organization_id: UUID) -> Organization:
        with self._sessions.session(organization_id, organization_id) as session:
            row = session.execute(
                text(
                    """
                    SELECT id, slug, name, currency, valuation_method
                    FROM organizations WHERE id = :organization_id
                    """
                ),
                {"organization_id": organization_id},
            ).mappings().one_or_none()
            if row is None:
                raise ResourceNotFound("organization not found")
            return Organization(
                id=UUID(str(row["id"])),
                slug=row["slug"],
                name=row["name"],
                currency=row["currency"],
                valuation_method=row["valuation_method"],
            )

    def policies_for(
        self, organization_id: UUID, action_type: str | None = None
    ) -> list[ApprovalPolicy]:
        with self._sessions.session(organization_id, organization_id) as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, action_type, minimum_approvals, approver_permissions,
                           conditions, active
                    FROM approval_policies
                    WHERE organization_id = :organization_id
                      AND (
                        CAST(:action_type AS text) IS NULL
                        OR action_type = CAST(:action_type AS text)
                      )
                    ORDER BY name
                    """
                ),
                {"organization_id": organization_id, "action_type": action_type},
            ).mappings()
            policies: list[ApprovalPolicy] = []
            for row in rows:
                conditions = row["conditions"] or {}
                threshold = conditions.get("amount_threshold")
                permissions = row["approver_permissions"] or ["administration.manage"]
                policies.append(
                    ApprovalPolicy(
                        id=UUID(str(row["id"])),
                        organization_id=organization_id,
                        action_type=row["action_type"],
                        minimum_approvals=row["minimum_approvals"],
                        approver_permission=permissions[0],
                        amount_threshold=None if threshold is None else Decimal(str(threshold)),
                        currency=conditions.get("currency"),
                        active=row["active"],
                    )
                )
            return policies

    def flags_for(self, organization_id: UUID) -> dict[str, tuple[bool, dict[str, object]]]:
        with self._sessions.session(organization_id, organization_id) as session:
            rows = session.execute(
                text(
                    """
                    SELECT key, enabled, configuration FROM feature_flags
                    WHERE organization_id = :organization_id ORDER BY key
                    """
                ),
                {"organization_id": organization_id},
            ).mappings()
            return {
                row["key"]: (row["enabled"], dict(row["configuration"] or {})) for row in rows
            }
