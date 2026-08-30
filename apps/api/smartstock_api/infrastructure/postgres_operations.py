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
    InsufficientStock,
    InvalidQuantity,
    InvalidStateTransition,
    ResourceNotFound,
)
from smartstock_api.domain.inventory import (
    CountCommand,
    StockCondition,
    StockKey,
    TransferReceiptCommand,
    TransferShipmentCommand,
)
from smartstock_api.domain.operations import (
    AllocationPostingLine,
    AllocationResult,
    OperationalOrder,
    OrderKind,
    OrderLine,
    Receipt,
    ReceiptPostingLine,
    ReceiptResult,
    ReturnAuthorization,
    ReturnLine,
    ReturnReceiptLine,
    ReturnReceiptResult,
    SalesAllocation,
    Shipment,
    ShipmentPostingLine,
    ShipmentResult,
    WarehouseTask,
    WarehouseTaskCountResult,
    WarehouseTaskState,
    WarehouseTaskType,
    WarehouseTransferReceiptResult,
    WarehouseTransferShipmentResult,
)
from smartstock_api.domain.valuation import (
    CostLayer,
    ValuationMethod,
    consume_fifo,
    weighted_average_cost,
)
from smartstock_api.infrastructure.database import TenantSessionFactory
from smartstock_api.infrastructure.postgres_inventory import PostgresInventoryStore


