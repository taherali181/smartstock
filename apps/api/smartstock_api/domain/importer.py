from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid5

from .errors import DuplicateResource, InvalidQuantity


@dataclass(frozen=True, slots=True)
class ImportMapping:
    resource_type: str
    legacy_id: str
    smartstock_id: UUID


@dataclass(frozen=True, slots=True)
class ImportPosition:
    product_id: UUID
    warehouse_id: UUID
    location_id: UUID
    quantity: Decimal
    uom: str


@dataclass(frozen=True, slots=True)
class RestockImportPlan:
    organization_id: UUID
    source_hash: str
    mappings: tuple[ImportMapping, ...]
    positions: tuple[ImportPosition, ...]
    source_quantity: Decimal

    @property
    def imported_quantity(self) -> Decimal:
        return sum((position.quantity for position in self.positions), Decimal("0"))

    @property
    def reconciled(self) -> bool:
        return self.source_quantity == self.imported_quantity


class RestockDemoImporter:
    """Pure one-shot planner for demo Restock exports.

    It never mutates the legacy SQLite database. Identifiers are stable for the
    organization and source IDs, so rerunning the same snapshot produces the
    same mapping and source hash.
    """

    @staticmethod
    def plan(organization_id: UUID, snapshot: dict[str, Any]) -> RestockImportPlan:
        canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
        source_hash = sha256(canonical.encode()).hexdigest()
        products = RestockDemoImporter._indexed(snapshot.get("products", []), "product")
        warehouses = RestockDemoImporter._indexed(snapshot.get("warehouses", []), "warehouse")
        bins = RestockDemoImporter._indexed(snapshot.get("bins", []), "bin")
        mappings = tuple(
            ImportMapping(resource_type, legacy_id, uuid5(organization_id, f"{resource_type}:{legacy_id}"))
            for resource_type, records in (
                ("product", products), ("warehouse", warehouses), ("bin", bins)
            )
            for legacy_id in sorted(records)
        )
        lookup = {(item.resource_type, item.legacy_id): item.smartstock_id for item in mappings}
        positions: list[ImportPosition] = []
        source_quantity = Decimal("0")
        for row in snapshot.get("inventory", []):
            quantity = Decimal(str(row["quantity"]))
            if quantity < 0:
                raise InvalidQuantity("demo import cannot introduce negative stock")
            product_key = str(row["product_id"])
            warehouse_key = str(row["warehouse_id"])
            bin_key = str(row["bin_id"])
            try:
                position = ImportPosition(
                    lookup[("product", product_key)],
                    lookup[("warehouse", warehouse_key)],
                    lookup[("bin", bin_key)],
                    quantity,
                    str(row.get("uom") or products[product_key].get("base_uom") or "ea"),
                )
            except KeyError as exc:
                raise InvalidQuantity(f"inventory row references unknown legacy id: {exc}") from exc
            positions.append(position)
            source_quantity += quantity
        plan = RestockImportPlan(
            organization_id,
            source_hash,
            mappings,
            tuple(positions),
            source_quantity,
        )
        if not plan.reconciled:
            raise InvalidQuantity("demo import quantity reconciliation failed")
        return plan

    @staticmethod
    def _indexed(rows: list[dict[str, Any]], resource_type: str) -> dict[str, dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for row in rows:
            legacy_id = str(row["id"])
            if legacy_id in indexed:
                raise DuplicateResource(f"duplicate legacy {resource_type} id: {legacy_id}")
            indexed[legacy_id] = row
        return indexed
