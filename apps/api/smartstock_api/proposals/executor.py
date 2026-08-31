"""Execute an approved proposal through the ordinary domain command.

The executor constructs exactly the aggregate the manual endpoint constructs and
calls the same store method with the same idempotency discipline. There is no
privileged path for AI-originated writes: if a person could not perform this
command, neither can an approved proposal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from smartstock_api.domain.operations import OperationalOrder, OrderKind, OrderLine
from smartstock_api.proposals.store import StoredProposal


def execute_purchase_proposal(
    stored: StoredProposal,
    *,
    organization_id: UUID,
    actor_id: UUID,
    operations_store: Any,
    correlation_id: UUID,
) -> dict[str, Any]:
    payload = stored.proposal.command_payload
    if payload.get("command") != "create_purchase_order":
        raise ValueError(f"unsupported proposal command: {payload.get('command')!r}")

    # The proposal id is the idempotency key, so approving twice cannot create
    # two purchase orders.
    idempotency_key = f"proposal:{stored.id}"
    order_id = uuid5(organization_id, f"purchase-order:{idempotency_key}")
    now = datetime.now(UTC)
    currency = str(payload["currency"])

    order = OperationalOrder(
        id=order_id,
        organization_id=organization_id,
        kind=OrderKind.PURCHASE,
        order_number=str(payload["order_number"]),
        party_id=UUID(str(payload["party_id"])),
        warehouse_id=UUID(str(payload["warehouse_id"])),
        state="draft",
        lines=tuple(
            OrderLine(
                id=uuid5(order_id, f"line:{index}"),
                product_id=UUID(str(line["product_id"])),
                quantity=Decimal(str(line["quantity"])),
                uom=str(line["uom"]),
                unit_price=Decimal(str(line["unit_price"])),
                currency=str(line["currency"]),
            )
            for index, line in enumerate(payload["lines"], start=1)
        ),
        currency=currency,
        created_at=now,
        updated_at=now,
    )

    created, replayed = operations_store.create_order(
        order, actor_id, correlation_id, idempotency_key
    )
    return {
        "order_id": str(created.id),
        "order_number": created.order_number,
        "state": created.state,
        "version": created.version,
        "replayed": replayed,
    }
