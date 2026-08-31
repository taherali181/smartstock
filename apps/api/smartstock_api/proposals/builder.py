"""Turn a requested write into an inert, version-bound proposal.

Nothing here mutates anything. It resolves the entities a request names, prices
the line from records, states the impact in plain language, and records the exact
versions the proposal was built from so approval can detect staleness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from smartstock_api.conversations.blocks import decimal_text
from smartstock_api.conversations.reads import OperationalReads
from smartstock_api.domain.proposals import ActionProposal, ProposalState
from smartstock_api.proposals.store import StoredProposal

PROPOSAL_TTL = timedelta(minutes=30)

# "raise a PO for 200 of SKU-1017 from Acme", "order 50 SKU-1002",
# "create a purchase order for 12 SKU-1003 from Northwind"
_PURCHASE_INTENT = re.compile(
    r"\b(?:raise|create|issue|place|draft|make)?\s*"
    r"(?:a\s+)?(?:new\s+)?(?:purchase\s+order|\bpo\b|order|buy|reorder|restock)\b"
    r"[^0-9]*?(?P<quantity>\d+(?:\.\d+)?)\s*"
    r"(?:units?\s+)?(?:of\s+)?(?P<sku>[A-Za-z]{2,6}-\d{2,6})"
    r"(?:.*?\bfrom\s+(?P<supplier>[A-Za-z0-9 &'.-]{2,40}))?",
    re.IGNORECASE,
)


class ProposalNotPossible(Exception):
    """The request was understood but cannot be turned into a valid command."""


@dataclass(frozen=True, slots=True)
class PurchaseIntent:
    quantity: Decimal
    sku: str
    supplier: str | None


def detect_purchase_intent(question: str) -> PurchaseIntent | None:
    match = _PURCHASE_INTENT.search(question)
    if not match:
        return None
    try:
        quantity = Decimal(match.group("quantity"))
    except Exception:  # noqa: BLE001
        return None
    if quantity <= 0:
        return None
    supplier = (match.group("supplier") or "").strip(" .") or None
    return PurchaseIntent(quantity=quantity, sku=match.group("sku"), supplier=supplier)


def build_purchase_proposal(
    reads: OperationalReads,
    intent: PurchaseIntent,
    *,
    organization_id: UUID,
    actor_id: UUID,
    now: datetime | None = None,
) -> StoredProposal:
    now = now or datetime.now(UTC)

    product = reads.product_by_sku(intent.sku)
    if product is None:
        raise ProposalNotPossible(f"No product matches SKU {intent.sku}.")

    suppliers = reads.suppliers()
    if not suppliers:
        raise ProposalNotPossible("This organization has no suppliers to order from.")

    supplier = None
    if intent.supplier:
        needle = intent.supplier.casefold()
        supplier = next(
            (
                candidate
                for candidate in suppliers
                if needle in candidate.name.casefold() or needle in candidate.code.casefold()
            ),
            None,
        )
        if supplier is None:
            raise ProposalNotPossible(
                f"No supplier matches {intent.supplier!r}. "
                f"Known suppliers: {', '.join(sorted(item.name for item in suppliers)[:5])}."
            )
    else:
        supplier = suppliers[0]

    warehouses = reads.warehouses()
    if not warehouses:
        raise ProposalNotPossible("This organization has no warehouse to receive into.")

    positions = reads.positions(product_id=product.id)
    warehouse = next(
        (
            item
            for item in warehouses
            if positions and item.id == positions[0].key.warehouse_id
        ),
        warehouses[0],
    )

    # Price from what the records actually say. Guessing a price would put an
    # invented number into a financial document.
    costed = [
        position.average_unit_cost for position in positions if position.average_unit_cost > 0
    ]
    if not costed:
        raise ProposalNotPossible(
            f"{product.sku} has no recorded unit cost, so a purchase price cannot be "
            "derived from records. Add a cost or raise this order manually."
        )
    unit_price = min(costed)
    currency = supplier.currency
    line_total = (unit_price * intent.quantity).quantize(Decimal("0.01"))

    order_number = f"PO-AI-{now:%Y%m%d}-{uuid4().hex[:6].upper()}"

    command_payload = {
        "command": "create_purchase_order",
        "order_number": order_number,
        "party_id": str(supplier.id),
        "warehouse_id": str(warehouse.id),
        "currency": currency,
        "lines": [
            {
                "product_id": str(product.id),
                "quantity": decimal_text(intent.quantity),
                "uom": product.base_uom,
                "unit_price": decimal_text(unit_price),
                "currency": currency,
            }
        ],
    }

    # Approval compares these against live versions and refuses if anything moved.
    source_versions = {
        f"product:{product.id}": product.version,
        f"supplier:{supplier.id}": supplier.version,
        f"warehouse:{warehouse.id}": warehouse.version,
    }

    impact = (
        f"Creates purchase order {order_number} in draft. Nothing is ordered until "
        "it is approved and sent.",
        f"{decimal_text(intent.quantity)} {product.base_uom} of {product.sku} "
        f"({product.name}).",
        f"Supplier {supplier.name} ({supplier.code}), receiving into {warehouse.code}.",
        f"{decimal_text(unit_price)} {currency} per {product.base_uom}, "
        f"{decimal_text(line_total)} {currency} total, priced from the recorded unit cost.",
        f"Incoming for {warehouse.code} rises by {decimal_text(intent.quantity)} "
        f"{product.base_uom} once the order is approved and sent. On-hand does not "
        "change until receipt.",
    )

    proposal = ActionProposal(
        id=uuid4(),
        organization_id=organization_id,
        created_by=actor_id,
        state=ProposalState.AWAITING_REVIEW,
        source_versions=source_versions,
        command_payload=command_payload,
        expires_at=now + PROPOSAL_TTL,
    )

    return StoredProposal(
        proposal=proposal,
        command="create_purchase_order",
        title=f"Purchase {decimal_text(intent.quantity)} {product.sku} from {supplier.name}",
        impact=impact,
        created_at=now,
    )


def current_source_versions(
    reads: OperationalReads, source_versions: dict[str, int]
) -> dict[str, int]:
    """Re-read every version the proposal was built from, as it stands now."""
    products = {str(item.id): item for item in reads.products()}
    suppliers = {str(item.id): item for item in reads.suppliers()}
    warehouses = {str(item.id): item for item in reads.warehouses()}
    lookup = {"product": products, "supplier": suppliers, "warehouse": warehouses}

    current: dict[str, int] = {}
    for key in source_versions:
        kind, _, identifier = key.partition(":")
        record = lookup.get(kind, {}).get(identifier)
        if record is not None:
            current[key] = record.version
    return current
