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
)
from smartstock_api.domain.inventory import (
    AdjustmentCommand,
    AdjustmentResult,
    InventoryAccount,
    InventoryPosition,
    LedgerLine,
    LedgerTransaction,
    StockCondition,
    StockKey,
    assert_balanced,
)
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
                           lot_key, serial_key, uom, on_hand, reserved, version, updated_at
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
                           lot_key, serial_key, uom, on_hand, reserved, version, updated_at
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
            if not command.allow_negative and next_on_hand < reserved:
                raise InsufficientStock(
                    "adjustment would reduce sellable stock below reservations"
                )

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
                      quantity, uom
                    ) VALUES (
                      :organization_id, :transaction_id, 1, 'on_hand', :product_id,
                      :warehouse_id, :location_id, :condition, :ownership,
                      NULLIF(:lot_key, '00000000-0000-0000-0000-000000000000'::uuid),
                      NULLIF(:serial_key, '00000000-0000-0000-0000-000000000000'::uuid),
                      :quantity, :uom
                    ), (
                      :organization_id, :transaction_id, 2, 'external', NULL,
                      NULL, NULL, NULL, NULL, NULL, NULL, -:quantity, NULL
                    )
                    """
                ),
                line_params,
            )
            next_version = row["version"] + 1
            session.execute(
                text(
                    """
                    UPDATE inventory_positions
                    SET on_hand = :on_hand, version = :version, updated_at = :updated_at
                    WHERE organization_id = :organization_id AND id = :id
                    """
                ),
                {
                    "organization_id": command.organization_id,
                    "id": row["id"],
                    "on_hand": next_on_hand,
                    "version": next_version,
                    "updated_at": now,
                },
            )
            response_body = {
                "transaction_id": str(transaction_id),
                "on_hand": str(next_on_hand),
                "reserved": str(reserved),
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
            version=int(body["version"]),
            updated_at=occurred_at,
        )
        return AdjustmentResult(transaction, position, replayed)