class PostgresOperationsStore:
    def __init__(self, sessions: TenantSessionFactory) -> None:
        self._sessions = sessions
        self._inventory = PostgresInventoryStore(sessions)

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
                  organization_id,id,task_number,task_type,warehouse_id,
                  destination_warehouse_id,state,source_location_id,destination_location_id,
                  product_id,quantity,uom,condition,ownership,lot_id,serial_id,
                  expected_position_version,reference_type,reference_id,
                  priority,version,created_by,created_at,updated_at
                ) VALUES (
                  :organization_id,:id,:number,:type,:warehouse_id,
                  :destination_warehouse_id,:state,:source_location_id,:destination_location_id,
                  :product_id,:quantity,:uom,:condition,:ownership,:lot_id,:serial_id,
                  :expected_position_version,:reference_type,:reference_id,
                  :priority,:version,:actor_id,:created_at,:updated_at
                ) ON CONFLICT (organization_id,id) DO NOTHING
                """
            ), {"organization_id": task.organization_id, "id": task.id,
                "number": task.task_number, "type": task.task_type.value,
                "warehouse_id": task.warehouse_id,
                "destination_warehouse_id": task.destination_warehouse_id,
                "state": task.state.value,
                "source_location_id": task.source_location_id,
                "destination_location_id": task.destination_location_id,
                "product_id": task.product_id,
                "quantity": task.quantity, "uom": task.uom,
                "condition": task.condition.value, "ownership": task.ownership,
                "lot_id": task.lot_id, "serial_id": task.serial_id,
                "expected_position_version": task.expected_position_version,
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

    def allocate_sales_order(
        self,
        organization_id: UUID,
        actor_id: UUID,
        sales_order_id: UUID,
        allocation_id: UUID,
        lines: tuple[AllocationPostingLine, ...],
        expected_order_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> AllocationResult:
        if not lines:
            raise InvalidQuantity("an allocation requires at least one line")
        fingerprint = self._hash(
            {"command": "allocate_sales_order", "order_id": sales_order_id,
             "allocation_id": allocation_id, "lines": lines,
             "expected_version": expected_order_version}
        )
        try:
            with self._sessions.session(organization_id, actor_id) as session:
                prior = self._claim(session, organization_id, idempotency_key, fingerprint)
                if prior is not None:
                    order = self._load_order(
                        session, organization_id, OrderKind.SALES, sales_order_id
                    )
                    allocation = self._load_allocation(
                        session, organization_id, allocation_id, lines
                    )
                    return AllocationResult(allocation, order, True)
                order = self._load_order(
                    session, organization_id, OrderKind.SALES, sales_order_id, lock=True
                )
                if order.version != expected_order_version:
                    raise ConcurrencyConflict("sales order version changed")
                if order.state not in {"confirmed", "partially_allocated", "backordered"}:
                    raise InvalidStateTransition("sales order is not allocatable")
                if len({(line.order_line_id, line.location_id) for line in lines}) != len(lines):
                    raise DuplicateResource("allocation position lines must be unique")
                order_lines = {line.id: line for line in order.lines}
                existing_rows = session.execute(
                    text(
                        """
                        SELECT source_id, COALESCE(sum(quantity), 0) AS quantity
                        FROM reservations
                        WHERE organization_id=:organization_id
                          AND source_type='sales_order_line' AND status='active'
                          AND source_id = ANY(CAST(:line_ids AS uuid[]))
                        GROUP BY source_id
                        """
                    ), {"organization_id": organization_id,
                        "line_ids": [str(line.id) for line in order.lines]},
                ).mappings()
                allocated = {
                    UUID(str(row["source_id"])): Decimal(row["quantity"])
                    for row in existing_rows
                }
                requested = dict(allocated)
                for posting in lines:
                    order_line = order_lines.get(posting.order_line_id)
                    if order_line is None:
                        raise ResourceNotFound("sales order line not found")
                    next_quantity = requested.get(order_line.id, Decimal("0")) + posting.quantity
                    if next_quantity > order_line.quantity:
                        raise InvalidQuantity("allocation exceeds the sales order line quantity")
                    requested[order_line.id] = next_quantity
                now = datetime.now(UTC)
                session.execute(
                    text(
                        """
                        INSERT INTO sales_allocations (
                          organization_id,id,sales_order_id,warehouse_id,created_by,created_at
                        ) VALUES (
                          :organization_id,:id,:order_id,:warehouse_id,:actor_id,:now
                        )
                        """
                    ), {"organization_id": organization_id, "id": allocation_id,
                        "order_id": sales_order_id, "warehouse_id": order.warehouse_id,
                        "actor_id": actor_id, "now": now},
                )
                reservation_ids: list[UUID] = []
                for posting in lines:
                    order_line = order_lines[posting.order_line_id]
                    position = session.execute(
                        text(
                            """
                            SELECT * FROM inventory_positions
                            WHERE organization_id=:organization_id AND product_id=:product_id
                              AND warehouse_id=:warehouse_id AND location_id=:location_id
                              AND condition='sellable' AND ownership='owned'
                              AND lot_key='00000000-0000-0000-0000-000000000000'
                              AND serial_key='00000000-0000-0000-0000-000000000000'
                              AND uom=:uom FOR UPDATE
                            """
                        ), {"organization_id": organization_id,
                            "product_id": order_line.product_id,
                            "warehouse_id": order.warehouse_id,
                            "location_id": posting.location_id, "uom": order_line.uom},
                    ).mappings().one_or_none()
                    if position is None:
                        raise InsufficientStock("allocation position has no sellable inventory")
                    if position["version"] != posting.expected_position_version:
                        raise ConcurrencyConflict(
                            f"expected position version {posting.expected_position_version}, "
                            f"got {position['version']}"
                        )
                    available = Decimal(position["on_hand"]) - Decimal(position["reserved"])
                    if available < posting.quantity:
                        raise InsufficientStock("allocation exceeds available sellable inventory")
                    reservation_id = uuid5(allocation_id, f"reservation:{posting.id}")
                    reservation_ids.append(reservation_id)
                    reservation_key = f"{idempotency_key}:{posting.id}"
                    session.execute(
                        text(
                            """
                            INSERT INTO reservations (
                              organization_id,id,inventory_position_id,source_type,source_id,
                              quantity,status,idempotency_key,version,created_by,created_at,updated_at
                            ) VALUES (
                              :organization_id,:id,:position_id,'sales_order_line',:source_id,
                              :quantity,'active',:key,1,:actor_id,:now,:now
                            )
                            """
                        ), {"organization_id": organization_id, "id": reservation_id,
                            "position_id": position["id"], "source_id": order_line.id,
                            "quantity": posting.quantity, "key": reservation_key,
                            "actor_id": actor_id, "now": now},
                    )
                    next_reserved = Decimal(position["reserved"]) + posting.quantity
                    next_version = position["version"] + 1
                    session.execute(
                        text(
                            """
                            UPDATE inventory_positions SET reserved=:reserved,version=:version,
                              updated_at=:now
                            WHERE organization_id=:organization_id AND id=:id
                            """
                        ), {"organization_id": organization_id, "id": position["id"],
                            "reserved": next_reserved, "version": next_version, "now": now},
                    )
                    session.execute(
                        text(
                            """
                            INSERT INTO sales_allocation_lines (
                              organization_id,id,allocation_id,order_line_id,
                              inventory_position_id,reservation_id,quantity,uom
                            ) VALUES (
                              :organization_id,:id,:allocation_id,:order_line_id,
                              :position_id,:reservation_id,:quantity,:uom
                            )
                            """
                        ), {"organization_id": organization_id, "id": posting.id,
                            "allocation_id": allocation_id, "order_line_id": order_line.id,
                            "position_id": position["id"], "reservation_id": reservation_id,
                            "quantity": posting.quantity, "uom": order_line.uom},
                    )
                    task = WarehouseTask(
                        id=uuid5(allocation_id, f"pick:{posting.id}"),
                        organization_id=organization_id,
                        task_number=f"PICK-{order.order_number}-{str(posting.id)[:8]}",
                        task_type=WarehouseTaskType.PICK, warehouse_id=order.warehouse_id,
                        source_location_id=posting.location_id,
                        product_id=order_line.product_id, quantity=posting.quantity,
                        uom=order_line.uom, reference_type="sales_allocation",
                        reference_id=allocation_id, priority=30,
                    )
                    self._insert_generated_task(session, task, actor_id)
                    reservation_payload = {
                        "reservation_id": str(reservation_id),
                        "sales_order_id": str(sales_order_id),
                        "order_line_id": str(order_line.id),
                        "inventory_position_id": str(position["id"]),
                        "quantity": str(posting.quantity),
                        "position_version": str(next_version),
                    }
                    self._record(
                        session, organization_id, actor_id, correlation_id,
                        "inventory.reservation_created", "reservation", reservation_id,
                        "inventory.reservation_created", reservation_payload,
                    )
                    self._record(
                        session, organization_id, actor_id, correlation_id,
                        "warehouse_task.created", "warehouse_task", task.id,
                        "warehouse_task.created",
                        {"id": str(task.id), "task_number": task.task_number,
                         "task_type": task.task_type.value, "quantity": str(task.quantity),
                         "reference_id": str(allocation_id)},
                    )
                fully_allocated = all(
                    requested.get(line.id, Decimal("0")) >= line.quantity
                    for line in order.lines
                )
                target = "allocated" if fully_allocated else "partially_allocated"
                transitioned = order.transition(target, organization_id, expected_order_version)
                session.execute(
                    text(
                        """
                        UPDATE operational_orders SET state=:state,version=:version,updated_at=:now
                        WHERE organization_id=:organization_id AND id=:id
                        """
                    ), {"organization_id": organization_id, "id": sales_order_id,
                        "state": target, "version": transitioned.version, "now": now},
                )
                payload = {
                    "id": str(allocation_id), "sales_order_id": str(sales_order_id),
                    "state": target, "order_version": transitioned.version,
                    "reservation_ids": [str(item) for item in reservation_ids],
                }
                self._record(
                    session, organization_id, actor_id, correlation_id,
                    "order.allocation_changed", "sales_allocation", allocation_id,
                    "order.allocation_changed", payload,
                )
                self._complete(session, organization_id, idempotency_key, payload)
                allocation = SalesAllocation(
                    allocation_id, organization_id, sales_order_id, order.warehouse_id,
                    tuple(reservation_ids), lines, created_at=now,
                )
                return AllocationResult(allocation, transitioned)
        except IntegrityError as exc:
            raise DuplicateResource("allocation or reservation already exists") from exc

    def _load_allocation(
        self, session: Any, organization_id: UUID, allocation_id: UUID,
        posting_lines: tuple[AllocationPostingLine, ...],
    ) -> SalesAllocation:
        row = session.execute(
            text(
                """
                SELECT a.*, array_agg(l.reservation_id ORDER BY l.id) AS reservation_ids
                FROM sales_allocations a JOIN sales_allocation_lines l
                  ON l.organization_id=a.organization_id AND l.allocation_id=a.id
                WHERE a.organization_id=:organization_id AND a.id=:id
                GROUP BY a.organization_id,a.id
                """
            ), {"organization_id": organization_id, "id": allocation_id},
        ).mappings().one_or_none()
        if row is None:
            raise ResourceNotFound("sales allocation not found")
        return SalesAllocation(
            UUID(str(row["id"])), organization_id, UUID(str(row["sales_order_id"])),
            UUID(str(row["warehouse_id"])),
            tuple(UUID(str(item)) for item in row["reservation_ids"]), posting_lines,
            row["state"], row["version"], row["created_at"],
        )

    def post_shipment(
        self, organization_id: UUID, actor_id: UUID, sales_order_id: UUID,
        shipment_id: UUID, lines: tuple[ShipmentPostingLine, ...],
        expected_order_version: int, correlation_id: UUID, idempotency_key: str,
    ) -> ShipmentResult:
        if not lines:
            raise InvalidQuantity("a shipment requires at least one reservation")
        fingerprint = self._hash({"command": "post_shipment", "order_id": sales_order_id,
                                  "shipment_id": shipment_id, "lines": lines,
                                  "expected_version": expected_order_version})
        try:
            with self._sessions.session(organization_id, actor_id) as session:
                prior = self._claim(session, organization_id, idempotency_key, fingerprint)
                if prior is not None:
                    return ShipmentResult(
                        self._load_shipment(session, organization_id, shipment_id, lines),
                        self._load_order(session, organization_id, OrderKind.SALES, sales_order_id),
                        True,
                    )
                order = self._load_order(
                    session, organization_id, OrderKind.SALES, sales_order_id, lock=True
                )
                if order.version != expected_order_version:
                    raise ConcurrencyConflict("sales order version changed")
                if order.state not in {"picking", "partially_shipped"}:
                    raise InvalidStateTransition("sales order is not ready to ship")
                if len({line.reservation_id for line in lines}) != len(lines):
                    raise DuplicateResource("shipment reservations must be unique")
                order_lines = {line.id: line for line in order.lines}
                organization = session.execute(
                    text("SELECT valuation_method,currency FROM organizations WHERE id=:id"),
                    {"id": organization_id},
                ).mappings().one()
                method = ValuationMethod(organization["valuation_method"])
                now = datetime.now(UTC)
                transaction_id = shipment_id
                session.execute(text(
                    """INSERT INTO inventory_transactions (
                         organization_id,id,actor_id,reason_code,business_reference,
                         idempotency_key,correlation_id,occurred_at)
                       VALUES (:organization_id,:id,:actor_id,'shipment',:reference,
                         :key,:correlation_id,:now)"""
                ), {"organization_id": organization_id, "id": transaction_id,
                    "actor_id": actor_id, "reference": str(sales_order_id),
                    "key": idempotency_key, "correlation_id": correlation_id, "now": now})
                session.execute(text(
                    """INSERT INTO shipments (
                         organization_id,id,sales_order_id,warehouse_id,
                         inventory_transaction_id,shipped_by,shipped_at)
                       VALUES (:organization_id,:id,:order_id,:warehouse_id,
                         :transaction_id,:actor_id,:now)"""
                ), {"organization_id": organization_id, "id": shipment_id,
                    "order_id": sales_order_id, "warehouse_id": order.warehouse_id,
                    "transaction_id": transaction_id, "actor_id": actor_id, "now": now})
                shipped: dict[UUID, Decimal] = {}
                line_number = 1
                for posting in lines:
                    order_line = order_lines.get(posting.order_line_id)
                    if order_line is None:
                        raise ResourceNotFound("sales order line not found")
                    row = session.execute(text(
                        """SELECT r.id AS reservation_id,r.source_id,r.quantity,
                             r.status,r.version AS reservation_version,
                             p.*
                           FROM reservations r JOIN inventory_positions p
                             ON p.organization_id=r.organization_id
                            AND p.id=r.inventory_position_id
                           WHERE r.organization_id=:organization_id AND r.id=:reservation_id
                           FOR UPDATE OF r,p"""
                    ), {"organization_id": organization_id,
                        "reservation_id": posting.reservation_id}).mappings().one_or_none()
                    if row is None or UUID(str(row["source_id"])) != order_line.id:
                        raise ResourceNotFound("order-line reservation not found")
                    if row["status"] != "active":
                        raise ConcurrencyConflict("reservation is no longer active")
                    if row["reservation_version"] != posting.expected_reservation_version:
                        raise ConcurrencyConflict("reservation version changed")
                    if row["version"] != posting.expected_position_version:
                        raise ConcurrencyConflict("inventory position version changed")
                    quantity = Decimal(row["quantity"])
                    if Decimal(row["on_hand"]) < quantity or Decimal(row["reserved"]) < quantity:
                        raise InsufficientStock("reserved inventory is no longer shippable")
                    current_average = Decimal(row["average_unit_cost"])
                    current_value = Decimal(row["inventory_value"])
                    issued_cost = quantity * current_average
                    unit_cost = current_average
                    if method == ValuationMethod.FIFO:
                        layers = session.execute(text(
                            """SELECT id,remaining_quantity,unit_cost FROM cost_layers
                               WHERE organization_id=:organization_id AND product_id=:product_id
                                 AND warehouse_id=:warehouse_id AND remaining_quantity>0
                               ORDER BY received_at,id FOR UPDATE"""
                        ), {"organization_id": organization_id,
                            "product_id": order_line.product_id,
                            "warehouse_id": order.warehouse_id}).mappings().all()
                        consumptions, issued_cost = consume_fifo([
                            CostLayer(UUID(str(item["id"])), Decimal(item["remaining_quantity"]),
                                      Decimal(item["unit_cost"])) for item in layers
                        ], quantity)
                        for item in consumptions:
                            session.execute(text(
                                """UPDATE cost_layers SET remaining_quantity=remaining_quantity-:q
                                   WHERE organization_id=:organization_id AND id=:id"""
                            ), {"organization_id": organization_id,
                                "id": item.layer_id, "q": item.quantity})
                        unit_cost = issued_cost / quantity
                    next_on_hand = Decimal(row["on_hand"]) - quantity
                    next_reserved = Decimal(row["reserved"]) - quantity
                    next_value = current_value - issued_cost
                    next_average = next_value / next_on_hand if next_on_hand else Decimal("0")
                    session.execute(text(
                        """UPDATE inventory_positions SET on_hand=:on_hand,reserved=:reserved,
                             average_unit_cost=:average,inventory_value=:value,
                             version=version+1,updated_at=:now
                           WHERE organization_id=:organization_id AND id=:id"""
                    ), {"organization_id": organization_id, "id": row["id"],
                        "on_hand": next_on_hand, "reserved": next_reserved,
                        "average": next_average, "value": next_value, "now": now})
                    session.execute(text(
                        """UPDATE reservations SET status='consumed',version=version+1,updated_at=:now
                           WHERE organization_id=:organization_id AND id=:id"""
                    ), {"organization_id": organization_id,
                        "id": posting.reservation_id, "now": now})
                    session.execute(text(
                        """INSERT INTO inventory_ledger_lines (
                             organization_id,transaction_id,line_number,account,product_id,
                             warehouse_id,location_id,condition,ownership,quantity,uom,unit_cost,currency)
                           VALUES (:organization_id,:transaction_id,:line_number,'on_hand',:product_id,
                             :warehouse_id,:location_id,'sellable','owned',-:quantity,:uom,:cost,:currency),
                            (:organization_id,:transaction_id,:external_line,'external',NULL,NULL,NULL,
                             NULL,NULL,:quantity,NULL,:cost,:currency)"""
                    ), {"organization_id": organization_id, "transaction_id": transaction_id,
                        "line_number": line_number, "external_line": line_number + 1,
                        "product_id": order_line.product_id, "warehouse_id": order.warehouse_id,
                        "location_id": row["location_id"], "quantity": quantity,
                        "uom": order_line.uom, "cost": unit_cost,
                        "currency": order.currency})
                    line_number += 2
                    session.execute(text(
                        """INSERT INTO valuation_postings (
                             organization_id,inventory_transaction_id,product_id,warehouse_id,
                             valuation_method,quantity,unit_cost,total_cost,currency,posted_at)
                           VALUES (:organization_id,:transaction_id,:product_id,:warehouse_id,
                             :method,-:quantity,:cost,-:total,:currency,:now)"""
                    ), {"organization_id": organization_id, "transaction_id": transaction_id,
                        "product_id": order_line.product_id, "warehouse_id": order.warehouse_id,
                        "method": method.value, "quantity": quantity, "cost": unit_cost,
                        "total": issued_cost, "currency": order.currency, "now": now})
                    session.execute(text(
                        """INSERT INTO shipment_lines (
                             organization_id,id,shipment_id,order_line_id,reservation_id,
                             inventory_position_id,product_id,location_id,quantity,uom,unit_cost,currency)
                           VALUES (:organization_id,:id,:shipment_id,:order_line_id,:reservation_id,
                             :position_id,:product_id,:location_id,:quantity,:uom,:cost,:currency)"""
                    ), {"organization_id": organization_id, "id": posting.id,
                        "shipment_id": shipment_id, "order_line_id": order_line.id,
                        "reservation_id": posting.reservation_id, "position_id": row["id"],
                        "product_id": order_line.product_id, "location_id": row["location_id"],
                        "quantity": quantity, "uom": order_line.uom,
                        "cost": unit_cost, "currency": order.currency})
                    shipped[order_line.id] = shipped.get(order_line.id, Decimal("0")) + quantity
                next_lines = tuple(replace(line,
                    received_or_shipped_quantity=line.received_or_shipped_quantity
                    + shipped.get(line.id, Decimal("0"))) for line in order.lines)
                if any(line.received_or_shipped_quantity > line.quantity for line in next_lines):
                    raise InvalidQuantity("shipment exceeds ordered quantity")
                target = "shipped" if all(line.open_quantity == 0 for line in next_lines) \
                    else "partially_shipped"
                transitioned = replace(
                    order.transition(target, organization_id, expected_order_version),
                    lines=next_lines,
                )
                for line in next_lines:
                    session.execute(text(
                        """UPDATE operational_order_lines SET processed_quantity=:quantity
                           WHERE organization_id=:organization_id AND id=:id"""
                    ), {"organization_id": organization_id, "id": line.id,
                        "quantity": line.received_or_shipped_quantity})
                session.execute(text(
                    """UPDATE operational_orders SET state=:state,version=:version,updated_at=:now
                       WHERE organization_id=:organization_id AND id=:id"""
                ), {"organization_id": organization_id, "id": sales_order_id,
                    "state": target, "version": transitioned.version, "now": now})
                payload = {"id": str(shipment_id), "sales_order_id": str(sales_order_id),
                           "state": target, "inventory_transaction_id": str(transaction_id)}
                self._record(session, organization_id, actor_id, correlation_id,
                             "shipment.shipped", "shipment", shipment_id,
                             "shipment.shipped", payload)
                self._complete(session, organization_id, idempotency_key, payload)
                return ShipmentResult(Shipment(
                    shipment_id, organization_id, sales_order_id, order.warehouse_id,
                    (transaction_id,), lines, shipped_at=now), transitioned)
        except IntegrityError as exc:
            raise DuplicateResource("shipment or reservation reference is invalid") from exc

    def _load_shipment(self, session: Any, organization_id: UUID, shipment_id: UUID,
                       posting_lines: tuple[ShipmentPostingLine, ...]) -> Shipment:
        row = session.execute(text(
            "SELECT * FROM shipments WHERE organization_id=:organization_id AND id=:id"
        ), {"organization_id": organization_id, "id": shipment_id}).mappings().one_or_none()
        if row is None:
            raise ResourceNotFound("shipment not found")
        return Shipment(UUID(str(row["id"])), organization_id,
                        UUID(str(row["sales_order_id"])), UUID(str(row["warehouse_id"])),
                        (UUID(str(row["inventory_transaction_id"])),), posting_lines,
                        row["state"], row["version"], row["shipped_at"])

    def create_return(
        self, item: ReturnAuthorization, actor_id: UUID, correlation_id: UUID,
        idempotency_key: str,
    ) -> tuple[ReturnAuthorization, bool]:
        fingerprint = self._hash({"command": "create_return", "return": item})
        try:
            with self._sessions.session(item.organization_id, actor_id) as session:
                prior = self._claim(session, item.organization_id, idempotency_key, fingerprint)
                if prior is not None:
                    return self._load_return(session, item.organization_id, item.id), True
                order = self._load_order(
                    session, item.organization_id, OrderKind.SALES, item.sales_order_id, lock=True
                )
                if order.state not in {"shipped", "delivered", "closed"}:
                    raise InvalidStateTransition("sales order is not returnable")
                by_id = {line.id: line for line in order.lines}
                for line in item.lines:
                    order_line = by_id.get(line.order_line_id)
                    if order_line is None:
                        raise ResourceNotFound("sales order line not found")
                    prior_quantity = session.execute(text(
                        """SELECT COALESCE(sum(rl.quantity),0) FROM return_lines rl
                           JOIN return_authorizations r ON r.organization_id=rl.organization_id
                            AND r.id=rl.return_id
                           WHERE rl.organization_id=:organization_id
                             AND rl.order_line_id=:line_id AND r.state NOT IN ('rejected','cancelled')"""
                    ), {"organization_id": item.organization_id,
                        "line_id": line.order_line_id}).scalar_one()
                    if Decimal(prior_quantity) + line.quantity > \
                            order_line.received_or_shipped_quantity:
                        raise InvalidQuantity("return exceeds shipped order quantity")
                session.execute(text(
                    """INSERT INTO return_authorizations (
                         organization_id,id,return_number,sales_order_id,warehouse_id,state,
                         notes,version,created_by,created_at,updated_at)
                       VALUES (:organization_id,:id,:number,:order_id,:warehouse_id,:state,
                         :notes,:version,:actor_id,:created_at,:updated_at)"""
                ), {"organization_id": item.organization_id, "id": item.id,
                    "number": item.return_number, "order_id": item.sales_order_id,
                    "warehouse_id": item.warehouse_id, "state": item.state,
                    "notes": item.notes, "version": item.version, "actor_id": actor_id,
                    "created_at": item.created_at, "updated_at": item.updated_at})
                for line in item.lines:
                    session.execute(text(
                        """INSERT INTO return_lines (
                             organization_id,id,return_id,order_line_id,product_id,
                             quantity,received_quantity,uom,reason_code)
                           VALUES (:organization_id,:id,:return_id,:order_line_id,:product_id,
                             :quantity,:received,:uom,:reason)"""
                    ), {"organization_id": item.organization_id, "id": line.id,
                        "return_id": item.id, "order_line_id": line.order_line_id,
                        "product_id": line.product_id, "quantity": line.quantity,
                        "received": line.received_quantity, "uom": line.uom,
                        "reason": line.reason_code})
                payload = {"id": str(item.id), "return_number": item.return_number,
                           "sales_order_id": str(item.sales_order_id), "state": item.state,
                           "version": item.version}
                self._record(session, item.organization_id, actor_id, correlation_id,
                             "return.requested", "return", item.id, "return.requested", payload)
                self._complete(session, item.organization_id, idempotency_key, payload)
                return item, False
        except IntegrityError as exc:
            raise DuplicateResource("return number or referenced record is invalid") from exc

    def returns_for(self, organization_id: UUID, actor_id: UUID) -> list[ReturnAuthorization]:
        with self._sessions.session(organization_id, actor_id) as session:
            ids = session.execute(text(
                "SELECT id FROM return_authorizations WHERE organization_id=:organization_id "
                "ORDER BY created_at DESC"
            ), {"organization_id": organization_id}).scalars().all()
            return [self._load_return(session, organization_id, UUID(str(item))) for item in ids]

    def return_record(self, organization_id: UUID, actor_id: UUID,
                      return_id: UUID) -> ReturnAuthorization:
        with self._sessions.session(organization_id, actor_id) as session:
            return self._load_return(session, organization_id, return_id)

    def transition_return(
        self, organization_id: UUID, actor_id: UUID, return_id: UUID, target: str,
        expected_version: int, correlation_id: UUID, idempotency_key: str,
    ) -> tuple[ReturnAuthorization, bool]:
        fingerprint = self._hash({"command": "transition_return", "id": return_id,
                                  "target": target, "version": expected_version})
        with self._sessions.session(organization_id, actor_id) as session:
            prior = self._claim(session, organization_id, idempotency_key, fingerprint)
            if prior is not None:
                return self._load_return(session, organization_id, return_id), True
            current = self._load_return(session, organization_id, return_id, lock=True)
            changed = current.transition(target, organization_id, expected_version)
            session.execute(text(
                """UPDATE return_authorizations SET state=:state,version=:version,updated_at=:now
                   WHERE organization_id=:organization_id AND id=:id"""
            ), {"organization_id": organization_id, "id": return_id,
                "state": target, "version": changed.version, "now": changed.updated_at})
            if target == "authorized":
                self._insert_generated_task(session, WarehouseTask(
                    uuid5(return_id, "receive"), organization_id,
                    f"RMA-{current.return_number}", WarehouseTaskType.RECEIVE,
                    current.warehouse_id, reference_type="return", reference_id=return_id,
                    priority=35,
                ), actor_id)
            payload = {"id": str(return_id), "state": target, "version": changed.version}
            self._record(session, organization_id, actor_id, correlation_id,
                         f"return.{target}", "return", return_id,
                         f"return.{target}", payload)
            self._complete(session, organization_id, idempotency_key, payload)
            return changed, False

    def receive_return(
        self, organization_id: UUID, actor_id: UUID, return_id: UUID,
        lines: tuple[ReturnReceiptLine, ...], expected_version: int,
        correlation_id: UUID, idempotency_key: str,
    ) -> ReturnReceiptResult:
        if not lines:
            raise InvalidQuantity("return receipt requires lines")
        fingerprint = self._hash({"command": "receive_return", "id": return_id,
                                  "lines": lines, "version": expected_version})
        with self._sessions.session(organization_id, actor_id) as session:
            prior = self._claim(session, organization_id, idempotency_key, fingerprint)
            if prior is not None:
                item = self._load_return(session, organization_id, return_id)
                return ReturnReceiptResult(item, (UUID(prior["transaction_id"]),), True)
            item = self._load_return(session, organization_id, return_id, lock=True)
            if item.state != "authorized" or item.version != expected_version:
                raise InvalidStateTransition("return is not authorized at the expected version")
            if {line.return_line_id for line in lines} != {line.id for line in item.lines}:
                raise InvalidQuantity("all authorized return lines must be received together")
            posting_by_id = {line.return_line_id: line for line in lines}
            now = datetime.now(UTC)
            organization = session.execute(text(
                "SELECT valuation_method,currency FROM organizations WHERE id=:id"
            ), {"id": organization_id}).mappings().one()
            valuation_method = ValuationMethod(organization["valuation_method"])
            transaction_id = uuid5(return_id, f"receipt:{idempotency_key}")
            session.execute(text(
                """INSERT INTO inventory_transactions (
                     organization_id,id,actor_id,reason_code,business_reference,
                     idempotency_key,correlation_id,occurred_at)
                   VALUES (:organization_id,:id,:actor_id,'return_receipt',:reference,
                     :key,:correlation_id,:now)"""
            ), {"organization_id": organization_id, "id": transaction_id,
                "actor_id": actor_id, "reference": item.return_number,
                "key": idempotency_key, "correlation_id": correlation_id, "now": now})
            line_number = 1
            next_lines: list[ReturnLine] = []
            for line in item.lines:
                posting = posting_by_id[line.id]
                session.execute(text(
                    """INSERT INTO inventory_positions (
                         organization_id,product_id,warehouse_id,location_id,condition,
                         ownership,lot_key,serial_key,uom)
                       VALUES (:organization_id,:product_id,:warehouse_id,:location_id,
                         'quarantined','owned','00000000-0000-0000-0000-000000000000',
                         '00000000-0000-0000-0000-000000000000',:uom)
                       ON CONFLICT DO NOTHING"""
                ), {"organization_id": organization_id, "product_id": line.product_id,
                    "warehouse_id": item.warehouse_id, "location_id": posting.location_id,
                    "uom": line.uom})
                position = session.execute(text(
                    """SELECT * FROM inventory_positions
                       WHERE organization_id=:organization_id AND product_id=:product_id
                         AND warehouse_id=:warehouse_id AND location_id=:location_id
                         AND condition='quarantined' AND ownership='owned'
                         AND lot_key='00000000-0000-0000-0000-000000000000'
                         AND serial_key='00000000-0000-0000-0000-000000000000'
                         AND uom=:uom FOR UPDATE"""
                ), {"organization_id": organization_id, "product_id": line.product_id,
                    "warehouse_id": item.warehouse_id, "location_id": posting.location_id,
                    "uom": line.uom}).mappings().one()
                if position["version"] != posting.expected_quarantine_version:
                    raise ConcurrencyConflict("quarantine position version changed")
                shipment_cost = session.execute(text(
                    """SELECT sl.unit_cost,sl.currency FROM shipment_lines sl
                       WHERE sl.organization_id=:organization_id
                         AND sl.order_line_id=:order_line_id ORDER BY sl.id DESC LIMIT 1"""
                ), {"organization_id": organization_id,
                    "order_line_id": line.order_line_id}).mappings().one_or_none()
                cost = Decimal(shipment_cost["unit_cost"]) if shipment_cost else Decimal("0")
                currency = shipment_cost["currency"] if shipment_cost else organization["currency"]
                next_on_hand = Decimal(position["on_hand"]) + line.quantity
                next_value = Decimal(position["inventory_value"]) + line.quantity * cost
                next_average = next_value / next_on_hand
                session.execute(text(
                    """UPDATE inventory_positions SET on_hand=:on_hand,
                         average_unit_cost=:average,inventory_value=:value,
                         version=version+1,updated_at=:now
                       WHERE organization_id=:organization_id AND id=:id"""
                ), {"organization_id": organization_id, "id": position["id"],
                    "on_hand": next_on_hand, "average": next_average,
                    "value": next_value, "now": now})
                session.execute(text(
                    """INSERT INTO inventory_ledger_lines (
                         organization_id,transaction_id,line_number,account,product_id,
                         warehouse_id,location_id,condition,ownership,quantity,uom,unit_cost,currency)
                       VALUES (:organization_id,:transaction_id,:line_number,'on_hand',:product_id,
                         :warehouse_id,:location_id,'quarantined','owned',:quantity,:uom,:cost,:currency),
                        (:organization_id,:transaction_id,:external_line,'external',NULL,NULL,NULL,
                         NULL,NULL,-:quantity,NULL,:cost,:currency)"""
                ), {"organization_id": organization_id, "transaction_id": transaction_id,
                    "line_number": line_number, "external_line": line_number + 1,
                    "product_id": line.product_id, "warehouse_id": item.warehouse_id,
                    "location_id": posting.location_id, "quantity": line.quantity,
                    "uom": line.uom, "cost": cost, "currency": currency})
                line_number += 2
                if valuation_method == ValuationMethod.FIFO:
                    session.execute(text(
                        """INSERT INTO cost_layers (
                             organization_id,product_id,warehouse_id,source_transaction_id,
                             received_at,original_quantity,remaining_quantity,unit_cost,currency)
                           VALUES (:organization_id,:product_id,:warehouse_id,:transaction_id,
                             :now,:quantity,:quantity,:cost,:currency)"""
                    ), {"organization_id": organization_id, "product_id": line.product_id,
                        "warehouse_id": item.warehouse_id, "transaction_id": transaction_id,
                        "now": now, "quantity": line.quantity, "cost": cost,
                        "currency": currency})
                session.execute(text(
                    """INSERT INTO valuation_postings (
                         organization_id,inventory_transaction_id,product_id,warehouse_id,
                         valuation_method,quantity,unit_cost,total_cost,currency,posted_at)
                       VALUES (:organization_id,:transaction_id,:product_id,:warehouse_id,
                         :method,:quantity,:cost,:total,:currency,:now)"""
                ), {"organization_id": organization_id, "transaction_id": transaction_id,
                    "product_id": line.product_id, "warehouse_id": item.warehouse_id,
                    "method": valuation_method.value, "quantity": line.quantity,
                    "cost": cost, "total": line.quantity * cost,
                    "currency": currency, "now": now})
                session.execute(text(
                    "UPDATE return_lines SET received_quantity=quantity "
                    "WHERE organization_id=:organization_id AND id=:id"
                ), {"organization_id": organization_id, "id": line.id})
                next_lines.append(replace(line, received_quantity=line.quantity))
            changed = replace(item.transition("received", organization_id, expected_version),
                              lines=tuple(next_lines))
            session.execute(text(
                """UPDATE return_authorizations SET state='received',version=:version,updated_at=:now
                   WHERE organization_id=:organization_id AND id=:id"""
            ), {"organization_id": organization_id, "id": return_id,
                "version": changed.version, "now": now})
            session.execute(text(
                """INSERT INTO return_receipts (
                     organization_id,id,return_id,inventory_transaction_id,received_by,received_at)
                   VALUES (:organization_id,:id,:return_id,:transaction_id,:actor_id,:now)"""
            ), {"organization_id": organization_id, "id": uuid5(return_id, "receipt"),
                "return_id": return_id, "transaction_id": transaction_id,
                "actor_id": actor_id, "now": now})
            payload = {"id": str(return_id), "state": "received",
                       "transaction_id": str(transaction_id), "version": changed.version}
            self._record(session, organization_id, actor_id, correlation_id,
                         "inventory.ledger_posted", "inventory_transaction", transaction_id,
                         "inventory.ledger_posted",
                         {"transaction_id": str(transaction_id),
                          "reason_code": "return_receipt"})
            self._record(session, organization_id, actor_id, correlation_id,
                         "return.received", "return", return_id, "return.received", payload)
            self._complete(session, organization_id, idempotency_key, payload)
            return ReturnReceiptResult(changed, (transaction_id,))

    def _load_return(self, session: Any, organization_id: UUID, return_id: UUID,
                     lock: bool = False) -> ReturnAuthorization:
        suffix = " FOR UPDATE" if lock else ""
        row = session.execute(text(
            "SELECT * FROM return_authorizations WHERE organization_id=:organization_id "
            "AND id=:id" + suffix
        ), {"organization_id": organization_id, "id": return_id}).mappings().one_or_none()
        if row is None:
            raise ResourceNotFound("return not found")
        lines = session.execute(text(
            "SELECT * FROM return_lines WHERE organization_id=:organization_id "
            "AND return_id=:id ORDER BY id"
        ), {"organization_id": organization_id, "id": return_id}).mappings().all()
        return ReturnAuthorization(
            UUID(str(row["id"])), organization_id, row["return_number"],
            UUID(str(row["sales_order_id"])), UUID(str(row["warehouse_id"])), row["state"],
            tuple(ReturnLine(UUID(str(line["id"])), UUID(str(line["order_line_id"])),
                             UUID(str(line["product_id"])), Decimal(line["quantity"]),
                             line["uom"], line["reason_code"],
                             Decimal(line["received_quantity"])) for line in lines),
            row["notes"], row["version"], row["created_at"], row["updated_at"],
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
                    "id", "task_number", "task_type", "warehouse_id",
                    "destination_warehouse_id", "state",
                    "source_location_id", "destination_location_id", "product_id", "quantity",
                    "uom", "condition", "ownership", "lot_id", "serial_id",
                    "expected_position_version", "reference_type", "reference_id", "assigned_to",
                    "priority", "version",
                    "created_at", "updated_at",
                )} | {"organization_id": task.organization_id, "actor_id": actor_id}
                params["task_type"] = task.task_type.value
                params["state"] = task.state.value
                session.execute(
                    text(
                        """
                        INSERT INTO warehouse_tasks (
                          organization_id, id, task_number, task_type, warehouse_id,
                          destination_warehouse_id, state,
                          source_location_id, destination_location_id, product_id, quantity, uom,
                          condition, ownership, lot_id, serial_id, expected_position_version,
                          reference_type, reference_id, assigned_to, priority, version, created_by,
                          created_at, updated_at
                        ) VALUES (
                          :organization_id, :id, :task_number, :task_type, :warehouse_id,
                          :destination_warehouse_id, :state,
                          :source_location_id, :destination_location_id, :product_id, :quantity, :uom,
                          :condition, :ownership, :lot_id, :serial_id, :expected_position_version,
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

    def task(
        self, organization_id: UUID, actor_id: UUID, task_id: UUID
    ) -> WarehouseTask:
        with self._sessions.session(organization_id, actor_id) as session:
            return self._load_task(session, organization_id, task_id)

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
            if (
                current.task_type in {WarehouseTaskType.COUNT, WarehouseTaskType.TRANSFER}
                and target == WarehouseTaskState.COMPLETED
            ):
                raise InvalidStateTransition(
                    "physical count and transfer tasks require their posting command"
                )
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

    def complete_count_task(
        self,
        organization_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        counted_quantity: Decimal,
        expected_task_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> WarehouseTaskCountResult:
        fingerprint = self._hash(
            {
                "command": "complete_count_task",
                "task_id": task_id,
                "counted_quantity": counted_quantity,
                "expected_task_version": expected_task_version,
            }
        )
        with self._sessions.session(organization_id, actor_id) as session:
            current = self._load_task(session, organization_id, task_id, lock=True)
            if current.task_type != WarehouseTaskType.COUNT:
                raise InvalidStateTransition("only count tasks can post a cycle count")
            assert current.product_id is not None
            assert current.source_location_id is not None
            assert current.uom is not None
            assert current.expected_position_version is not None
            command = CountCommand(
                organization_id=organization_id,
                actor_id=actor_id,
                count_number=current.task_number,
                stock_key=StockKey(
                    organization_id=organization_id,
                    product_id=current.product_id,
                    warehouse_id=current.warehouse_id,
                    location_id=current.source_location_id,
                    uom=current.uom,
                    condition=current.condition,
                    ownership=current.ownership,
                    lot_id=current.lot_id,
                    serial_id=current.serial_id,
                ),
                counted_quantity=counted_quantity,
                expected_position_version=current.expected_position_version,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
            prior = self._claim(session, organization_id, idempotency_key, fingerprint)
            if prior is not None:
                count = self._inventory._count_result_from_body(command, prior, replayed=True)
                return WarehouseTaskCountResult(current, count, True)
            transitioned = current.transition(
                WarehouseTaskState.COMPLETED, organization_id, expected_task_version
            )
            count = self._inventory.post_count_in_session(session, command)
            session.execute(
                text(
                    """
                    UPDATE warehouse_tasks
                    SET state=:state, version=:version, updated_at=:updated_at
                    WHERE organization_id=:organization_id AND id=:id
                    """
                ),
                {
                    "organization_id": organization_id,
                    "id": task_id,
                    "state": transitioned.state.value,
                    "version": transitioned.version,
                    "updated_at": transitioned.updated_at,
                },
            )
            body = {
                "count_id": str(count.cycle_count_id),
                "transaction_id": str(count.transaction.id) if count.transaction else None,
                "snapshot_quantity": str(count.snapshot_quantity),
                "counted_quantity": str(count.counted_quantity),
                "variance_quantity": str(count.variance_quantity),
                "on_hand": str(count.position.on_hand),
                "reserved": str(count.position.reserved),
                "average_unit_cost": str(count.position.average_unit_cost),
                "inventory_value": str(count.position.inventory_value),
                "position_version": count.position.version,
                "updated_at": count.position.updated_at.isoformat(),
                "task_id": str(task_id),
                "task_version": transitioned.version,
            }
            self._record(
                session,
                organization_id,
                actor_id,
                correlation_id,
                "warehouse_task.completed",
                "warehouse_task",
                task_id,
                "warehouse_task.completed",
                {
                    "id": str(task_id),
                    "state": transitioned.state.value,
                    "version": transitioned.version,
                    "cycle_count_id": str(count.cycle_count_id),
                },
                {"state": current.state.value, "version": current.version},
            )
            self._complete(session, organization_id, idempotency_key, body)
            return WarehouseTaskCountResult(transitioned, count)

    def ship_transfer_task(
        self,
        organization_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        expected_task_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> WarehouseTransferShipmentResult:
        fingerprint = self._hash(
            {"command": "ship_transfer_task", "task_id": task_id,
             "expected_task_version": expected_task_version}
        )
        with self._sessions.session(organization_id, actor_id) as session:
            current = self._load_task(session, organization_id, task_id, lock=True)
            if (
                current.task_type != WarehouseTaskType.TRANSFER
                or current.reference_type == "transfer_receipt"
            ):
                raise InvalidStateTransition("task is not a transfer shipment")
            assert current.destination_warehouse_id is not None
            assert current.source_location_id is not None
            assert current.destination_location_id is not None
            assert current.product_id is not None
            assert current.quantity is not None
            assert current.uom is not None
            assert current.expected_position_version is not None
            transfer_id = uuid5(current.id, "staged-transfer")
            common = {
                "organization_id": organization_id,
                "product_id": current.product_id,
                "uom": current.uom,
                "condition": current.condition,
                "ownership": current.ownership,
                "lot_id": current.lot_id,
                "serial_id": current.serial_id,
            }
            command = TransferShipmentCommand(
                organization_id, actor_id, transfer_id, current.task_number,
                StockKey(warehouse_id=current.warehouse_id,
                         location_id=current.source_location_id, **common),
                StockKey(warehouse_id=current.destination_warehouse_id,
                         location_id=current.destination_location_id, **common),
                current.quantity, current.expected_position_version,
                idempotency_key, correlation_id,
            )
            prior = self._claim(session, organization_id, idempotency_key, fingerprint)
            receipt_task_id = uuid5(transfer_id, "receipt-task")
            if prior is not None:
                return WarehouseTransferShipmentResult(
                    current,
                    self._load_task(session, organization_id, receipt_task_id),
                    self._inventory._shipment_result_from_body(
                        command, prior, replayed=True
                    ),
                    True,
                )
            transitioned = current.transition(
                WarehouseTaskState.COMPLETED, organization_id, expected_task_version
            )
            shipment = self._inventory.ship_transfer_in_session(session, command)
            session.execute(
                text(
                    """
                    UPDATE warehouse_tasks SET state=:state,version=:version,updated_at=:updated_at
                    WHERE organization_id=:organization_id AND id=:id
                    """
                ),
                {"organization_id": organization_id, "id": task_id,
                 "state": transitioned.state.value, "version": transitioned.version,
                 "updated_at": transitioned.updated_at},
            )
            receipt_task = WarehouseTask(
                id=receipt_task_id, organization_id=organization_id,
                task_number=f"RCV-{current.task_number}",
                task_type=WarehouseTaskType.TRANSFER,
                warehouse_id=current.destination_warehouse_id,
                source_location_id=current.source_location_id,
                destination_location_id=current.destination_location_id,
                product_id=current.product_id, quantity=current.quantity, uom=current.uom,
                condition=current.condition, ownership=current.ownership,
                lot_id=current.lot_id, serial_id=current.serial_id,
                expected_position_version=shipment.destination_position.version,
                reference_type="transfer_receipt", reference_id=transfer_id,
                priority=current.priority,
            )
            self._insert_generated_task(session, receipt_task, actor_id)
            body = {
                "transfer_id": str(transfer_id),
                "transaction_id": str(shipment.transaction.id),
                "quantity": str(shipment.quantity),
                "source_on_hand": str(shipment.source_position.on_hand),
                "source_reserved": str(shipment.source_position.reserved),
                "source_average": str(shipment.source_position.average_unit_cost),
                "source_value": str(shipment.source_position.inventory_value),
                "source_version": shipment.source_position.version,
                "destination_on_hand": str(shipment.destination_position.on_hand),
                "destination_reserved": str(shipment.destination_position.reserved),
                "destination_average": str(shipment.destination_position.average_unit_cost),
                "destination_value": str(shipment.destination_position.inventory_value),
                "destination_version": shipment.destination_position.version,
                "updated_at": shipment.source_position.updated_at.isoformat(),
                "task_id": str(task_id), "task_version": transitioned.version,
                "receipt_task_id": str(receipt_task.id),
            }
            self._record(
                session, organization_id, actor_id, correlation_id,
                "warehouse_task.completed", "warehouse_task", task_id,
                "warehouse_task.completed",
                {"id": str(task_id), "state": "completed",
                 "version": transitioned.version, "transfer_id": str(transfer_id)},
                {"state": current.state.value, "version": current.version},
            )
            self._record(
                session, organization_id, actor_id, correlation_id,
                "warehouse_task.created", "warehouse_task", receipt_task.id,
                "warehouse_task.created",
                {"id": str(receipt_task.id), "task_number": receipt_task.task_number,
                 "task_type": receipt_task.task_type.value,
                 "reference_id": str(transfer_id)},
            )
            self._complete(session, organization_id, idempotency_key, body)
            return WarehouseTransferShipmentResult(transitioned, receipt_task, shipment)

    def receive_transfer_task(
        self,
        organization_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        received_quantity: Decimal,
        expected_task_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> WarehouseTransferReceiptResult:
        fingerprint = self._hash(
            {"command": "receive_transfer_task", "task_id": task_id,
             "received_quantity": received_quantity,
             "expected_task_version": expected_task_version}
        )
        with self._sessions.session(organization_id, actor_id) as session:
            current = self._load_task(session, organization_id, task_id, lock=True)
            if (
                current.task_type != WarehouseTaskType.TRANSFER
                or current.reference_type != "transfer_receipt"
                or current.reference_id is None
                or current.expected_position_version is None
            ):
                raise InvalidStateTransition("task is not a transfer receipt")
            command = TransferReceiptCommand(
                organization_id, actor_id, current.reference_id, received_quantity,
                current.expected_position_version, idempotency_key, correlation_id,
            )
            prior = self._claim(session, organization_id, idempotency_key, fingerprint)
            if prior is not None:
                source_key, destination_key, _ = (
                    self._inventory.transfer_keys_in_session(
                        session, organization_id, current.reference_id
                    )
                )
                receipt = self._inventory._receipt_result_from_body(
                    command, source_key, destination_key, prior, replayed=True
                )
                return WarehouseTransferReceiptResult(current, receipt, True)
            transitioned = current.transition(
                WarehouseTaskState.COMPLETED, organization_id, expected_task_version
            )
            receipt = self._inventory.receive_transfer_in_session(session, command)
            session.execute(
                text(
                    """
                    UPDATE warehouse_tasks SET state=:state,version=:version,updated_at=:updated_at
                    WHERE organization_id=:organization_id AND id=:id
                    """
                ),
                {"organization_id": organization_id, "id": task_id,
                 "state": transitioned.state.value, "version": transitioned.version,
                 "updated_at": transitioned.updated_at},
            )
            body = {
                "transfer_id": str(receipt.transfer_id),
                "transaction_id": str(receipt.transaction.id),
                "transfer_number": receipt.transaction.business_reference,
                "shipped_quantity": str(receipt.shipped_quantity),
                "received_quantity": str(receipt.received_quantity),
                "discrepancy_quantity": str(receipt.discrepancy_quantity),
                "state": receipt.state,
                "destination_on_hand": str(receipt.destination_position.on_hand),
                "destination_reserved": str(receipt.destination_position.reserved),
                "destination_average": str(receipt.destination_position.average_unit_cost),
                "destination_value": str(receipt.destination_position.inventory_value),
                "destination_version": receipt.destination_position.version,
                "updated_at": receipt.destination_position.updated_at.isoformat(),
                "task_id": str(task_id), "task_version": transitioned.version,
            }
            self._record(
                session, organization_id, actor_id, correlation_id,
                "warehouse_task.completed", "warehouse_task", task_id,
                "warehouse_task.completed",
                {"id": str(task_id), "state": "completed",
                 "version": transitioned.version,
                 "transfer_id": str(receipt.transfer_id),
                 "transfer_state": receipt.state},
                {"state": current.state.value, "version": current.version},
            )
            self._complete(session, organization_id, idempotency_key, body)
            return WarehouseTransferReceiptResult(transitioned, receipt)

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
            warehouse_id=UUID(str(row["warehouse_id"])),
            destination_warehouse_id=uuid_or_none(row["destination_warehouse_id"]),
            state=WarehouseTaskState(row["state"]),
            source_location_id=uuid_or_none(row["source_location_id"]),
            destination_location_id=uuid_or_none(row["destination_location_id"]),
            product_id=uuid_or_none(row["product_id"]), quantity=row["quantity"], uom=row["uom"],
            condition=StockCondition(row["condition"]), ownership=row["ownership"],
            lot_id=uuid_or_none(row["lot_id"]), serial_id=uuid_or_none(row["serial_id"]),
            expected_position_version=row["expected_position_version"],
            reference_type=row["reference_type"], reference_id=uuid_or_none(row["reference_id"]),
            assigned_to=uuid_or_none(row["assigned_to"]), priority=row["priority"],
            version=row["version"], created_at=row["created_at"], updated_at=row["updated_at"],
        )
