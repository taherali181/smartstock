"""Typed operational tools.

Operational facts come from these tools and nowhere else. A model may choose
which tool to call and with what arguments; it never supplies the numbers. Each
tool returns exact record values plus the citations that authorise them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

from smartstock_api.conversations.blocks import Citation, decimal_text
from smartstock_api.conversations.reads import OperationalReads
from smartstock_api.domain.operations import OrderKind

TOOL_VERSION = "1.0.0"


@dataclass(slots=True)
class ToolResult:
    tool: str
    title: str
    rows: list[dict[str, Any]]
    citations: list[Citation] = field(default_factory=list)
    summary: str = ""
    empty_reason: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.rows


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[OperationalReads, dict[str, Any]], ToolResult]
    permission: str = "inventory.view"
    version: str = TOOL_VERSION

    def schema(self) -> dict[str, Any]:
        """OpenAI/Ollama-compatible function schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


def _qty(value: Decimal | None) -> Decimal:
    return value if value is not None else Decimal("0")


# --- inventory ------------------------------------------------------------


def _inventory_positions(reads: OperationalReads, args: dict[str, Any]) -> ToolResult:
    sku = (args.get("sku") or "").strip()
    warehouse_code = (args.get("warehouse") or "").strip()

    products = reads.product_names()
    warehouses = reads.warehouse_names()

    product_filter = None
    if sku:
        product = reads.product_by_sku(sku)
        if product is None:
            return ToolResult(
                "inventory_positions",
                f"Inventory for {sku}",
                [],
                empty_reason=f"No product matches SKU {sku!r} in this organization.",
            )
        product_filter = product.id

    warehouse_filter = None
    if warehouse_code:
        warehouse = reads.warehouse_by_code(warehouse_code)
        if warehouse is None:
            return ToolResult(
                "inventory_positions",
                f"Inventory in {warehouse_code}",
                [],
                empty_reason=f"No warehouse matches code {warehouse_code!r}.",
            )
        warehouse_filter = warehouse.id

    rows: list[dict[str, Any]] = []
    citations: list[Citation] = []
    total_on_hand = Decimal("0")
    total_available = Decimal("0")

    for position in reads.positions(
        product_id=product_filter, warehouse_id=warehouse_filter
    ):
        key = position.key
        product = products.get(key.product_id)
        warehouse = warehouses.get(key.warehouse_id)
        rows.append(
            {
                "sku": product.sku if product else str(key.product_id),
                "product": product.name if product else "unknown",
                "warehouse": warehouse.code if warehouse else str(key.warehouse_id),
                "condition": key.condition,
                "uom": key.uom,
                "on_hand": position.on_hand,
                "reserved": position.reserved,
                "available": position.available,
                "unit_cost": position.average_unit_cost,
            }
        )
        total_on_hand += position.on_hand
        total_available += position.available
        citations.append(
            Citation(
                record_type="inventory_position",
                record_id=f"{key.product_id}:{key.warehouse_id}:{key.condition}",
                label=(
                    f"{product.sku if product else key.product_id} @ "
                    f"{warehouse.code if warehouse else key.warehouse_id}"
                ),
                version=position.version,
                observed_at=position.updated_at,
            )
        )

    scope = " ".join(filter(None, [sku, f"in {warehouse_code}" if warehouse_code else ""])).strip()
    if not rows:
        return ToolResult(
            "inventory_positions",
            f"Inventory {scope}".strip(),
            [],
            empty_reason=(
                f"No inventory positions exist for {scope}." if scope
                else "No inventory positions exist yet."
            ),
        )
    summary = (
        f"{decimal_text(total_on_hand)} on hand and "
        f"{decimal_text(total_available)} available across {len(rows)} position(s)"
    )
    if scope:
        summary = f"{scope}: {summary}"
    return ToolResult(
        "inventory_positions", f"Inventory {scope}".strip(), rows, citations, summary
    )


