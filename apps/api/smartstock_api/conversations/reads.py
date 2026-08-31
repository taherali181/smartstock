"""Permission-checked operational reads for the conversation tool layer.

This is the seam described in PARALLEL_PLAN.md section 4, Seam 1.

When the core lane supplies an ``OperationalReadPort`` implementation, reads go
through it and filtering pushes down to the query. Until then the same calls are
served by the concrete stores on ``app.state`` and filtered in memory. Tools
above this module are written against one interface either way, so the arrival
of a port implementation changes performance, not behaviour.

Store results are cached for the lifetime of one request, because a question
answered by several tools should not re-query the same rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from smartstock_api.domain.catalog import Customer, Product, Supplier, Warehouse
from smartstock_api.domain.inventory import InventoryPosition, StockCondition
from smartstock_api.domain.operations import OperationalOrder, OrderKind, WarehouseTask
from smartstock_api.domain.ports import OperationalReadPort


class ReadUnavailable(RuntimeError):
    """A backing store could not answer. Surfaced to the user as a warning."""

    def __init__(self, source: str, cause: BaseException) -> None:
        super().__init__(f"{source} is unavailable: {cause}")
        self.source = source


@dataclass(slots=True)
class OperationalReads:
    catalog: Any
    inventory: Any
    operations: Any
    organization_id: UUID
    actor_id: UUID
    port: OperationalReadPort | None = None
    _cache: dict[str, Any] = field(default_factory=dict)

    def _cached(self, key: str, source: str, loader: Any) -> Any:
        if key not in self._cache:
            try:
                self._cache[key] = loader()
            except Exception as exc:  # noqa: BLE001 - degraded, not fatal
                raise ReadUnavailable(source, exc) from exc
        return self._cache[key]

    def _via_port(self, source: str, call: Any) -> Any:
        try:
            return call()
        except Exception as exc:  # noqa: BLE001
            raise ReadUnavailable(source, exc) from exc

    # -- inventory ---------------------------------------------------------

    def positions(
        self,
        *,
        product_id: UUID | None = None,
        warehouse_id: UUID | None = None,
        condition: StockCondition | None = None,
    ) -> list[InventoryPosition]:
        if self.port is not None:
            return list(
                self._via_port(
                    "inventory",
                    lambda: self.port.inventory_positions(  # type: ignore[union-attr]
                        self.organization_id,
                        self.actor_id,
                        product_id=product_id,
                        warehouse_id=warehouse_id,
                        condition=condition,
                    ),
                )
            )
        rows: list[InventoryPosition] = self._cached(
            "positions",
            "inventory",
            lambda: self.inventory.positions_for(self.organization_id, self.actor_id),
        )
        return [
            position
            for position in rows
            if (product_id is None or position.key.product_id == product_id)
            and (warehouse_id is None or position.key.warehouse_id == warehouse_id)
            and (condition is None or position.key.condition == condition)
        ]

    # -- catalog -----------------------------------------------------------

    def products(self, *, query: str | None = None) -> list[Product]:
        if self.port is not None:
            return list(
                self._via_port(
                    "catalog",
                    lambda: self.port.product_lookup(  # type: ignore[union-attr]
                        self.organization_id, self.actor_id, query=query
                    ),
                )
            )
        rows: list[Product] = self._cached(
            "products",
            "catalog",
            lambda: self.catalog.products_for(self.organization_id, self.actor_id),
        )
        if not query:
            return rows
        needle = query.casefold()
        return [
            product
            for product in rows
            if needle in product.sku.casefold() or needle in product.name.casefold()
        ]

    def warehouses(self) -> list[Warehouse]:
        return self._cached(
            "warehouses",
            "catalog",
            lambda: self.catalog.warehouses_for(self.organization_id, self.actor_id),
        )

    def suppliers(self, *, query: str | None = None) -> list[Supplier]:
        if self.port is not None:
            return list(
                self._via_port(
                    "catalog",
                    lambda: self.port.supplier_lookup(  # type: ignore[union-attr]
                        self.organization_id, self.actor_id, query=query
                    ),
                )
            )
        return self._cached(
            "suppliers",
            "catalog",
            lambda: self.catalog.suppliers_for(self.organization_id, self.actor_id),
        )

    def customers(self) -> list[Customer]:
        return self._cached(
            "customers",
            "catalog",
            lambda: self.catalog.customers_for(self.organization_id, self.actor_id),
        )

    # -- operations --------------------------------------------------------

    def orders(self, kind: OrderKind) -> list[OperationalOrder]:
        return self._cached(
            f"orders:{kind.value}",
            "operations",
            lambda: self.operations.orders_for(self.organization_id, self.actor_id, kind),
        )

    def order_by_number(self, kind: OrderKind, order_number: str) -> OperationalOrder | None:
        """Single-order lookup. The port answers this directly when present."""
        if self.port is not None:
            lookup = (
                self.port.purchase_order_status
                if kind == OrderKind.PURCHASE
                else self.port.sales_order_status
            )
            try:
                return lookup(
                    self.organization_id, self.actor_id, order_number=order_number
                )
            except Exception:  # noqa: BLE001 - a miss is not an outage
                return None
        target = order_number.casefold()
        return next(
            (order for order in self.orders(kind) if order.order_number.casefold() == target),
            None,
        )

    def tasks(self) -> list[WarehouseTask]:
        return self._cached(
            "tasks",
            "warehouse tasks",
            lambda: self.operations.tasks_for(self.organization_id, self.actor_id),
        )

    # -- resolution helpers -------------------------------------------------

    def product_by_sku(self, sku: str) -> Product | None:
        target = sku.strip().casefold()
        return next(
            (product for product in self.products(query=sku)
             if product.sku.casefold() == target),
            None,
        )

    def warehouse_by_code(self, code: str) -> Warehouse | None:
        target = code.strip().casefold()
        return next(
            (warehouse for warehouse in self.warehouses()
             if warehouse.code.casefold() == target),
            None,
        )

    def product_names(self) -> dict[UUID, Product]:
        return {product.id: product for product in self.products()}

    def warehouse_names(self) -> dict[UUID, Warehouse]:
        return {warehouse.id: warehouse for warehouse in self.warehouses()}
