"""Permission-checked operational reads for the conversation tool layer.

This is the seam described in PARALLEL_PLAN.md section 4, Seam 1. It currently
adapts the concrete stores held on ``app.state``; when the core lane publishes
``domain/ports.py`` the annotations here bind to those Protocols and nothing
above this module changes.

Reads are cached for the lifetime of one request so that a question answered by
several tools does not re-query the database for the same rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from smartstock_api.domain.catalog import Customer, Product, Supplier, Warehouse
from smartstock_api.domain.inventory import InventoryPosition
from smartstock_api.domain.operations import OperationalOrder, OrderKind, WarehouseTask


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
    _cache: dict[str, Any] = field(default_factory=dict)

    def _cached(self, key: str, source: str, loader: Any) -> Any:
        if key not in self._cache:
            try:
                self._cache[key] = loader()
            except Exception as exc:  # noqa: BLE001 - degraded, not fatal
                raise ReadUnavailable(source, exc) from exc
        return self._cache[key]

    def products(self) -> list[Product]:
        return self._cached(
            "products",
            "catalog",
            lambda: self.catalog.products_for(self.organization_id, self.actor_id),
        )

    def warehouses(self) -> list[Warehouse]:
        return self._cached(
            "warehouses",
            "catalog",
            lambda: self.catalog.warehouses_for(self.organization_id, self.actor_id),
        )

    def suppliers(self) -> list[Supplier]:
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

    def positions(self) -> list[InventoryPosition]:
        return self._cached(
            "positions",
            "inventory",
            lambda: self.inventory.positions_for(self.organization_id, self.actor_id),
        )

    def orders(self, kind: OrderKind) -> list[OperationalOrder]:
        return self._cached(
            f"orders:{kind.value}",
            "operations",
            lambda: self.operations.orders_for(self.organization_id, self.actor_id, kind),
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
        for product in self.products():
            if product.sku.casefold() == target:
                return product
        return None

    def warehouse_by_code(self, code: str) -> Warehouse | None:
        target = code.strip().casefold()
        for warehouse in self.warehouses():
            if warehouse.code.casefold() == target:
                return warehouse
        return None

    def product_names(self) -> dict[UUID, Product]:
        return {product.id: product for product in self.products()}

    def warehouse_names(self) -> dict[UUID, Warehouse]:
        return {warehouse.id: warehouse for warehouse in self.warehouses()}
