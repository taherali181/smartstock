from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from smartstock_api.domain.errors import (
    ConcurrencyConflict,
    IdempotencyConflict,
    InsufficientStock,
    InvalidQuantity,
    ResourceNotFound,
    TenantBoundaryViolation,
)
from smartstock_api.domain.inventory import (
    AdjustmentCommand,
    AdjustmentResult,
    CountCommand,
    CountResult,
    InventoryAccount,
    InventoryPosition,
    LedgerLine,
    LedgerTransaction,
    ReconciliationResult,
    ReleaseReservationCommand,
    Reservation,
    ReservationResult,
    ReservationStatus,
    ReserveCommand,
    StockCondition,
    StockKey,
    TransferCommand,
    TransferResult,
    assert_balanced,
)
from smartstock_api.domain.valuation import CostLayer, ValuationMethod, consume_fifo, weighted_average_cost
from smartstock_api.infrastructure.database import TenantSessionFactory

ZERO_UUID = UUID("00000000-0000-0000-0000-000000000000")


class PostgresInventoryStore:
    """Atomic PostgreSQL adapter for the inventory command contract."""

    def __init__(self, sessions: TenantSessionFactory) -> None:
        self._sessions = sessions

    def positions_for(
        self, organization_id: UUID, actor_id: UUID | None = None
    ) -> list[InventoryPosition]:
        if actor_id is None:
            raise ValueError("PostgreSQL reads require actor identity")
        with self._sessions.session(organization_id, actor_id) as session:
            rows = session.execute(
                text(
                    """
                    SELECT product_id, warehouse_id, location_id, condition, ownership,
                           lot_key, serial_key, uom, on_hand, reserved, average_unit_cost,
                           inventory_value, version, updated_at
                    FROM inventory_positions
                    WHERE organization_id = :organization_id
                    ORDER BY updated_at DESC, id
                    LIMIT 250
                    """
                ),
                {"organization_id": organization_id},
            ).mappings()
            return [self._position_from_row(organization_id, row) for row in rows]

    def adjust(self, command: AdjustmentCommand) -> AdjustmentResult:
        if command.stock_key.organization_id != command.organization_id:
            raise TenantBoundaryViolation("stock key belongs to a different organization")
        if command.unit_cost is not None and command.unit_cost < 0:
            raise InvalidQuantity("unit cost cannot be negative")
        request_hash = command.fingerprint()
        now = datetime.now(UTC)
        with self._sessions.session(command.organization_id, command.actor_id) as session:
            claimed = session.execute(
                text(
                    """
                    INSERT INTO idempotency_records (
                      organization_id, key, request_hash, response_status, response_body, expires_at
                    ) VALUES (
                      :organization_id, :key, :request_hash, 0, '{}'::jsonb, :expires_at
                    ) ON CONFLICT (organization_id, key) DO NOTHING
                    RETURNING key
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "key": command.idempotency_key,
                    "request_hash": request_hash,
                    "expires_at": now + timedelta(days=7),
                },
            ).scalar_one_or_none()
            if claimed is None:
                prior = session.execute(
                    text(
                        """
                        SELECT request_hash, response_body
                        FROM idempotency_records
                        WHERE organization_id = :organization_id AND key = :key
                        FOR UPDATE
                        """
                    ),
                    {"organization_id": command.organization_id, "key": command.idempotency_key},
                ).mappings().one()
                if prior["request_hash"] != request_hash:
                    raise IdempotencyConflict(
                        "idempotency key was reused with a different command"
                    )
                if not prior["response_body"]:
                    raise IdempotencyConflict("prior command did not complete")
                return self._replayed_result(command, prior["response_body"])

            lot_key = command.stock_key.lot_id or ZERO_UUID
            serial_key = command.stock_key.serial_id or ZERO_UUID
            session.execute(
                text(
                    """
                    INSERT INTO inventory_positions (
                      organization_id, product_id, warehouse_id, location_id, condition,
                      ownership, lot_key, serial_key, uom
                    ) VALUES (
                      :organization_id, :product_id, :warehouse_id, :location_id, :condition,
                      :ownership, :lot_key, :serial_key, :uom
                    ) ON CONFLICT (
                      organization_id, product_id, warehouse_id, location_id, condition,
                      ownership, lot_key, serial_key, uom
                    ) DO NOTHING
                    """
                ),
                self._key_params(command.stock_key),
            )
            row = session.execute(
                text(
                    """
                    SELECT id, product_id, warehouse_id, location_id, condition, ownership,
                           lot_key, serial_key, uom, on_hand, reserved, average_unit_cost,
                           inventory_value, version, updated_at
                    FROM inventory_positions
                    WHERE organization_id = :organization_id
                      AND product_id = :product_id AND warehouse_id = :warehouse_id
                      AND location_id = :location_id AND condition = :condition
                      AND ownership = :ownership AND lot_key = :lot_key
                      AND serial_key = :serial_key AND uom = :uom
                    FOR UPDATE
                    """
                ),
                self._key_params(command.stock_key),
            ).mappings().one()
            if row["version"] != command.expected_version:
                raise ConcurrencyConflict(
                    f"expected position version {command.expected_version}, got {row['version']}"
                )
            next_on_hand = Decimal(row["on_hand"]) + command.quantity_delta
            reserved = Decimal(row["reserved"])
            if command.stock_key.serial_id is not None and next_on_hand not in (
                Decimal("0"),
                Decimal("1"),
            ):
                raise InvalidQuantity("a serial-number position must contain zero or one unit")
            if not command.allow_negative and next_on_hand < reserved:
                raise InsufficientStock(
                    "adjustment would reduce sellable stock below reservations"
                )

            organization = session.execute(
                text(
                    "SELECT valuation_method, currency FROM organizations WHERE id=:organization_id"
                ),
                {"organization_id": command.organization_id},
            ).mappings().one()
            valuation_method = ValuationMethod(organization["valuation_method"])
            currency = command.currency or organization["currency"]
            current_on_hand = Decimal(row["on_hand"])
            current_average = Decimal(row["average_unit_cost"])
            current_value = Decimal(row["inventory_value"])
            next_average = current_average
            next_value = current_value
            valuation_unit_cost = current_average
            valuation_total = command.quantity_delta * current_average

            transaction_id = uuid4()
            session.execute(
                text(
                    """
                    INSERT INTO inventory_transactions (
                      organization_id, id, actor_id, reason_code, business_reference,
                      idempotency_key, correlation_id, occurred_at
                    ) VALUES (
                      :organization_id, :id, :actor_id, :reason_code, :business_reference,
                      :idempotency_key, :correlation_id, :occurred_at
                    )
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "id": transaction_id,
                    "actor_id": command.actor_id,
                    "reason_code": command.reason_code,
                    "business_reference": command.business_reference,
                    "idempotency_key": command.idempotency_key,
                    "correlation_id": command.correlation_id,
                    "occurred_at": now,
                },
            )
            if command.quantity_delta > 0 and command.unit_cost is not None:
                valuation_unit_cost = command.unit_cost
                valuation_total = command.quantity_delta * command.unit_cost
                next_value = current_value + valuation_total
                if valuation_method == ValuationMethod.WEIGHTED_AVERAGE:
                    next_average = weighted_average_cost(
                        current_on_hand,
                        current_average,
                        command.quantity_delta,
                        command.unit_cost,
                    )
                else:
                    session.execute(
                        text(
                            """
                            INSERT INTO cost_layers (
                              organization_id, product_id, warehouse_id, source_transaction_id,
                              received_at, original_quantity, remaining_quantity, unit_cost, currency
                            ) VALUES (
                              :organization_id, :product_id, :warehouse_id, :transaction_id,
                              :received_at, :quantity, :quantity, :unit_cost, :currency
                            )
                            """
                        ),
                        {
                            "organization_id": command.organization_id,
                            "product_id": command.stock_key.product_id,
                            "warehouse_id": command.stock_key.warehouse_id,
                            "transaction_id": transaction_id,
                            "received_at": now,
                            "quantity": command.quantity_delta,
                            "unit_cost": command.unit_cost,
                            "currency": currency,
                        },
                    )
                    next_average = next_value / next_on_hand if next_on_hand else Decimal("0")
            elif command.quantity_delta < 0:
                issued_quantity = -command.quantity_delta
                if valuation_method == ValuationMethod.FIFO:
                    layer_rows = session.execute(
                        text(
                            """
                            SELECT id, remaining_quantity, unit_cost FROM cost_layers
                            WHERE organization_id=:organization_id AND product_id=:product_id
                              AND warehouse_id=:warehouse_id AND remaining_quantity > 0
                            ORDER BY received_at, id FOR UPDATE
                            """
                        ),
                        {
                            "organization_id": command.organization_id,
                            "product_id": command.stock_key.product_id,
                            "warehouse_id": command.stock_key.warehouse_id,
                        },
                    ).mappings()
                    consumptions, issued_cost = consume_fifo(
                        [
                            CostLayer(
                                UUID(str(layer["id"])),
                                Decimal(layer["remaining_quantity"]),
                                Decimal(layer["unit_cost"]),
                            )
                            for layer in layer_rows
                        ],
                        issued_quantity,
                    )
                    for consumption in consumptions:
                        session.execute(
                            text(
                                """
                                UPDATE cost_layers
                                SET remaining_quantity = remaining_quantity - :quantity
                                WHERE organization_id=:organization_id AND id=:id
                                """
                            ),
                            {
                                "organization_id": command.organization_id,
                                "id": consumption.layer_id,
                                "quantity": consumption.quantity,
                            },
                        )
                    valuation_total = -issued_cost
                    valuation_unit_cost = issued_cost / issued_quantity
                else:
                    valuation_total = command.quantity_delta * current_average
                    valuation_unit_cost = current_average
                next_value = current_value + valuation_total
                next_average = next_value / next_on_hand if next_on_hand else Decimal("0")
            line_params = self._key_params(command.stock_key) | {
                "transaction_id": transaction_id,
                "quantity": command.quantity_delta,
            }
            session.execute(
                text(
                    """
                    INSERT INTO inventory_ledger_lines (
                      organization_id, transaction_id, line_number, account, product_id,
                      warehouse_id, location_id, condition, ownership, lot_id, serial_id,
                      quantity, uom, unit_cost, currency
                    ) VALUES (
                      :organization_id, :transaction_id, 1, 'on_hand', :product_id,
                      :warehouse_id, :location_id, :condition, :ownership,
                      NULLIF(:lot_key, '00000000-0000-0000-0000-000000000000'::uuid),
                      NULLIF(:serial_key, '00000000-0000-0000-0000-000000000000'::uuid),
                      :quantity, :uom, :valuation_unit_cost, :currency
                    ), (
                      :organization_id, :transaction_id, 2, 'external', NULL,
                      NULL, NULL, NULL, NULL, NULL, NULL, -:quantity, NULL,
                      :valuation_unit_cost, :currency
                    )
                    """
                ),
                line_params
                | {"valuation_unit_cost": valuation_unit_cost, "currency": currency},
            )
            next_version = row["version"] + 1
            session.execute(
                text(
                    """
                    UPDATE inventory_positions
                    SET on_hand = :on_hand, average_unit_cost = :average_unit_cost,
                        inventory_value = :inventory_value,
                        version = :version, updated_at = :updated_at
                    WHERE organization_id = :organization_id AND id = :id
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "id": row["id"],
                    "on_hand": next_on_hand,
                    "average_unit_cost": next_average,
                    "inventory_value": next_value,
                    "version": next_version,
                    "updated_at": now,
                },
            )
            if command.unit_cost is not None or command.quantity_delta < 0:
                session.execute(
                    text(
                        """
                        INSERT INTO valuation_postings (
                          organization_id, inventory_transaction_id, product_id, warehouse_id,
                          valuation_method, quantity, unit_cost, total_cost, currency, posted_at
                        ) VALUES (
                          :organization_id, :transaction_id, :product_id, :warehouse_id,
                          :valuation_method, :quantity, :unit_cost, :total_cost, :currency, :posted_at
                        )
                        """
                    ),
                    {
                        "organization_id": command.organization_id,
                        "transaction_id": transaction_id,
                        "product_id": command.stock_key.product_id,
                        "warehouse_id": command.stock_key.warehouse_id,
                        "valuation_method": valuation_method.value,
                        "quantity": command.quantity_delta,
                        "unit_cost": valuation_unit_cost,
                        "total_cost": valuation_total,
                        "currency": currency,
                        "posted_at": now,
                    },
                )
            response_body = {
                "transaction_id": str(transaction_id),
                "on_hand": str(next_on_hand),
                "reserved": str(reserved),
                "average_unit_cost": str(next_average),
                "inventory_value": str(next_value),
                "version": next_version,
                "updated_at": now.isoformat(),
            }
            common_event = {
                "organization_id": command.organization_id,
                "aggregate_id": transaction_id,
                "correlation_id": command.correlation_id,
            }
            session.execute(
                text(
                    """
                    INSERT INTO outbox_events (
                      organization_id, topic, aggregate_id, correlation_id, payload
                    ) VALUES (
                      :organization_id, 'inventory.ledger_posted', :aggregate_id,
                      :correlation_id, CAST(:payload AS jsonb)
                    )
                    """
                ),
                common_event
                | {
                    "payload": json.dumps(
                        response_body
                        | {
                            "product_id": str(command.stock_key.product_id),
                            "quantity_delta": str(command.quantity_delta),
                        }
                    )
                },
            )
            session.execute(
                text(
                    """
                    INSERT INTO audit_events (
                      organization_id, actor_id, action, resource_type, resource_id,
                      correlation_id, after_state
                    ) VALUES (
                      :organization_id, :actor_id, 'inventory.adjusted',
                      'inventory_transaction', :resource_id, :correlation_id,
                      CAST(:after_state AS jsonb)
                    )
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "actor_id": command.actor_id,
                    "resource_id": str(transaction_id),
                    "correlation_id": command.correlation_id,
                    "after_state": json.dumps(response_body),
                },
            )
            session.execute(
                text(
                    """
                    UPDATE idempotency_records
                    SET response_status = 201, response_body = CAST(:body AS jsonb)
                    WHERE organization_id = :organization_id AND key = :key
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "key": command.idempotency_key,
                    "body": json.dumps(response_body),
                },
            )
            return self._result(command, response_body, replayed=False)

    def reserve(self, command: ReserveCommand) -> ReservationResult:
        if command.stock_key.organization_id != command.organization_id:
            raise TenantBoundaryViolation("stock key belongs to a different organization")
        if command.quantity <= 0:
            raise InvalidQuantity("reservation quantity must be positive")
        now = datetime.now(UTC)
        with self._sessions.session(command.organization_id, command.actor_id) as session:
            prior = self._claim_idempotency(
                session,
                organization_id=command.organization_id,
                key=command.idempotency_key,
                request_hash=command.fingerprint(),
                expires_at=now + timedelta(days=7),
            )
            if prior is not None:
                return self._reservation_result_from_body(command.stock_key, prior, replayed=True)
            session.execute(
                text(
                    """
                    INSERT INTO inventory_positions (
                      organization_id, product_id, warehouse_id, location_id, condition,
                      ownership, lot_key, serial_key, uom
                    ) VALUES (
                      :organization_id, :product_id, :warehouse_id, :location_id, :condition,
                      :ownership, :lot_key, :serial_key, :uom
                    ) ON CONFLICT (
                      organization_id, product_id, warehouse_id, location_id, condition,
                      ownership, lot_key, serial_key, uom
                    ) DO NOTHING
                    """
                ),
                self._key_params(command.stock_key),
            )
            row = session.execute(
                text(
                    """
                    SELECT id, product_id, warehouse_id, location_id, condition, ownership,
                           lot_key, serial_key, uom, on_hand, reserved, average_unit_cost,
                           inventory_value, version, updated_at
                    FROM inventory_positions
                    WHERE organization_id=:organization_id AND product_id=:product_id
                      AND warehouse_id=:warehouse_id AND location_id=:location_id
                      AND condition=:condition AND ownership=:ownership
                      AND lot_key=:lot_key AND serial_key=:serial_key AND uom=:uom
                    FOR UPDATE
                    """
                ),
                self._key_params(command.stock_key),
            ).mappings().one()
            if row["version"] != command.expected_position_version:
                raise ConcurrencyConflict(
                    f"expected position version {command.expected_position_version}, got {row['version']}"
                )
            available = Decimal(row["on_hand"]) - Decimal(row["reserved"])
            if command.stock_key.condition != StockCondition.SELLABLE or available < command.quantity:
                raise InsufficientStock("reservation exceeds available sellable inventory")
            reservation_id = uuid4()
            session.execute(
                text(
                    """
                    INSERT INTO reservations (
                      organization_id, id, inventory_position_id, source_type, source_id,
                      quantity, status, idempotency_key, version, created_by, created_at, updated_at
                    ) VALUES (
                      :organization_id, :id, :position_id, :source_type, :source_id,
                      :quantity, 'active', :idempotency_key, 1, :actor_id, :now, :now
                    )
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "id": reservation_id,
                    "position_id": row["id"],
                    "source_type": command.source_type,
                    "source_id": command.source_id,
                    "quantity": command.quantity,
                    "idempotency_key": command.idempotency_key,
                    "actor_id": command.actor_id,
                    "now": now,
                },
            )
            next_reserved = Decimal(row["reserved"]) + command.quantity
            next_version = row["version"] + 1
            session.execute(
                text(
                    """
                    UPDATE inventory_positions
                    SET reserved=:reserved, version=:version, updated_at=:now
                    WHERE organization_id=:organization_id AND id=:id
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "id": row["id"],
                    "reserved": next_reserved,
                    "version": next_version,
                    "now": now,
                },
            )
            body = self._reservation_body(
                reservation_id=reservation_id,
                stock_key=command.stock_key,
                source_type=command.source_type,
                source_id=command.source_id,
                quantity=command.quantity,
                status=ReservationStatus.ACTIVE,
                reservation_version=1,
                created_at=now,
                position_on_hand=Decimal(row["on_hand"]),
                position_reserved=next_reserved,
                position_average=Decimal(row["average_unit_cost"]),
                position_value=Decimal(row["inventory_value"]),
                position_version=next_version,
                position_updated_at=now,
            )
            self._record_reservation_event(
                session,
                command.organization_id,
                command.actor_id,
                command.correlation_id,
                reservation_id,
                "inventory.reservation_created",
                body,
            )
            self._complete_idempotency(
                session, command.organization_id, command.idempotency_key, 201, body
            )
            return self._reservation_result_from_body(command.stock_key, body, replayed=False)

    def release_reservation(self, command: ReleaseReservationCommand) -> ReservationResult:
        now = datetime.now(UTC)
        with self._sessions.session(command.organization_id, command.actor_id) as session:
            prior = self._claim_idempotency(
                session,
                organization_id=command.organization_id,
                key=command.idempotency_key,
                request_hash=command.fingerprint(),
                expires_at=now + timedelta(days=7),
            )
            if prior is not None:
                return self._reservation_result_from_body(None, prior, replayed=True)
            row = session.execute(
                text(
                    """
                    SELECT r.id AS reservation_id, r.source_type, r.source_id, r.quantity,
                           r.status, r.version AS reservation_version, r.created_at,
                           p.id AS position_id, p.product_id, p.warehouse_id, p.location_id,
                           p.condition, p.ownership, p.lot_key, p.serial_key, p.uom,
                           p.on_hand, p.reserved, p.average_unit_cost, p.inventory_value,
                           p.version AS position_version, p.updated_at
                    FROM reservations r
                    JOIN inventory_positions p
                      ON p.organization_id=r.organization_id AND p.id=r.inventory_position_id
                    WHERE r.organization_id=:organization_id AND r.id=:reservation_id
                    FOR UPDATE OF r, p
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "reservation_id": command.reservation_id,
                },
            ).mappings().one_or_none()
            if row is None:
                raise ResourceNotFound("reservation not found")
            if row["reservation_version"] != command.expected_reservation_version:
                raise ConcurrencyConflict("reservation version does not match")
            if row["status"] != ReservationStatus.ACTIVE.value:
                raise ConcurrencyConflict("reservation is no longer active")
            stock_key = self._stock_key_from_row(command.organization_id, row)
            next_reserved = Decimal(row["reserved"]) - Decimal(row["quantity"])
            next_position_version = row["position_version"] + 1
            next_reservation_version = row["reservation_version"] + 1
            session.execute(
                text(
                    """
                    UPDATE reservations SET status='released', version=:version, updated_at=:now
                    WHERE organization_id=:organization_id AND id=:id
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "id": command.reservation_id,
                    "version": next_reservation_version,
                    "now": now,
                },
            )
            session.execute(
                text(
                    """
                    UPDATE inventory_positions
                    SET reserved=:reserved, version=:version, updated_at=:now
                    WHERE organization_id=:organization_id AND id=:id
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "id": row["position_id"],
                    "reserved": next_reserved,
                    "version": next_position_version,
                    "now": now,
                },
            )
            body = self._reservation_body(
                reservation_id=command.reservation_id,
                stock_key=stock_key,
                source_type=row["source_type"],
                source_id=UUID(str(row["source_id"])),
                quantity=Decimal(row["quantity"]),
                status=ReservationStatus.RELEASED,
                reservation_version=next_reservation_version,
                created_at=row["created_at"],
                position_on_hand=Decimal(row["on_hand"]),
                position_reserved=next_reserved,
                position_average=Decimal(row["average_unit_cost"]),
                position_value=Decimal(row["inventory_value"]),
                position_version=next_position_version,
                position_updated_at=now,
            )
            self._record_reservation_event(
                session,
                command.organization_id,
                command.actor_id,
                command.correlation_id,
                command.reservation_id,
                "inventory.reservation_released",
                body,
            )
            self._complete_idempotency(
                session, command.organization_id, command.idempotency_key, 200, body
            )
            return self._reservation_result_from_body(stock_key, body, replayed=False)

    def reconcile(self, organization_id: UUID, actor_id: UUID) -> list[ReconciliationResult]:
        with self._sessions.session(organization_id, actor_id) as session:
            rows = session.execute(
                text(
                    """
                    SELECT p.product_id, p.warehouse_id, p.location_id, p.condition,
                           p.ownership, p.lot_key, p.serial_key, p.uom,
                           r.projected_on_hand, r.ledger_on_hand,
                           r.projected_reserved, r.reservation_total
                    FROM inventory_reconciliation r
                    JOIN inventory_positions p
                      ON p.organization_id=r.organization_id AND p.id=r.inventory_position_id
                    WHERE r.organization_id=:organization_id
                    ORDER BY p.product_id, p.warehouse_id, p.location_id
                    """
                ),
                {"organization_id": organization_id},
            ).mappings()
            return [
                ReconciliationResult(
                    stock_key=self._stock_key_from_row(organization_id, row),
                    projected_on_hand=Decimal(row["projected_on_hand"]),
                    ledger_on_hand=Decimal(row["ledger_on_hand"]),
                    projected_reserved=Decimal(row["projected_reserved"]),
                    reservation_total=Decimal(row["reservation_total"]),
                )
                for row in rows
            ]

    def transfer(self, command: TransferCommand) -> TransferResult:
        if (
            command.source_key.organization_id != command.organization_id
            or command.destination_key.organization_id != command.organization_id
        ):
            raise TenantBoundaryViolation("transfer stock belongs to a different organization")
        if command.quantity <= 0:
            raise InvalidQuantity("transfer quantity must be positive")
        if command.source_key.serial_id is not None and command.quantity != Decimal("1"):
            raise InvalidQuantity("a serial-number transfer must move exactly one unit")
        if (
            command.source_key.product_id != command.destination_key.product_id
            or command.source_key.uom != command.destination_key.uom
            or command.source_key.condition != command.destination_key.condition
            or command.source_key.ownership != command.destination_key.ownership
            or command.source_key.lot_id != command.destination_key.lot_id
            or command.source_key.serial_id != command.destination_key.serial_id
        ):
            raise ValueError("transfer stock dimensions do not match")
        now = datetime.now(UTC)
        with self._sessions.session(command.organization_id, command.actor_id) as session:
            prior = self._claim_idempotency(
                session,
                organization_id=command.organization_id,
                key=command.idempotency_key,
                request_hash=command.fingerprint(),
                expires_at=now + timedelta(days=7),
            )
            if prior is not None:
                return self._transfer_result_from_body(command, prior, replayed=True)
            self._ensure_position(session, command.destination_key)
            source_id = self._position_id(session, command.source_key)
            destination_id = self._position_id(session, command.destination_key)
            if source_id is None:
                raise InsufficientStock("source position has no inventory")
            locked = session.execute(
                text(
                    """
                    SELECT id, product_id, warehouse_id, location_id, condition, ownership,
                           lot_key, serial_key, uom, on_hand, reserved, average_unit_cost,
                           inventory_value, version, updated_at
                    FROM inventory_positions
                    WHERE organization_id=:organization_id AND (id=:source_id OR id=:destination_id)
                    ORDER BY id FOR UPDATE
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "source_id": source_id,
                    "destination_id": destination_id,
                },
            ).mappings().all()
            positions = {UUID(str(row["id"])): row for row in locked}
            source = positions[source_id]
            destination = positions[destination_id]
            if source["version"] != command.expected_source_version:
                raise ConcurrencyConflict(
                    f"expected source version {command.expected_source_version}, got {source['version']}"
                )
            if destination["version"] != command.expected_destination_version:
                raise ConcurrencyConflict("destination position changed after transfer snapshot")
            if Decimal(source["on_hand"]) - Decimal(source["reserved"]) < command.quantity:
                raise InsufficientStock("transfer exceeds available source inventory")
            transfer_id = uuid4()
            transaction_id = uuid4()
            session.execute(
                text(
                    """
                    INSERT INTO transfers (
                      organization_id, id, transfer_number, source_warehouse_id,
                      destination_warehouse_id, state, version, created_by, approved_by,
                      created_at, updated_at
                    ) VALUES (
                      :organization_id, :id, :number, :source_warehouse_id,
                      :destination_warehouse_id, 'received', 5, :actor_id, :actor_id, :now, :now
                    )
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "id": transfer_id,
                    "number": command.transfer_number,
                    "source_warehouse_id": command.source_key.warehouse_id,
                    "destination_warehouse_id": command.destination_key.warehouse_id,
                    "actor_id": command.actor_id,
                    "now": now,
                },
            )
            session.execute(
                text(
                    """
                    INSERT INTO transfer_lines (
                      organization_id, transfer_id, product_id, source_location_id,
                      destination_location_id, lot_id, serial_id, uom, requested_quantity,
                      shipped_quantity, received_quantity, version
                    ) VALUES (
                      :organization_id, :transfer_id, :product_id, :source_location_id,
                      :destination_location_id, :lot_id, :serial_id, :uom, :quantity,
                      :quantity, :quantity, 3
                    )
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "transfer_id": transfer_id,
                    "product_id": command.source_key.product_id,
                    "source_location_id": command.source_key.location_id,
                    "destination_location_id": command.destination_key.location_id,
                    "lot_id": command.source_key.lot_id,
                    "serial_id": command.source_key.serial_id,
                    "uom": command.source_key.uom,
                    "quantity": command.quantity,
                },
            )
            session.execute(
                text(
                    """
                    INSERT INTO inventory_transactions (
                      organization_id, id, actor_id, reason_code, business_reference,
                      idempotency_key, correlation_id, occurred_at
                    ) VALUES (
                      :organization_id, :id, :actor_id, 'transfer', :business_reference,
                      :idempotency_key, :correlation_id, :now
                    )
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "id": transaction_id,
                    "actor_id": command.actor_id,
                    "business_reference": command.transfer_number,
                    "idempotency_key": command.idempotency_key,
                    "correlation_id": command.correlation_id,
                    "now": now,
                },
            )
            source_params = self._key_params(command.source_key)
            destination_params = {
                f"destination_{key}": value
                for key, value in self._key_params(command.destination_key).items()
                if key != "organization_id"
            }
            unit_cost = Decimal(source["average_unit_cost"])
            session.execute(
                text(
                    """
                    INSERT INTO inventory_ledger_lines (
                      organization_id, transaction_id, line_number, account, product_id,
                      warehouse_id, location_id, condition, ownership, lot_id, serial_id,
                      quantity, uom, unit_cost
                    ) VALUES
                    (:organization_id, :transaction_id, 1, 'on_hand', :product_id,
                     :warehouse_id, :location_id, :condition, :ownership,
                     NULLIF(:lot_key, '00000000-0000-0000-0000-000000000000'::uuid),
                     NULLIF(:serial_key, '00000000-0000-0000-0000-000000000000'::uuid),
                     -:quantity, :uom, :unit_cost),
                    (:organization_id, :transaction_id, 2, 'in_transit', :product_id,
                     :warehouse_id, :location_id, :condition, :ownership,
                     NULLIF(:lot_key, '00000000-0000-0000-0000-000000000000'::uuid),
                     NULLIF(:serial_key, '00000000-0000-0000-0000-000000000000'::uuid),
                     :quantity, :uom, :unit_cost),
                    (:organization_id, :transaction_id, 3, 'in_transit', :destination_product_id,
                     :destination_warehouse_id, :destination_location_id, :destination_condition,
                     :destination_ownership,
                     NULLIF(:destination_lot_key, '00000000-0000-0000-0000-000000000000'::uuid),
                     NULLIF(:destination_serial_key, '00000000-0000-0000-0000-000000000000'::uuid),
                     -:quantity, :destination_uom, :unit_cost),
                    (:organization_id, :transaction_id, 4, 'on_hand', :destination_product_id,
                     :destination_warehouse_id, :destination_location_id, :destination_condition,
                     :destination_ownership,
                     NULLIF(:destination_lot_key, '00000000-0000-0000-0000-000000000000'::uuid),
                     NULLIF(:destination_serial_key, '00000000-0000-0000-0000-000000000000'::uuid),
                     :quantity, :destination_uom, :unit_cost)
                    """
                ),
                source_params
                | destination_params
                | {
                    "transaction_id": transaction_id,
                    "quantity": command.quantity,
                    "unit_cost": unit_cost,
                },
            )
            moved_value = command.quantity * unit_cost
            source_on_hand = Decimal(source["on_hand"]) - command.quantity
            source_value = Decimal(source["inventory_value"]) - moved_value
            source_average = source_value / source_on_hand if source_on_hand else Decimal("0")
            destination_on_hand = Decimal(destination["on_hand"]) + command.quantity
            destination_value = Decimal(destination["inventory_value"]) + moved_value
            destination_average = destination_value / destination_on_hand
            for row, on_hand, value, average in (
                (source, source_on_hand, source_value, source_average),
                (destination, destination_on_hand, destination_value, destination_average),
            ):
                session.execute(
                    text(
                        """
                        UPDATE inventory_positions SET on_hand=:on_hand, inventory_value=:value,
                          average_unit_cost=:average, version=:version, updated_at=:now
                        WHERE organization_id=:organization_id AND id=:id
                        """
                    ),
                    {
                        "organization_id": command.organization_id,
                        "id": row["id"],
                        "on_hand": on_hand,
                        "value": value,
                        "average": average,
                        "version": row["version"] + 1,
                        "now": now,
                    },
                )
            body = {
                "transfer_id": str(transfer_id),
                "transaction_id": str(transaction_id),
                "quantity": str(command.quantity),
                "source_on_hand": str(source_on_hand),
                "source_reserved": str(source["reserved"]),
                "source_average": str(source_average),
                "source_value": str(source_value),
                "source_version": source["version"] + 1,
                "destination_on_hand": str(destination_on_hand),
                "destination_reserved": str(destination["reserved"]),
                "destination_average": str(destination_average),
                "destination_value": str(destination_value),
                "destination_version": destination["version"] + 1,
                "updated_at": now.isoformat(),
            }
            self._record_inventory_event(
                session,
                command.organization_id,
                command.actor_id,
                command.correlation_id,
                transfer_id,
                "transfer.received",
                "transfer",
                body,
            )
            self._complete_idempotency(
                session, command.organization_id, command.idempotency_key, 201, body
            )
            return self._transfer_result_from_body(command, body, replayed=False)

    def post_count(self, command: CountCommand) -> CountResult:
        if command.stock_key.organization_id != command.organization_id:
            raise TenantBoundaryViolation("count stock belongs to a different organization")
        if command.counted_quantity < 0:
            raise InvalidQuantity("counted quantity cannot be negative")
        if command.stock_key.serial_id is not None and command.counted_quantity not in (
            Decimal("0"),
            Decimal("1"),
        ):
            raise InvalidQuantity("a serial-number count must be zero or one")
        now = datetime.now(UTC)
        with self._sessions.session(command.organization_id, command.actor_id) as session:
            prior = self._claim_idempotency(
                session,
                organization_id=command.organization_id,
                key=command.idempotency_key,
                request_hash=command.fingerprint(),
                expires_at=now + timedelta(days=7),
            )
            if prior is not None:
                return self._count_result_from_body(command, prior, replayed=True)
            self._ensure_position(session, command.stock_key)
            row = session.execute(
                text(
                    """
                    SELECT id, product_id, warehouse_id, location_id, condition, ownership,
                           lot_key, serial_key, uom, on_hand, reserved, average_unit_cost,
                           inventory_value, version, updated_at
                    FROM inventory_positions
                    WHERE organization_id=:organization_id AND product_id=:product_id
                      AND warehouse_id=:warehouse_id AND location_id=:location_id
                      AND condition=:condition AND ownership=:ownership
                      AND lot_key=:lot_key AND serial_key=:serial_key AND uom=:uom
                    FOR UPDATE
                    """
                ),
                self._key_params(command.stock_key),
            ).mappings().one()
            if row["version"] != command.expected_position_version:
                raise ConcurrencyConflict("position changed after the count snapshot")
            if command.counted_quantity < Decimal(row["reserved"]):
                raise InsufficientStock("count would reduce stock below active reservations")
            variance = command.counted_quantity - Decimal(row["on_hand"])
            count_id = uuid4()
            session.execute(
                text(
                    """
                    INSERT INTO cycle_counts (
                      organization_id, id, count_number, warehouse_id, state, blind_count,
                      version, created_by, approved_by, created_at, updated_at
                    ) VALUES (
                      :organization_id, :id, :count_number, :warehouse_id, 'posted', true,
                      6, :actor_id, :actor_id, :now, :now
                    )
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "id": count_id,
                    "count_number": command.count_number,
                    "warehouse_id": command.stock_key.warehouse_id,
                    "actor_id": command.actor_id,
                    "now": now,
                },
            )
            session.execute(
                text(
                    """
                    INSERT INTO cycle_count_lines (
                      organization_id, cycle_count_id, inventory_position_id,
                      snapshot_quantity, counted_quantity, variance_quantity,
                      counted_by, counted_at, version
                    ) VALUES (
                      :organization_id, :count_id, :position_id, :snapshot_quantity,
                      :counted_quantity, :variance, :actor_id, :now, 3
                    )
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "count_id": count_id,
                    "position_id": row["id"],
                    "snapshot_quantity": row["on_hand"],
                    "counted_quantity": command.counted_quantity,
                    "variance": variance,
                    "actor_id": command.actor_id,
                    "now": now,
                },
            )
            transaction_id: UUID | None = None
            if variance:
                transaction_id = uuid4()
                session.execute(
                    text(
                        """
                        INSERT INTO inventory_transactions (
                          organization_id, id, actor_id, reason_code, business_reference,
                          idempotency_key, correlation_id, occurred_at
                        ) VALUES (
                          :organization_id, :id, :actor_id, 'cycle_count', :business_reference,
                          :idempotency_key, :correlation_id, :now
                        )
                        """
                    ),
                    {
                        "organization_id": command.organization_id,
                        "id": transaction_id,
                        "actor_id": command.actor_id,
                        "business_reference": command.count_number,
                        "idempotency_key": command.idempotency_key,
                        "correlation_id": command.correlation_id,
                        "now": now,
                    },
                )
                session.execute(
                    text(
                        """
                        INSERT INTO inventory_ledger_lines (
                          organization_id, transaction_id, line_number, account, product_id,
                          warehouse_id, location_id, condition, ownership, lot_id, serial_id,
                          quantity, uom, unit_cost
                        ) VALUES (
                          :organization_id, :transaction_id, 1, 'on_hand', :product_id,
                          :warehouse_id, :location_id, :condition, :ownership,
                          NULLIF(:lot_key, '00000000-0000-0000-0000-000000000000'::uuid),
                          NULLIF(:serial_key, '00000000-0000-0000-0000-000000000000'::uuid),
                          :variance, :uom, :unit_cost
                        ), (
                          :organization_id, :transaction_id, 2, 'discrepancy', NULL,
                          NULL, NULL, NULL, NULL, NULL, NULL, -:variance, NULL, :unit_cost
                        )
                        """
                    ),
                    self._key_params(command.stock_key)
                    | {
                        "transaction_id": transaction_id,
                        "variance": variance,
                        "unit_cost": row["average_unit_cost"],
                    },
                )
            next_version = row["version"] + 1
            next_value = command.counted_quantity * Decimal(row["average_unit_cost"])
            session.execute(
                text(
                    """
                    UPDATE inventory_positions SET on_hand=:on_hand, inventory_value=:value,
                      version=:version, updated_at=:now
                    WHERE organization_id=:organization_id AND id=:id
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "id": row["id"],
                    "on_hand": command.counted_quantity,
                    "value": next_value,
                    "version": next_version,
                    "now": now,
                },
            )
            body = {
                "count_id": str(count_id),
                "transaction_id": str(transaction_id) if transaction_id else None,
                "snapshot_quantity": str(row["on_hand"]),
                "counted_quantity": str(command.counted_quantity),
                "variance_quantity": str(variance),
                "on_hand": str(command.counted_quantity),
                "reserved": str(row["reserved"]),
                "average_unit_cost": str(row["average_unit_cost"]),
                "inventory_value": str(next_value),
                "position_version": next_version,
                "updated_at": now.isoformat(),
            }
            self._record_inventory_event(
                session,
                command.organization_id,
                command.actor_id,
                command.correlation_id,
                count_id,
                "inventory.count_posted",
                "cycle_count",
                body,
            )
            self._complete_idempotency(
                session, command.organization_id, command.idempotency_key, 201, body
            )
            return self._count_result_from_body(command, body, replayed=False)

    @staticmethod
    def _claim_idempotency(
        session: Any,
        *,
        organization_id: UUID,
        key: str,
        request_hash: str,
        expires_at: datetime,
    ) -> dict[str, Any] | None:
        claimed = session.execute(
            text(
                """
                INSERT INTO idempotency_records (
                  organization_id, key, request_hash, response_status, response_body, expires_at
                ) VALUES (
                  :organization_id, :key, :request_hash, 0, '{}'::jsonb, :expires_at
                ) ON CONFLICT (organization_id, key) DO NOTHING RETURNING key
                """
            ),
            {
                "organization_id": organization_id,
                "key": key,
                "request_hash": request_hash,
                "expires_at": expires_at,
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
        if prior["request_hash"] != request_hash:
            raise IdempotencyConflict("idempotency key was reused with a different command")
        if not prior["response_body"]:
            raise IdempotencyConflict("prior command did not complete")
        return dict(prior["response_body"])

    @staticmethod
    def _ensure_position(session: Any, key: StockKey) -> None:
        session.execute(
            text(
                """
                INSERT INTO inventory_positions (
                  organization_id, product_id, warehouse_id, location_id, condition,
                  ownership, lot_key, serial_key, uom
                ) VALUES (
                  :organization_id, :product_id, :warehouse_id, :location_id, :condition,
                  :ownership, :lot_key, :serial_key, :uom
                ) ON CONFLICT (
                  organization_id, product_id, warehouse_id, location_id, condition,
                  ownership, lot_key, serial_key, uom
                ) DO NOTHING
                """
            ),
            PostgresInventoryStore._key_params(key),
        )

    @staticmethod
    def _position_id(session: Any, key: StockKey) -> UUID | None:
        value = session.execute(
            text(
                """
                SELECT id FROM inventory_positions
                WHERE organization_id=:organization_id AND product_id=:product_id
                  AND warehouse_id=:warehouse_id AND location_id=:location_id
                  AND condition=:condition AND ownership=:ownership
                  AND lot_key=:lot_key AND serial_key=:serial_key AND uom=:uom
                """
            ),
            PostgresInventoryStore._key_params(key),
        ).scalar_one_or_none()
        return UUID(str(value)) if value is not None else None

    @staticmethod
    def _record_inventory_event(
        session: Any,
        organization_id: UUID,
        actor_id: UUID,
        correlation_id: UUID,
        aggregate_id: UUID,
        topic: str,
        resource_type: str,
        body: dict[str, Any],
    ) -> None:
        serialized = json.dumps(body)
        session.execute(
            text(
                """
                INSERT INTO outbox_events (
                  organization_id, topic, aggregate_id, correlation_id, actor_id, payload
                ) VALUES (
                  :organization_id, :topic, :aggregate_id, :correlation_id, :actor_id,
                  CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "organization_id": organization_id,
                "topic": topic,
                "aggregate_id": aggregate_id,
                "correlation_id": correlation_id,
                "actor_id": actor_id,
                "payload": serialized,
            },
        )
        session.execute(
            text(
                """
                INSERT INTO audit_events (
                  organization_id, actor_id, action, resource_type, resource_id,
                  correlation_id, after_state
                ) VALUES (
                  :organization_id, :actor_id, :action, :resource_type, :resource_id,
                  :correlation_id, CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "organization_id": organization_id,
                "actor_id": actor_id,
                "action": topic,
                "resource_type": resource_type,
                "resource_id": str(aggregate_id),
                "correlation_id": correlation_id,
                "payload": serialized,
            },
        )

    @classmethod
    def _transfer_result_from_body(
        cls, command: TransferCommand, body: dict[str, Any], *, replayed: bool
    ) -> TransferResult:
        occurred_at = datetime.fromisoformat(body["updated_at"])
        transaction = LedgerTransaction(
            id=UUID(body["transaction_id"]),
            organization_id=command.organization_id,
            actor_id=command.actor_id,
            reason_code="transfer",
            business_reference=command.transfer_number,
            idempotency_key=command.idempotency_key,
            occurred_at=occurred_at,
            lines=assert_balanced(
                (
                    LedgerLine(InventoryAccount.ON_HAND, -command.quantity, command.source_key),
                    LedgerLine(InventoryAccount.IN_TRANSIT, command.quantity, command.source_key),
                    LedgerLine(
                        InventoryAccount.IN_TRANSIT, -command.quantity, command.destination_key
                    ),
                    LedgerLine(
                        InventoryAccount.ON_HAND, command.quantity, command.destination_key
                    ),
                )
            ),
        )
        source = InventoryPosition(
            command.source_key,
            Decimal(body["source_on_hand"]),
            Decimal(body["source_reserved"]),
            Decimal(body["source_average"]),
            Decimal(body["source_value"]),
            int(body["source_version"]),
            occurred_at,
        )
        destination = InventoryPosition(
            command.destination_key,
            Decimal(body["destination_on_hand"]),
            Decimal(body["destination_reserved"]),
            Decimal(body["destination_average"]),
            Decimal(body["destination_value"]),
            int(body["destination_version"]),
            occurred_at,
        )
        return TransferResult(
            UUID(body["transfer_id"]), transaction, source, destination, replayed
        )

    @staticmethod
    def _count_result_from_body(
        command: CountCommand, body: dict[str, Any], *, replayed: bool
    ) -> CountResult:
        occurred_at = datetime.fromisoformat(body["updated_at"])
        transaction_id = body.get("transaction_id")
        variance = Decimal(body["variance_quantity"])
        transaction = None
        if transaction_id:
            transaction = LedgerTransaction(
                id=UUID(transaction_id),
                organization_id=command.organization_id,
                actor_id=command.actor_id,
                reason_code="cycle_count",
                business_reference=command.count_number,
                idempotency_key=command.idempotency_key,
                occurred_at=occurred_at,
                lines=assert_balanced(
                    (
                        LedgerLine(InventoryAccount.ON_HAND, variance, command.stock_key),
                        LedgerLine(InventoryAccount.DISCREPANCY, -variance),
                    )
                ),
            )
        position = InventoryPosition(
            key=command.stock_key,
            on_hand=Decimal(body["on_hand"]),
            reserved=Decimal(body["reserved"]),
            average_unit_cost=Decimal(body["average_unit_cost"]),
            inventory_value=Decimal(body["inventory_value"]),
            version=int(body["position_version"]),
            updated_at=occurred_at,
        )
        return CountResult(
            cycle_count_id=UUID(body["count_id"]),
            transaction=transaction,
            snapshot_quantity=Decimal(body["snapshot_quantity"]),
            counted_quantity=Decimal(body["counted_quantity"]),
            variance_quantity=variance,
            position=position,
            replayed=replayed,
        )

    @staticmethod
    def _complete_idempotency(
        session: Any,
        organization_id: UUID,
        key: str,
        response_status: int,
        body: dict[str, Any],
    ) -> None:
        session.execute(
            text(
                """
                UPDATE idempotency_records
                SET response_status=:response_status, response_body=CAST(:body AS jsonb)
                WHERE organization_id=:organization_id AND key=:key
                """
            ),
            {
                "organization_id": organization_id,
                "key": key,
                "response_status": response_status,
                "body": json.dumps(body),
            },
        )

    @staticmethod
    def _record_reservation_event(
        session: Any,
        organization_id: UUID,
        actor_id: UUID,
        correlation_id: UUID,
        reservation_id: UUID,
        topic: str,
        body: dict[str, Any],
    ) -> None:
        serialized = json.dumps(body)
        session.execute(
            text(
                """
                INSERT INTO outbox_events (
                  organization_id, topic, aggregate_id, correlation_id, actor_id, payload
                ) VALUES (
                  :organization_id, :topic, :aggregate_id, :correlation_id, :actor_id,
                  CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "organization_id": organization_id,
                "topic": topic,
                "aggregate_id": reservation_id,
                "correlation_id": correlation_id,
                "actor_id": actor_id,
                "payload": serialized,
            },
        )
        session.execute(
            text(
                """
                INSERT INTO audit_events (
                  organization_id, actor_id, action, resource_type, resource_id,
                  correlation_id, after_state
                ) VALUES (
                  :organization_id, :actor_id, :action, 'reservation', :resource_id,
                  :correlation_id, CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "organization_id": organization_id,
                "actor_id": actor_id,
                "action": topic,
                "resource_id": str(reservation_id),
                "correlation_id": correlation_id,
                "payload": serialized,
            },
        )

    @staticmethod
    def _reservation_body(
        *,
        reservation_id: UUID,
        stock_key: StockKey,
        source_type: str,
        source_id: UUID,
        quantity: Decimal,
        status: ReservationStatus,
        reservation_version: int,
        created_at: datetime,
        position_on_hand: Decimal,
        position_reserved: Decimal,
        position_average: Decimal,
        position_value: Decimal,
        position_version: int,
        position_updated_at: datetime,
    ) -> dict[str, Any]:
        return {
            "organization_id": str(stock_key.organization_id),
            "reservation_id": str(reservation_id),
            "source_type": source_type,
            "source_id": str(source_id),
            "quantity": str(quantity),
            "status": status.value,
            "reservation_version": reservation_version,
            "reservation_created_at": created_at.isoformat(),
            "product_id": str(stock_key.product_id),
            "warehouse_id": str(stock_key.warehouse_id),
            "location_id": str(stock_key.location_id),
            "condition": stock_key.condition.value,
            "ownership": stock_key.ownership,
            "lot_id": str(stock_key.lot_id) if stock_key.lot_id else None,
            "serial_id": str(stock_key.serial_id) if stock_key.serial_id else None,
            "uom": stock_key.uom,
            "position_on_hand": str(position_on_hand),
            "position_reserved": str(position_reserved),
            "position_average_unit_cost": str(position_average),
            "position_inventory_value": str(position_value),
            "position_version": position_version,
            "position_updated_at": position_updated_at.isoformat(),
        }

    @classmethod
    def _reservation_result_from_body(
        cls, stock_key: StockKey | None, body: dict[str, Any], *, replayed: bool
    ) -> ReservationResult:
        if stock_key is None:
            stock_key = StockKey(
                organization_id=UUID(str(body.get("organization_id", ZERO_UUID))),
                product_id=UUID(body["product_id"]),
                warehouse_id=UUID(body["warehouse_id"]),
                location_id=UUID(body["location_id"]),
                condition=StockCondition(body["condition"]),
                ownership=body["ownership"],
                lot_id=UUID(body["lot_id"]) if body.get("lot_id") else None,
                serial_id=UUID(body["serial_id"]) if body.get("serial_id") else None,
                uom=body["uom"],
            )
        reservation = Reservation(
            id=UUID(body["reservation_id"]),
            organization_id=stock_key.organization_id,
            stock_key=stock_key,
            source_type=body["source_type"],
            source_id=UUID(body["source_id"]),
            quantity=Decimal(body["quantity"]),
            status=ReservationStatus(body["status"]),
            version=int(body["reservation_version"]),
            created_at=datetime.fromisoformat(body["reservation_created_at"]),
        )
        position = InventoryPosition(
            key=stock_key,
            on_hand=Decimal(body["position_on_hand"]),
            reserved=Decimal(body["position_reserved"]),
            average_unit_cost=Decimal(body["position_average_unit_cost"]),
            inventory_value=Decimal(body["position_inventory_value"]),
            version=int(body["position_version"]),
            updated_at=datetime.fromisoformat(body["position_updated_at"]),
        )
        return ReservationResult(reservation, position, replayed)

    @staticmethod
    def _stock_key_from_row(organization_id: UUID, row: Any) -> StockKey:
        return StockKey(
            organization_id=organization_id,
            product_id=UUID(str(row["product_id"])),
            warehouse_id=UUID(str(row["warehouse_id"])),
            location_id=UUID(str(row["location_id"])),
            condition=StockCondition(row["condition"]),
            ownership=row["ownership"],
            lot_id=None if UUID(str(row["lot_key"])) == ZERO_UUID else UUID(str(row["lot_key"])),
            serial_id=None
            if UUID(str(row["serial_key"])) == ZERO_UUID
            else UUID(str(row["serial_key"])),
            uom=row["uom"],
        )

    @staticmethod
    def _key_params(key: StockKey) -> dict[str, Any]:
        return {
            "organization_id": key.organization_id,
            "product_id": key.product_id,
            "warehouse_id": key.warehouse_id,
            "location_id": key.location_id,
            "condition": key.condition.value,
            "ownership": key.ownership,
            "lot_key": key.lot_id or ZERO_UUID,
            "serial_key": key.serial_id or ZERO_UUID,
            "uom": key.uom,
        }

    @staticmethod
    def _position_from_row(organization_id: UUID, row: Any) -> InventoryPosition:
        return InventoryPosition(
            key=StockKey(
                organization_id=organization_id,
                product_id=UUID(str(row["product_id"])),
                warehouse_id=UUID(str(row["warehouse_id"])),
                location_id=UUID(str(row["location_id"])),
                condition=StockCondition(row["condition"]),
                ownership=row["ownership"],
                lot_id=None if UUID(str(row["lot_key"])) == ZERO_UUID else UUID(str(row["lot_key"])),
                serial_id=None
                if UUID(str(row["serial_key"])) == ZERO_UUID
                else UUID(str(row["serial_key"])),
                uom=row["uom"],
            ),
            on_hand=Decimal(row["on_hand"]),
            reserved=Decimal(row["reserved"]),
            average_unit_cost=Decimal(row.get("average_unit_cost", 0)),
            inventory_value=Decimal(row.get("inventory_value", 0)),
            version=row["version"],
            updated_at=row["updated_at"],
        )

    def _replayed_result(
        self, command: AdjustmentCommand, body: dict[str, Any]
    ) -> AdjustmentResult:
        return self._result(command, body, replayed=True)

    @staticmethod
    def _result(
        command: AdjustmentCommand, body: dict[str, Any], *, replayed: bool
    ) -> AdjustmentResult:
        lines = assert_balanced(
            (
                LedgerLine(InventoryAccount.ON_HAND, command.quantity_delta, command.stock_key),
                LedgerLine(InventoryAccount.EXTERNAL, -command.quantity_delta),
            )
        )
        occurred_at = datetime.fromisoformat(body["updated_at"])
        transaction = LedgerTransaction(
            id=UUID(body["transaction_id"]),
            organization_id=command.organization_id,
            actor_id=command.actor_id,
            reason_code=command.reason_code,
            business_reference=command.business_reference,
            idempotency_key=command.idempotency_key,
            occurred_at=occurred_at,
            lines=lines,
        )
        position = InventoryPosition(
            key=command.stock_key,
            on_hand=Decimal(body["on_hand"]),
            reserved=Decimal(body["reserved"]),
            average_unit_cost=Decimal(body.get("average_unit_cost", "0")),
            inventory_value=Decimal(body.get("inventory_value", "0")),
            version=int(body["version"]),
            updated_at=occurred_at,
        )
        return AdjustmentResult(transaction, position, replayed)
