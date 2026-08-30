from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from smartstock_api.domain.errors import (
    DuplicateResource,
    IdempotencyConflict,
    ResourceNotFound,
)
from smartstock_api.domain.operations import (
    OperationalOrder,
    OrderKind,
    OrderLine,
    WarehouseTask,
    WarehouseTaskState,
    WarehouseTaskType,
)
from smartstock_api.infrastructure.database import TenantSessionFactory


class PostgresOperationsStore:
    def __init__(self, sessions: TenantSessionFactory) -> None:
        self._sessions = sessions

    @staticmethod
    def _hash(payload: dict[str, object]) -> str:
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    @staticmethod
    def _claim(session: Any, organization_id: UUID, key: str, fingerprint: str) -> dict | None:
        claimed = session.execute(
            text(
                """
                INSERT INTO idempotency_records (
                  organization_id, key, request_hash, response_status, response_body, expires_at
                ) VALUES (
                  :organization_id, :key, :fingerprint, 0, '{}'::jsonb, :expires_at
                ) ON CONFLICT (organization_id, key) DO NOTHING RETURNING key
                """
            ),
            {
                "organization_id": organization_id,
                "key": key,
                "fingerprint": fingerprint,
                "expires_at": datetime.now(UTC) + timedelta(days=7),
            },
        ).scalar_one_or_none()
        if claimed is not None:
            return None
        prior = session.execute(
            text(
                """
                SELECT request_hash, response_body FROM idempotency_records
                WHERE organization_id=:organization_id AND key=:key FOR UPDATE
                """
            ),
            {"organization_id": organization_id, "key": key},
        ).mappings().one()
        if prior["request_hash"] != fingerprint:
            raise IdempotencyConflict("idempotency key was reused with a different command")
        if not prior["response_body"]:
            raise IdempotencyConflict("prior command did not complete")
        return dict(prior["response_body"])

    @staticmethod
    def _complete(session: Any, organization_id: UUID, key: str, body: dict) -> None:
        session.execute(
            text(
                """
                UPDATE idempotency_records
                SET response_status=200, response_body=CAST(:body AS jsonb)
                WHERE organization_id=:organization_id AND key=:key
                """
            ),
            {"organization_id": organization_id, "key": key, "body": json.dumps(body)},
        )

    @staticmethod
    def _record(
        session: Any,
        organization_id: UUID,
        actor_id: UUID,
        correlation_id: UUID,
        action: str,
        resource_type: str,
        resource_id: UUID,
        topic: str,
        payload: dict,
        before: dict | None = None,
    ) -> None:
        session.execute(
            text(
                """
                INSERT INTO audit_events (
                  organization_id, actor_id, action, resource_type, resource_id,
                  correlation_id, before_state, after_state
                ) VALUES (
                  :organization_id, :actor_id, :action, :resource_type, :resource_id,
                  :correlation_id, CAST(:before AS jsonb), CAST(:after AS jsonb)
                )
                """
            ),
            {
                "organization_id": organization_id,
                "actor_id": actor_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": str(resource_id),
                "correlation_id": correlation_id,
                "before": json.dumps(before) if before is not None else None,
                "after": json.dumps(payload),
            },
        )
        session.execute(
            text(
                """
                INSERT INTO outbox_events (
                  organization_id, topic, aggregate_id, correlation_id, payload
                ) VALUES (
                  :organization_id, :topic, :resource_id, :correlation_id, CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "organization_id": organization_id,
                "topic": topic,
                "resource_id": resource_id,
                "correlation_id": correlation_id,
                "payload": json.dumps(payload),
            },
        )

    def create_order(
        self,
        order: OperationalOrder,
        actor_id: UUID,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> tuple[OperationalOrder, bool]:
        fingerprint = self._hash({"command": "create_order", "order": order})
        try:
            with self._sessions.session(order.organization_id, actor_id) as session:
                prior = self._claim(session, order.organization_id, idempotency_key, fingerprint)
                if prior is not None:
                    return self._load_order(
                        session, order.organization_id, OrderKind(prior["kind"]), UUID(prior["id"])
                    ), True
                party_table = "suppliers" if order.kind == OrderKind.PURCHASE else "customers"
                if session.execute(
                    text(
                        f"SELECT 1 FROM {party_table} WHERE organization_id=:organization_id AND id=:id"
                    ),
                    {"organization_id": order.organization_id, "id": order.party_id},
                ).scalar_one_or_none() is None:
                    raise ResourceNotFound(f"{party_table[:-1]} not found")
                session.execute(
                    text(
                        """
                        INSERT INTO operational_orders (
                          organization_id, id, kind, order_number, party_id, warehouse_id,
                          state, currency, expected_on, notes, version, created_by,
                          created_at, updated_at
                        ) VALUES (
                          :organization_id, :id, :kind, :order_number, :party_id, :warehouse_id,
                          :state, :currency, :expected_on, :notes, :version, :actor_id,
                          :created_at, :updated_at
                        )
                        """
                    ),
                    {
                        "organization_id": order.organization_id,
                        "id": order.id,
                        "kind": order.kind.value,
                        "order_number": order.order_number,
                        "party_id": order.party_id,
                        "warehouse_id": order.warehouse_id,
                        "state": order.state,
                        "currency": order.currency,
                        "expected_on": order.expected_on,
                        "notes": order.notes,
                        "version": order.version,
                        "actor_id": actor_id,
                        "created_at": order.created_at,
                        "updated_at": order.updated_at,
                    },
                )
                for index, line in enumerate(order.lines, start=1):
                    session.execute(
                        text(
                            """
                            INSERT INTO operational_order_lines (
                              organization_id, id, order_id, line_number, product_id,
                              quantity, processed_quantity, uom, unit_price, currency
                            ) VALUES (
                              :organization_id, :id, :order_id, :line_number, :product_id,
                              :quantity, :processed_quantity, :uom, :unit_price, :currency
                            )
                            """
                        ),
                        {
                            "organization_id": order.organization_id,
                            "id": line.id,
                            "order_id": order.id,
                            "line_number": index,
                            "product_id": line.product_id,
                            "quantity": line.quantity,
                            "processed_quantity": line.received_or_shipped_quantity,
                            "uom": line.uom,
                            "unit_price": line.unit_price,
                            "currency": line.currency,
                        },
                    )
                payload = {
                    "id": str(order.id),
                    "kind": order.kind.value,
                    "order_number": order.order_number,
                    "state": order.state,
                    "version": order.version,
                    "total": str(order.total),
                }
                topic = "purchase_order.created" if order.kind == OrderKind.PURCHASE else "order.created"
                self._record(
                    session, order.organization_id, actor_id, correlation_id,
                    f"{order.kind.value}_order.created", "operational_order", order.id, topic, payload
                )
                self._complete(session, order.organization_id, idempotency_key, payload)
                return order, False
        except IntegrityError as exc:
            raise DuplicateResource("order number or referenced record is invalid") from exc

    def orders_for(
        self, organization_id: UUID, actor_id: UUID, kind: OrderKind
    ) -> list[OperationalOrder]:
        with self._sessions.session(organization_id, actor_id) as session:
            ids = session.execute(
                text(
                    """
                    SELECT id FROM operational_orders
                    WHERE organization_id=:organization_id AND kind=:kind
                    ORDER BY created_at DESC, order_number LIMIT 250
                    """
                ),
                {"organization_id": organization_id, "kind": kind.value},
            ).scalars()
            return [self._load_order(session, organization_id, kind, UUID(str(item))) for item in ids]

    def order(
        self, organization_id: UUID, actor_id: UUID, kind: OrderKind, order_id: UUID
    ) -> OperationalOrder:
        with self._sessions.session(organization_id, actor_id) as session:
            return self._load_order(session, organization_id, kind, order_id)

    def transition_order(
        self,
        organization_id: UUID,
        actor_id: UUID,
        kind: OrderKind,
        order_id: UUID,
        target: str,
        expected_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> tuple[OperationalOrder, bool]:
        fingerprint = self._hash(
            {"command": "transition_order", "kind": kind, "order_id": order_id,
             "target": target, "expected_version": expected_version}
        )
        with self._sessions.session(organization_id, actor_id) as session:
            prior = self._claim(session, organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return self._load_order(session, organization_id, kind, order_id), True
            current = self._load_order(session, organization_id, kind, order_id, lock=True)
            transitioned = current.transition(target, organization_id, expected_version)
            session.execute(
                text(
                    """
                    UPDATE operational_orders SET state=:state, version=:version, updated_at=:updated_at
                    WHERE organization_id=:organization_id AND id=:id AND kind=:kind
                    """
                ),
                {"organization_id": organization_id, "id": order_id, "kind": kind.value,
                 "state": transitioned.state, "version": transitioned.version,
                 "updated_at": transitioned.updated_at},
            )
            payload = {"id": str(order_id), "kind": kind.value, "state": target,
                       "version": transitioned.version}
            topic = "purchase_order.state_changed" if kind == OrderKind.PURCHASE else "order.state_changed"
            if kind == OrderKind.PURCHASE and target == "approved":
                topic = "purchase_order.approved"
            elif kind == OrderKind.SALES and target == "confirmed":
                topic = "order.confirmed"
            self._record(
                session, organization_id, actor_id, correlation_id,
                f"{kind.value}_order.{target}", "operational_order", order_id, topic, payload,
                {"state": current.state, "version": current.version},
            )
            self._complete(session, organization_id, idempotency_key, payload)
            return transitioned, False

    def create_task(
        self, task: WarehouseTask, actor_id: UUID, correlation_id: UUID, idempotency_key: str
    ) -> tuple[WarehouseTask, bool]:
        fingerprint = self._hash({"command": "create_task", "task": task})
        try:
            with self._sessions.session(task.organization_id, actor_id) as session:
                prior = self._claim(session, task.organization_id, idempotency_key, fingerprint)
                if prior is not None:
                    return self._load_task(session, task.organization_id, UUID(prior["id"])), True
                params = {name: getattr(task, name) for name in (
                    "id", "task_number", "task_type", "warehouse_id", "state",
                    "source_location_id", "destination_location_id", "product_id", "quantity",
                    "uom", "reference_type", "reference_id", "assigned_to", "priority", "version",
                    "created_at", "updated_at",
                )} | {"organization_id": task.organization_id, "actor_id": actor_id}
                params["task_type"] = task.task_type.value
                params["state"] = task.state.value
                session.execute(
                    text(
                        """
                        INSERT INTO warehouse_tasks (
                          organization_id, id, task_number, task_type, warehouse_id, state,
                          source_location_id, destination_location_id, product_id, quantity, uom,
                          reference_type, reference_id, assigned_to, priority, version, created_by,
                          created_at, updated_at
                        ) VALUES (
                          :organization_id, :id, :task_number, :task_type, :warehouse_id, :state,
                          :source_location_id, :destination_location_id, :product_id, :quantity, :uom,
                          :reference_type, :reference_id, :assigned_to, :priority, :version, :actor_id,
                          :created_at, :updated_at
                        )
                        """
                    ), params,
                )
                payload = {"id": str(task.id), "task_number": task.task_number,
                           "task_type": task.task_type.value, "state": task.state.value,
                           "version": task.version}
                self._record(
                    session, task.organization_id, actor_id, correlation_id,
                    "warehouse_task.created", "warehouse_task", task.id,
                    "warehouse_task.created", payload,
                )
                self._complete(session, task.organization_id, idempotency_key, payload)
                return task, False
        except IntegrityError as exc:
            raise DuplicateResource("warehouse task number or referenced record is invalid") from exc

    def tasks_for(
        self, organization_id: UUID, actor_id: UUID, warehouse_id: UUID | None = None
    ) -> list[WarehouseTask]:
        with self._sessions.session(organization_id, actor_id) as session:
            rows = session.execute(
                text(
                    """
                    SELECT * FROM warehouse_tasks WHERE organization_id=:organization_id
                      AND (:warehouse_id IS NULL OR warehouse_id=:warehouse_id)
                    ORDER BY priority, created_at, task_number LIMIT 250
                    """
                ), {"organization_id": organization_id, "warehouse_id": warehouse_id},
            ).mappings()
            return [self._task_from_row(organization_id, row) for row in rows]

    def transition_task(
        self,
        organization_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        target: WarehouseTaskState,
        expected_version: int,
        correlation_id: UUID,
        idempotency_key: str,
        assigned_to: UUID | None = None,
    ) -> tuple[WarehouseTask, bool]:
        fingerprint = self._hash(
            {"command": "transition_task", "task_id": task_id, "target": target,
             "expected_version": expected_version, "assigned_to": assigned_to}
        )
        with self._sessions.session(organization_id, actor_id) as session:
            prior = self._claim(session, organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return self._load_task(session, organization_id, task_id), True
            current = self._load_task(session, organization_id, task_id, lock=True)
            transitioned = current.transition(target, organization_id, expected_version, assigned_to)
            session.execute(
                text(
                    """
                    UPDATE warehouse_tasks
                    SET state=:state, assigned_to=:assigned_to, version=:version, updated_at=:updated_at
                    WHERE organization_id=:organization_id AND id=:id
                    """
                ), {"organization_id": organization_id, "id": task_id,
                    "state": transitioned.state.value, "assigned_to": transitioned.assigned_to,
                    "version": transitioned.version, "updated_at": transitioned.updated_at},
            )
            payload = {"id": str(task_id), "state": transitioned.state.value,
                       "version": transitioned.version}
            self._record(
                session, organization_id, actor_id, correlation_id,
                f"warehouse_task.{target.value}", "warehouse_task", task_id,
                "warehouse_task.state_changed", payload,
                {"state": current.state.value, "version": current.version},
            )
            self._complete(session, organization_id, idempotency_key, payload)
            return transitioned, False

    def _load_order(
        self, session: Any, organization_id: UUID, kind: OrderKind, order_id: UUID,
        lock: bool = False,
    ) -> OperationalOrder:
        row = session.execute(
            text(
                """
                SELECT * FROM operational_orders
                WHERE organization_id=:organization_id AND kind=:kind AND id=:id
                """ + (" FOR UPDATE" if lock else "")
            ), {"organization_id": organization_id, "kind": kind.value, "id": order_id},
        ).mappings().one_or_none()
        if row is None:
            raise ResourceNotFound("order not found")
        lines = session.execute(
            text(
                """
                SELECT * FROM operational_order_lines
                WHERE organization_id=:organization_id AND order_id=:id ORDER BY line_number
                """
            ), {"organization_id": organization_id, "id": order_id},
        ).mappings()
        return OperationalOrder(
            id=UUID(str(row["id"])), organization_id=organization_id, kind=kind,
            order_number=row["order_number"], party_id=UUID(str(row["party_id"])),
            warehouse_id=UUID(str(row["warehouse_id"])), state=row["state"],
            lines=tuple(OrderLine(
                UUID(str(line["id"])), UUID(str(line["product_id"])), Decimal(line["quantity"]),
                line["uom"], Decimal(line["unit_price"]), line["currency"],
                Decimal(line["processed_quantity"]),
            ) for line in lines), currency=row["currency"], expected_on=row["expected_on"],
            notes=row["notes"], version=row["version"], created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _load_task(
        self, session: Any, organization_id: UUID, task_id: UUID, lock: bool = False
    ) -> WarehouseTask:
        row = session.execute(
            text(
                "SELECT * FROM warehouse_tasks WHERE organization_id=:organization_id AND id=:id"
                + (" FOR UPDATE" if lock else "")
            ), {"organization_id": organization_id, "id": task_id},
        ).mappings().one_or_none()
        if row is None:
            raise ResourceNotFound("warehouse task not found")
        return self._task_from_row(organization_id, row)

    @staticmethod
    def _task_from_row(organization_id: UUID, row: Any) -> WarehouseTask:
        uuid_or_none = lambda value: None if value is None else UUID(str(value))
        return WarehouseTask(
            id=UUID(str(row["id"])), organization_id=organization_id,
            task_number=row["task_number"], task_type=WarehouseTaskType(row["task_type"]),
            warehouse_id=UUID(str(row["warehouse_id"])), state=WarehouseTaskState(row["state"]),
            source_location_id=uuid_or_none(row["source_location_id"]),
            destination_location_id=uuid_or_none(row["destination_location_id"]),
            product_id=uuid_or_none(row["product_id"]), quantity=row["quantity"], uom=row["uom"],
            reference_type=row["reference_type"], reference_id=uuid_or_none(row["reference_id"]),
            assigned_to=uuid_or_none(row["assigned_to"]), priority=row["priority"],
            version=row["version"], created_at=row["created_at"], updated_at=row["updated_at"],
        )
