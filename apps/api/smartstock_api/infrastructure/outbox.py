from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy import text

from smartstock_api.infrastructure.database import TenantSessionFactory

Publisher = Callable[[str, dict[str, Any], dict[str, str]], None]


class OutboxDispatcher:
    def __init__(self, sessions: TenantSessionFactory, publisher: Publisher) -> None:
        self._sessions = sessions
        self._publisher = publisher

    def dispatch_one(self, organization_id: UUID, worker_id: UUID) -> bool:
        with self._sessions.session(organization_id, worker_id) as session:
            event = session.execute(
                text(
                    """
                    SELECT id, topic, event_version, aggregate_id, correlation_id,
                           causation_id, payload
                    FROM outbox_events
                    WHERE organization_id = CAST(:organization_id AS uuid)
                      AND published_at IS NULL
                      AND (locked_at IS NULL OR locked_at < now() - interval '5 minutes')
                    ORDER BY occurred_at
                    FOR UPDATE SKIP LOCKED LIMIT 1
                    """
                ),
                {"organization_id": organization_id},
            ).mappings().one_or_none()
            if event is None:
                return False
            session.execute(
                text(
                    """
                    UPDATE outbox_events SET locked_at = now(), attempts = attempts + 1
                    WHERE organization_id = CAST(:organization_id AS uuid)
                      AND id = CAST(:event_id AS uuid)
                    """
                ),
                {"organization_id": organization_id, "event_id": event["id"]},
            )
            headers = {
                "event_id": str(event["id"]),
                "event_version": str(event["event_version"]),
                "organization_id": str(organization_id),
                "aggregate_id": str(event["aggregate_id"]),
                "correlation_id": str(event["correlation_id"]),
            }
            if event["causation_id"]:
                headers["causation_id"] = str(event["causation_id"])
            try:
                self._publisher(event["topic"], dict(event["payload"]), headers)
            except Exception as exc:
                session.execute(
                    text(
                        """
                        UPDATE outbox_events SET locked_at = NULL, last_error = :error
                        WHERE organization_id = CAST(:organization_id AS uuid)
                          AND id = CAST(:event_id AS uuid)
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "event_id": event["id"],
                        "error": str(exc)[:2000],
                    },
                )
                raise
            session.execute(
                text(
                    """
                    UPDATE outbox_events
                    SET published_at = now(), locked_at = NULL, last_error = NULL
                    WHERE organization_id = CAST(:organization_id AS uuid)
                      AND id = CAST(:event_id AS uuid)
                    """
                ),
                {"organization_id": organization_id, "event_id": event["id"]},
            )
            return True
