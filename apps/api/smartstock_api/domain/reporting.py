from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from .catalog import Product
from .inventory import InventoryPosition, StockCondition
from .operations import OperationalOrder, Receipt

ZERO = Decimal("0")
INCOMING_ORDER_STATES = frozenset(
    {"approved", "sent", "acknowledged", "partially_received"}
)


@dataclass(frozen=True, slots=True)
class StockSummary:
    product_id: UUID
    sku: str
    product_name: str
    warehouse_id: UUID
    condition: StockCondition
    uom: str
    on_hand: Decimal
    reserved: Decimal
    available: Decimal
    incoming: Decimal
    inventory_value: Decimal
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ReorderSuggestion:
    product_id: UUID
    sku: str
    product_name: str
    uom: str
    available: Decimal
    incoming: Decimal
    reorder_point: Decimal
    safety_stock: Decimal
    target_stock: Decimal
    suggested_quantity: Decimal
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ReceiptSummary:
    receipt_id: UUID
    receipt_number: str
    purchase_order_id: UUID
    warehouse_id: UUID
    accepted_quantity: Decimal
    rejected_quantity: Decimal
    posted_at: datetime


def incoming_by_product_and_warehouse(
    purchase_orders: list[OperationalOrder],
) -> dict[tuple[UUID, UUID], Decimal]:
    incoming: dict[tuple[UUID, UUID], Decimal] = {}
    for order in purchase_orders:
        if order.state not in INCOMING_ORDER_STATES:
            continue
        for line in order.lines:
            key = (line.product_id, order.warehouse_id)
            incoming[key] = incoming.get(key, ZERO) + line.open_quantity
    return incoming


def stock_summaries(
    products: list[Product],
    positions: list[InventoryPosition],
    purchase_orders: list[OperationalOrder],
) -> list[StockSummary]:
    product_by_id = {product.id: product for product in products}
    incoming = incoming_by_product_and_warehouse(purchase_orders)
    grouped: dict[
        tuple[UUID, UUID, StockCondition, str],
        tuple[Decimal, Decimal, Decimal, datetime],
    ] = {}
    for position in positions:
        key = (
            position.key.product_id,
            position.key.warehouse_id,
            position.key.condition,
            position.key.uom,
        )
        current = grouped.get(key)
        if current is None:
            grouped[key] = (
                position.on_hand,
                position.reserved,
                position.inventory_value,
                position.updated_at,
            )
        else:
            grouped[key] = (
                current[0] + position.on_hand,
                current[1] + position.reserved,
                current[2] + position.inventory_value,
                max(current[3], position.updated_at),
            )

    summaries: list[StockSummary] = []
    for key, values in grouped.items():
        product_id, warehouse_id, condition, uom = key
        product = product_by_id[product_id]
        on_hand, reserved, inventory_value, updated_at = values
        available = on_hand - reserved if condition == StockCondition.SELLABLE else ZERO
        summaries.append(
            StockSummary(
                product_id,
                product.sku,
                product.name,
                warehouse_id,
                condition,
                uom,
                on_hand,
                reserved,
                available,
                incoming.get((product_id, warehouse_id), ZERO),
                inventory_value,
                updated_at,
            )
        )
    return sorted(
        summaries,
        key=lambda item: (item.sku, str(item.warehouse_id), item.condition.value),
    )


def reorder_suggestions(
    products: list[Product],
    positions: list[InventoryPosition],
    purchase_orders: list[OperationalOrder],
) -> list[ReorderSuggestion]:
    incoming_by_location = incoming_by_product_and_warehouse(purchase_orders)
    available: dict[UUID, Decimal] = {}
    incoming: dict[UUID, Decimal] = {}
    freshness: dict[UUID, datetime] = {}
    for position in positions:
        if position.key.condition != StockCondition.SELLABLE:
            continue
        product_id = position.key.product_id
        available[product_id] = available.get(product_id, ZERO) + position.available
        freshness[product_id] = max(
            freshness.get(product_id, position.updated_at), position.updated_at
        )
    for (product_id, _), quantity in incoming_by_location.items():
        incoming[product_id] = incoming.get(product_id, ZERO) + quantity

    suggestions: list[ReorderSuggestion] = []
    for product in products:
        reorder_point = Decimal(str(product.custom_fields.get("reorder_point", "0")))
        if reorder_point <= ZERO or available.get(product.id, ZERO) >= reorder_point:
            continue
        safety_stock = Decimal(str(product.custom_fields.get("safety_stock", "0")))
        target_stock = Decimal(
            str(product.custom_fields.get("target_stock", reorder_point + safety_stock))
        )
        current_available = available.get(product.id, ZERO)
        current_incoming = incoming.get(product.id, ZERO)
        suggestions.append(
            ReorderSuggestion(
                product.id,
                product.sku,
                product.name,
                product.base_uom,
                current_available,
                current_incoming,
                reorder_point,
                safety_stock,
                target_stock,
                max(target_stock - current_available - current_incoming, ZERO),
                freshness.get(product.id, product.updated_at),
            )
        )
    return sorted(suggestions, key=lambda item: (item.available / item.reorder_point, item.sku))


def receipt_summaries(receipts: list[Receipt]) -> list[ReceiptSummary]:
    return [
        ReceiptSummary(
            receipt.id,
            receipt.receipt_number,
            receipt.purchase_order_id,
            receipt.warehouse_id,
            sum((line.accepted_quantity for line in receipt.lines), ZERO),
            sum((line.rejected_quantity for line in receipt.lines), ZERO),
            receipt.posted_at,
        )
        for receipt in receipts
    ]
