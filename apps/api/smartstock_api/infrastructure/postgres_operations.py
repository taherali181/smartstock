from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from smartstock_api.domain.errors import (
    ConcurrencyConflict,
    DuplicateResource,
    IdempotencyConflict,
    InvalidQuantity,
    InvalidStateTransition,
    ResourceNotFound,
)
from smartstock_api.domain.operations import (
    OperationalOrder,
    OrderKind,
    OrderLine,
    Receipt,
    ReceiptPostingLine,
    ReceiptResult,
    WarehouseTask,
    WarehouseTaskState,
    WarehouseTaskType,
)
from smartstock_api.domain.valuation import ValuationMethod, weighted_average_cost
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
            generated_task = self._generate_task_for_order(session, transitioned, actor_id)
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
            if generated_task is not None:
                task_payload = {
                    "id": str(generated_task.id),
                    "task_number": generated_task.task_number,
                    "task_type": generated_task.task_type.value,
                    "state": generated_task.state.value,
                    "version": generated_task.version,
                    "reference_id": str(order_id),
                }
                self._record(
                    session, organization_id, actor_id, correlation_id,
                    "warehouse_task.created", "warehouse_task", generated_task.id,
                    "warehouse_task.created", task_payload,
                )
            self._complete(session, organization_id, idempotency_key, payload)
            return transitioned, False

    def _generate_task_for_order(
        self, session: Any, order: OperationalOrder, actor_id: UUID
    ) -> WarehouseTask | None:
        if order.kind == OrderKind.PURCHASE and order.state == "acknowledged":
            task_type = WarehouseTaskType.RECEIVE
            prefix = "RCV"
        elif order.kind == OrderKind.SALES and order.state == "allocated":
            task_type = WarehouseTaskType.PICK
            prefix = "PICK"
        else:
            return None
        task = WarehouseTask(
            id=uuid5(order.id, task_type.value),
            organization_id=order.organization_id,
            task_number=f"{prefix}-{order.order_number}",
            task_type=task_type,
            warehouse_id=order.warehouse_id,
            reference_type=f"{order.kind.value}_order",
            reference_id=order.id,
            priority=50,
        )
        inserted = session.execute(
            text(
                """
                INSERT INTO warehouse_tasks (
                  organization_id, id, task_number, task_type, warehouse_id, state,
                  reference_type, reference_id, priority, version, created_by,
                  created_at, updated_at
                ) VALUES (
                  :organization_id, :id, :task_number, :task_type, :warehouse_id, :state,
                  :reference_type, :reference_id, :priority, :version, :actor_id,
                  :created_at, :updated_at
                ) ON CONFLICT (organization_id, id) DO NOTHING RETURNING id
                """
            ),
            {
                "organization_id": task.organization_id,
                "id": task.id,
                "task_number": task.task_number,
                "task_type": task.task_type.value,
                "warehouse_id": task.warehouse_id,
                "state": task.state.value,
                "reference_type": task.reference_type,
                "reference_id": task.reference_id,
                "priority": task.priority,
                "version": task.version,
                "actor_id": actor_id,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
            },
        ).scalar_one_or_none()
        return task if inserted is not None else None

    def post_receipt(
        self,
        organization_id: UUID,
        actor_id: UUID,
        purchase_order_id: UUID,
        receipt_id: UUID,
        receipt_number: str,
        lines: tuple[ReceiptPostingLine, ...],
        expected_order_version: int,
        over_receipt_tolerance_percent: Decimal,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> ReceiptResult:
        if not lines:
            raise InvalidQuantity("a receipt requires at least one line")
        if over_receipt_tolerance_percent < 0 or over_receipt_tolerance_percent > 100:
            raise InvalidQuantity("over-receipt tolerance must be between 0 and 100 percent")
        fingerprint = self._hash(
            {"command": "post_receipt", "order_id": purchase_order_id,
             "receipt_id": receipt_id, "number": receipt_number, "lines": lines,
             "expected_version": expected_order_version,
             "tolerance": over_receipt_tolerance_percent}
        )
        try:
            with self._sessions.session(organization_id, actor_id) as session:
                prior = self._claim(session, organization_id, idempotency_key, fingerprint)
                if prior is not None:
                    order = self._load_order(
                        session, organization_id, OrderKind.PURCHASE, purchase_order_id
                    )
                    receipt = self._load_receipt(session, organization_id, receipt_id, lines)
                    return ReceiptResult(receipt, order, True)
                order = self._load_order(
                    session, organization_id, OrderKind.PURCHASE, purchase_order_id, lock=True
                )
                if order.version != expected_order_version:
                    raise ConcurrencyConflict("purchase order version changed")
                if order.state not in {"acknowledged", "partially_received"}:
                    raise InvalidStateTransition("purchase order is not receivable")
                if len({line.order_line_id for line in lines}) != len(lines):
                    raise DuplicateResource("receipt order lines must be unique")
                order_lines = {line.id: line for line in order.lines}
                organization = session.execute(
                    text("SELECT valuation_method, currency FROM organizations WHERE id=:id"),
                    {"id": organization_id},
                ).mappings().one()
                valuation_method = ValuationMethod(organization["valuation_method"])
                now = datetime.now(UTC)
                transaction_id = UUID(str(receipt_id))
                session.execute(
                    text(
                        """
                        INSERT INTO inventory_transactions (
                          organization_id,id,actor_id,reason_code,business_reference,
                          idempotency_key,correlation_id,occurred_at
                        ) VALUES (
                          :organization_id,:id,:actor_id,'purchase_receipt',:reference,
                          :key,:correlation_id,:now
                        )
                        """
                    ), {"organization_id": organization_id, "id": transaction_id,
                        "actor_id": actor_id, "reference": receipt_number,
                        "key": idempotency_key, "correlation_id": correlation_id, "now": now},
                )
                session.execute(
                    text(
                        """
                        INSERT INTO receipts (
                          organization_id,id,receipt_number,purchase_order_id,warehouse_id,
                          inventory_transaction_id,posted_by,posted_at
                        ) VALUES (
                          :organization_id,:id,:number,:order_id,:warehouse_id,
                          :transaction_id,:actor_id,:now
                        )
                        """
                    ), {"organization_id": organization_id, "id": receipt_id,
                        "number": receipt_number, "order_id": purchase_order_id,
                        "warehouse_id": order.warehouse_id, "transaction_id": transaction_id,
                        "actor_id": actor_id, "now": now},
                )
                updated_quantities: dict[UUID, Decimal] = {}
                position_events: list[tuple[UUID, dict[str, str]]] = []
                ledger_line_number = 1
                for posting in lines:
                    order_line = order_lines.get(posting.order_line_id)
                    if order_line is None:
                        raise ResourceNotFound("purchase order line not found")
                    delivered = posting.accepted_quantity + posting.rejected_quantity
                    maximum = order_line.quantity * (
                        Decimal("1") + over_receipt_tolerance_percent / Decimal("100")
                    ) - order_line.received_or_shipped_quantity
                    if delivered > maximum:
                        raise InvalidQuantity("receipt exceeds the configured over-receipt tolerance")
                    updated_quantities[order_line.id] = (
                        order_line.received_or_shipped_quantity + delivered
                    )
                    session.execute(
                        text(
                            """
                            INSERT INTO receipt_lines (
                              organization_id,id,receipt_id,order_line_id,product_id,location_id,
                              accepted_quantity,rejected_quantity,uom,unit_cost,currency
                            ) VALUES (
                              :organization_id,:id,:receipt_id,:order_line_id,:product_id,:location_id,
                              :accepted,:rejected,:uom,:unit_cost,:currency
                            )
                            """
                        ), {"organization_id": organization_id, "id": posting.id,
                            "receipt_id": receipt_id, "order_line_id": order_line.id,
                            "product_id": order_line.product_id, "location_id": posting.location_id,
                            "accepted": posting.accepted_quantity,
                            "rejected": posting.rejected_quantity, "uom": order_line.uom,
                            "unit_cost": order_line.unit_price, "currency": order.currency},
                    )
                    for label, quantity, condition, expected_version in (
                        ("accepted", posting.accepted_quantity, "sellable",
                         posting.expected_sellable_version),
                        ("rejected", posting.rejected_quantity, "quarantined",
                         posting.expected_quarantine_version),
                    ):
                        if quantity <= 0:
                            continue
                        session.execute(
                            text(
                                """
                                INSERT INTO inventory_positions (
                                  organization_id,product_id,warehouse_id,location_id,condition,
                                  ownership,lot_key,serial_key,uom
                                ) VALUES (
                                  :organization_id,:product_id,:warehouse_id,:location_id,:condition,
                                  'owned','00000000-0000-0000-0000-000000000000',
                                  '00000000-0000-0000-0000-000000000000',:uom
                                ) ON CONFLICT (
                                  organization_id,product_id,warehouse_id,location_id,condition,
                                  ownership,lot_key,serial_key,uom
                                ) DO NOTHING
                                """
                            ), {"organization_id": organization_id,
                                "product_id": order_line.product_id,
                                "warehouse_id": order.warehouse_id,
                                "location_id": posting.location_id, "condition": condition,
                                "uom": order_line.uom},
                        )
                        position = session.execute(
                            text(
                                """
                                SELECT * FROM inventory_positions
                                WHERE organization_id=:organization_id AND product_id=:product_id
                                  AND warehouse_id=:warehouse_id AND location_id=:location_id
                                  AND condition=:condition AND ownership='owned'
                                  AND lot_key='00000000-0000-0000-0000-000000000000'
                                  AND serial_key='00000000-0000-0000-0000-000000000000'
                                  AND uom=:uom FOR UPDATE
                                """
                            ), {"organization_id": organization_id,
                                "product_id": order_line.product_id,
                                "warehouse_id": order.warehouse_id,
                                "location_id": posting.location_id, "condition": condition,
                                "uom": order_line.uom},
                        ).mappings().one()
                        if position["version"] != expected_version:
                            raise ConcurrencyConflict(
                                f"expected {label} position version {expected_version}, "
                                f"got {position['version']}"
                            )
                        current_on_hand = Decimal(position["on_hand"])
                        current_average = Decimal(position["average_unit_cost"])
                        current_value = Decimal(position["inventory_value"])
                        next_on_hand = current_on_hand + quantity
                        next_value = current_value + quantity * order_line.unit_price
                        next_average = weighted_average_cost(
                            current_on_hand, current_average, quantity, order_line.unit_price
                        )
                        if valuation_method == ValuationMethod.FIFO:
                            next_average = next_value / next_on_hand
                            session.execute(
                                text(
                                    """
                                    INSERT INTO cost_layers (
                                      organization_id,product_id,warehouse_id,source_transaction_id,
                                      received_at,original_quantity,remaining_quantity,unit_cost,currency
                                    ) VALUES (
                                      :organization_id,:product_id,:warehouse_id,:transaction_id,
                                      :now,:quantity,:quantity,:unit_cost,:currency
                                    )
                                    """
                                ), {"organization_id": organization_id,
                                    "product_id": order_line.product_id,
                                    "warehouse_id": order.warehouse_id,
                                    "transaction_id": transaction_id, "now": now,
                                    "quantity": quantity, "unit_cost": order_line.unit_price,
                                    "currency": order.currency},
                            )
                        session.execute(
                            text(
                                """
                                UPDATE inventory_positions SET on_hand=:on_hand,
                                  average_unit_cost=:average,inventory_value=:value,
                                  version=version+1,updated_at=:now
                                WHERE organization_id=:organization_id AND id=:id
                                """
                            ), {"organization_id": organization_id, "id": position["id"],
                                "on_hand": next_on_hand, "average": next_average,
                                "value": next_value, "now": now},
                        )
                        session.execute(
                            text(
                                """
                                INSERT INTO inventory_ledger_lines (
                                  organization_id,transaction_id,line_number,account,product_id,
                                  warehouse_id,location_id,condition,ownership,quantity,uom,
                                  unit_cost,currency
                                ) VALUES (
                                  :organization_id,:transaction_id,:line_number,'on_hand',:product_id,
                                  :warehouse_id,:location_id,:condition,'owned',:quantity,:uom,
                                  :unit_cost,:currency
                                ),(
                                  :organization_id,:transaction_id,:external_line,'external',NULL,
                                  NULL,NULL,NULL,NULL,-:quantity,NULL,:unit_cost,:currency
                                )
                                """
                            ), {"organization_id": organization_id,
                                "transaction_id": transaction_id,
                                "line_number": ledger_line_number,
                                "external_line": ledger_line_number + 1,
                                "product_id": order_line.product_id,
                                "warehouse_id": order.warehouse_id,
                                "location_id": posting.location_id, "condition": condition,
                                "quantity": quantity, "uom": order_line.uom,
                                "unit_cost": order_line.unit_price, "currency": order.currency},
                        )
                        ledger_line_number += 2
                        session.execute(
                            text(
                                """
                                INSERT INTO valuation_postings (
                                  organization_id,inventory_transaction_id,product_id,warehouse_id,
                                  valuation_method,quantity,unit_cost,total_cost,currency,posted_at
                                ) VALUES (
                                  :organization_id,:transaction_id,:product_id,:warehouse_id,
                                  :method,:quantity,:unit_cost,:total,:currency,:now
                                )
                                """
                            ), {"organization_id": organization_id,
                                "transaction_id": transaction_id,
                                "product_id": order_line.product_id,
                                "warehouse_id": order.warehouse_id,
                                "method": valuation_method.value, "quantity": quantity,
                                "unit_cost": order_line.unit_price,
                                "total": quantity * order_line.unit_price,
                                "currency": order.currency, "now": now},
                        )
                        position_events.append(
                            (
                                UUID(str(position["id"])),
                                {
                                    "transaction_id": str(transaction_id),
                                    "product_id": str(order_line.product_id),
                                    "warehouse_id": str(order.warehouse_id),
                                    "location_id": str(posting.location_id),
                                    "condition": condition,
                                    "quantity_delta": str(quantity),
                                    "on_hand": str(next_on_hand),
                                    "version": str(position["version"] + 1),
                                },
                            )
                        )
                    session.execute(
                        text(
                            """
                            UPDATE operational_order_lines SET processed_quantity=:quantity
                            WHERE organization_id=:organization_id AND id=:id
                            """
                        ), {"organization_id": organization_id, "id": order_line.id,
                            "quantity": updated_quantities[order_line.id]},
                    )
                    if posting.accepted_quantity > 0:
                        task = WarehouseTask(
                            id=uuid5(receipt_id, f"putaway:{posting.id}"),
                            organization_id=organization_id,
                            task_number=f"PUT-{receipt_number}-{str(posting.id)[:8]}",
                            task_type=WarehouseTaskType.PUTAWAY,
                            warehouse_id=order.warehouse_id,
                            source_location_id=posting.location_id,
                            product_id=order_line.product_id,
                            quantity=posting.accepted_quantity, uom=order_line.uom,
                            reference_type="receipt", reference_id=receipt_id, priority=40,
                        )
                        self._insert_generated_task(session, task, actor_id)
                refreshed = self._load_order(
                    session, organization_id, OrderKind.PURCHASE, purchase_order_id
                )
                target = "received" if all(
                    line.open_quantity == 0 for line in refreshed.lines
                ) else "partially_received"
                transitioned = order.transition(target, organization_id, expected_order_version)
                transitioned = replace(transitioned, lines=refreshed.lines)
                session.execute(
                    text(
                        """
                        UPDATE operational_orders SET state=:state,version=:version,updated_at=:now
                        WHERE organization_id=:organization_id AND id=:id
                        """
                    ), {"organization_id": organization_id, "id": purchase_order_id,
                        "state": target, "version": transitioned.version, "now": now},
                )
                payload = {"id": str(receipt_id), "receipt_number": receipt_number,
                           "purchase_order_id": str(purchase_order_id), "state": target,
                           "order_version": transitioned.version,
                           "inventory_transaction_id": str(transaction_id)}
                self._record(
                    session, organization_id, actor_id, correlation_id,
                    "inventory.ledger_posted", "inventory_transaction", transaction_id,
                    "inventory.ledger_posted",
                    {"transaction_id": str(transaction_id), "reason_code": "purchase_receipt",
                     "business_reference": receipt_number},
                )
                for position_id, position_payload in position_events:
                    self._record(
                        session, organization_id, actor_id, correlation_id,
                        "inventory.position_changed", "inventory_position", position_id,
                        "inventory.position_changed", position_payload,
                    )
                self._record(
                    session, organization_id, actor_id, correlation_id,
                    "receipt.posted", "receipt", receipt_id, "receipt.posted", payload,
                )
                self._complete(session, organization_id, idempotency_key, payload)
                receipt = Receipt(receipt_id, organization_id, receipt_number,
                                  purchase_order_id, order.warehouse_id,
                                  (transaction_id,), lines, posted_at=now)
                return ReceiptResult(receipt, transitioned)
        except IntegrityError as exc:
            raise DuplicateResource("receipt number or referenced record is invalid") from exc

    @staticmethod
    def _insert_generated_task(session: Any, task: WarehouseTask, actor_id: UUID) -> None:
        session.execute(
            text(
                """
                INSERT INTO warehouse_tasks (
                  organization_id,id,task_number,task_type,warehouse_id,state,
                  source_location_id,product_id,quantity,uom,reference_type,reference_id,
                  priority,version,created_by,created_at,updated_at
                ) VALUES (
                  :organization_id,:id,:number,:type,:warehouse_id,:state,
                  :source_location_id,:product_id,:quantity,:uom,:reference_type,:reference_id,
                  :priority,:version,:actor_id,:created_at,:updated_at
                ) ON CONFLICT (organization_id,id) DO NOTHING
                """
            ), {"organization_id": task.organization_id, "id": task.id,
                "number": task.task_number, "type": task.task_type.value,
                "warehouse_id": task.warehouse_id, "state": task.state.value,
                "source_location_id": task.source_location_id, "product_id": task.product_id,
                "quantity": task.quantity, "uom": task.uom,
                "reference_type": task.reference_type, "reference_id": task.reference_id,
                "priority": task.priority, "version": task.version, "actor_id": actor_id,
                "created_at": task.created_at, "updated_at": task.updated_at},
        )

    def _load_receipt(
        self, session: Any, organization_id: UUID, receipt_id: UUID,
        posting_lines: tuple[ReceiptPostingLine, ...],
    ) -> Receipt:
        row = session.execute(
            text("SELECT * FROM receipts WHERE organization_id=:organization_id AND id=:id"),
            {"organization_id": organization_id, "id": receipt_id},
        ).mappings().one_or_none()
        if row is None:
            raise ResourceNotFound("receipt not found")
        return Receipt(
            UUID(str(row["id"])), organization_id, row["receipt_number"],
            UUID(str(row["purchase_order_id"])), UUID(str(row["warehouse_id"])),
            (UUID(str(row["inventory_transaction_id"])),), posting_lines,
            row["state"], row["version"], row["posted_at"],
        )

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