def _low_stock(reads: OperationalReads, args: dict[str, Any]) -> ToolResult:
    raw = args.get("threshold", 10)
    try:
        threshold = Decimal(str(raw))
    except Exception:  # noqa: BLE001
        threshold = Decimal("10")

    products = reads.product_names()
    warehouses = reads.warehouse_names()
    rows: list[dict[str, Any]] = []
    citations: list[Citation] = []

    for position in reads.positions():
        if position.available > threshold:
            continue
        key = position.key
        product = products.get(key.product_id)
        warehouse = warehouses.get(key.warehouse_id)
        rows.append(
            {
                "sku": product.sku if product else str(key.product_id),
                "product": product.name if product else "unknown",
                "warehouse": warehouse.code if warehouse else str(key.warehouse_id),
                "available": position.available,
                "on_hand": position.on_hand,
                "reserved": position.reserved,
            }
        )
        citations.append(
            Citation(
                record_type="inventory_position",
                record_id=f"{key.product_id}:{key.warehouse_id}:{key.condition}",
                label=f"{product.sku if product else key.product_id} low stock",
                version=position.version,
                observed_at=position.updated_at,
            )
        )

    rows.sort(key=lambda row: row["available"])
    if not rows:
        return ToolResult(
            "low_stock",
            "Low stock",
            [],
            empty_reason=f"No sellable position is at or below {decimal_text(threshold)} available.",
        )
    return ToolResult(
        "low_stock",
        f"Positions at or below {decimal_text(threshold)} available",
        rows,
        citations,
        # Stated as a threshold, not a reorder point: reorder policy lands with
        # /v1/reports/reorder-suggestions in the core lane.
        f"{len(rows)} position(s) at or below a {decimal_text(threshold)} unit threshold",
    )


# --- catalog --------------------------------------------------------------


def _product_search(reads: OperationalReads, args: dict[str, Any]) -> ToolResult:
    query = (args.get("query") or "").strip().casefold()
    matches = list(reads.products(query=query or None))
    rows = [
        {
            "sku": product.sku,
            "name": product.name,
            "uom": product.base_uom,
            "tracking": product.tracking_mode,
            "state": product.lifecycle_state,
        }
        for product in matches[:50]
    ]
    citations = [
        Citation("product", str(product.id), product.sku, product.version, product.updated_at)
        for product in matches[:50]
    ]
    if not rows:
        return ToolResult(
            "product_search",
            "Products",
            [],
            empty_reason=f"No product matches {query!r}." if query else "No products exist yet.",
        )
    return ToolResult(
        "product_search",
        f"Products matching {query!r}" if query else "Products",
        rows,
        citations,
        f"{len(matches)} product(s) matched",
    )


# --- orders ---------------------------------------------------------------


def _orders(reads: OperationalReads, args: dict[str, Any], kind: OrderKind) -> ToolResult:
    state = (args.get("state") or "").strip().casefold()
    number = (args.get("order_number") or "").strip().casefold()
    label = "Purchase orders" if kind == OrderKind.PURCHASE else "Sales orders"

    if number:
        found = reads.order_by_number(kind, number)
        orders = [found] if found else []
    else:
        orders = reads.orders(kind)
    if state:
        orders = [order for order in orders if order.state.casefold() == state]

    warehouses = reads.warehouse_names()
    rows = [
        {
            "order_number": order.order_number,
            "state": order.state,
            "warehouse": (
                warehouses[order.warehouse_id].code
                if order.warehouse_id in warehouses
                else str(order.warehouse_id)
            ),
            "lines": len(order.lines),
            "total": order.total,
            "currency": order.currency,
            "expected_on": order.expected_on,
        }
        for order in orders[:50]
    ]
    citations = [
        Citation(
            record_type=f"{kind.value}_order",
            record_id=str(order.id),
            label=order.order_number,
            version=order.version,
            observed_at=order.updated_at,
        )
        for order in orders[:50]
    ]
    if not rows:
        detail = f" in state {state!r}" if state else ""
        detail += f" numbered {number!r}" if number else ""
        return ToolResult(
            f"{kind.value}_orders", label, [], empty_reason=f"No {label.lower()}{detail}."
        )
    return ToolResult(
        f"{kind.value}_orders", label, rows, citations, f"{len(orders)} {label.lower()}"
    )


def _purchase_orders(reads: OperationalReads, args: dict[str, Any]) -> ToolResult:
    return _orders(reads, args, OrderKind.PURCHASE)


def _sales_orders(reads: OperationalReads, args: dict[str, Any]) -> ToolResult:
    return _orders(reads, args, OrderKind.SALES)


# --- warehouse ------------------------------------------------------------


def _warehouse_tasks(reads: OperationalReads, args: dict[str, Any]) -> ToolResult:
    state = (args.get("state") or "").strip().casefold()
    task_type = (args.get("task_type") or "").strip().casefold()

    tasks = reads.tasks()
    if state:
        tasks = [task for task in tasks if task.state.casefold() == state]
    if task_type:
        tasks = [task for task in tasks if task.task_type.casefold() == task_type]

    products = reads.product_names()
    warehouses = reads.warehouse_names()
    rows = [
        {
            "task": task.task_number,
            "type": task.task_type,
            "state": task.state,
            "warehouse": (
                warehouses[task.warehouse_id].code
                if task.warehouse_id in warehouses
                else str(task.warehouse_id)
            ),
            "sku": (
                products[task.product_id].sku
                if task.product_id and task.product_id in products
                else None
            ),
            "quantity": _qty(task.quantity),
        }
        for task in tasks[:50]
    ]
    citations = [
        Citation("warehouse_task", str(task.id), task.task_number, task.version)
        for task in tasks[:50]
    ]
    if not rows:
        return ToolResult(
            "warehouse_tasks", "Warehouse tasks", [], empty_reason="No warehouse tasks match."
        )
    return ToolResult(
        "warehouse_tasks", "Warehouse tasks", rows, citations, f"{len(tasks)} open task(s)"
    )


REGISTRY: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            "inventory_positions",
            "Current on-hand, reserved and available inventory. Filter by SKU and/or "
            "warehouse code. Use for any 'how much / how many / do we have' question.",
            _object(
                {
                    "sku": {"type": "string", "description": "Product SKU, e.g. SKU-1017"},
                    "warehouse": {"type": "string", "description": "Warehouse code, e.g. WH-MAIN"},
                }
            ),
            _inventory_positions,
            permission="inventory.view",
        ),
        Tool(
            "low_stock",
            "Positions at or below an availability threshold. Use for 'what is running "
            "low', 'what should I reorder', 'what is below reorder point'.",
            _object({"threshold": {"type": "number", "description": "Availability cutoff"}}),
            _low_stock,
            permission="inventory.view",
        ),
        Tool(
            "product_search",
            "Find products by SKU fragment or name. Use to identify a product before "
            "looking up its stock.",
            _object({"query": {"type": "string"}}, ["query"]),
            _product_search,
            permission="catalog.view",
        ),
        Tool(
            "purchase_orders",
            "Purchase orders with state, supplier, totals and expected dates. Use for "
            "questions about buying, receiving or incoming stock.",
            _object(
                {
                    "state": {"type": "string", "description": "e.g. draft, approved, received"},
                    "order_number": {"type": "string"},
                }
            ),
            _purchase_orders,
            permission="purchasing.view",
        ),
        Tool(
            "sales_orders",
            "Sales orders with state, totals and fulfilment progress. Use for questions "
            "about customer demand, allocation or shipping.",
            _object(
                {
                    "state": {"type": "string", "description": "e.g. confirmed, allocated"},
                    "order_number": {"type": "string"},
                }
            ),
            _sales_orders,
            permission="orders.view",
        ),
        Tool(
            "warehouse_tasks",
            "The warehouse work queue: receive, putaway, pick, pack, count and transfer "
            "tasks with their state.",
            _object({"state": {"type": "string"}, "task_type": {"type": "string"}}),
            _warehouse_tasks,
            permission="inventory.view",
        ),
    )
}


def allowed_tools(permissions: frozenset[str]) -> dict[str, Tool]:
    """The tools this principal may use. A tool it cannot use is never offered
    to the model, so it cannot be selected, described, or invoked."""
    if "*" in permissions:
        return dict(REGISTRY)
    return {
        name: tool for name, tool in REGISTRY.items() if tool.permission in permissions
    }


def schemas(tools: dict[str, Tool] | None = None) -> list[dict[str, Any]]:
    return [tool.schema() for tool in (tools or REGISTRY).values()]


def versions(tools: dict[str, Tool] | None = None) -> dict[str, str]:
    return {name: tool.version for name, tool in (tools or REGISTRY).items()}
