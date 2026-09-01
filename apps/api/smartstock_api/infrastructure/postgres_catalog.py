from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from smartstock_api.domain.catalog import (
    BinLocation,
    Customer,
    KitComponent,
    LifecycleState,
    Lot,
    Product,
    ProductSupplier,
    ProductVariant,
    SerialNumber,
    Supplier,
    TrackingMode,
    UomConversion,
    Warehouse,
)
from smartstock_api.domain.errors import (
    DuplicateResource,
    IdempotencyConflict,
    ResourceNotFound,
)
from smartstock_api.infrastructure.database import TenantSessionFactory


class PostgresCatalogStore:
    def __init__(self, sessions: TenantSessionFactory) -> None:
        self._sessions = sessions

    def create_product(self, product: Product, actor_id: UUID, correlation_id: UUID) -> Product:
        try:
            with self._sessions.session(product.organization_id, actor_id) as session:
                inserted = session.execute(
                    text(
                        """
                        INSERT INTO products (
                          organization_id, id, sku, name, base_uom, tracking_mode,
                          lifecycle_state, description, custom_fields, version, created_at, updated_at
                        ) VALUES (
                          :organization_id, :id, :sku, :name, :base_uom, :tracking_mode,
                          :lifecycle_state, :description, CAST(:custom_fields AS jsonb),
                          :version, :created_at, :updated_at
                        ) ON CONFLICT (organization_id, id) DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "organization_id": product.organization_id,
                        "id": product.id,
                        "sku": product.sku,
                        "name": product.name,
                        "base_uom": product.base_uom,
                        "tracking_mode": product.tracking_mode.value,
                        "lifecycle_state": product.lifecycle_state.value,
                        "description": product.description,
                        "custom_fields": json.dumps(product.custom_fields),
                        "version": product.version,
                        "created_at": product.created_at,
                        "updated_at": product.updated_at,
                    },
                ).scalar_one_or_none()
                if inserted is None:
                    existing = self._product_by_id(
                        session, product.organization_id, product.id
                    )
                    if existing is None or not self._same_product(existing, product):
                        raise DuplicateResource(
                            "idempotency key was reused with different product data"
                        )
                    return existing
                self._record(
                    session,
                    organization_id=product.organization_id,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    action="catalog.product_created",
                    resource_type="product",
                    resource_id=product.id,
                    topic="catalog.product_created",
                    payload={"product_id": str(product.id), "sku": product.sku, "version": 1},
                )
        except IntegrityError as exc:
            raise DuplicateResource("product id or SKU already exists in this organization") from exc
        return product

    def products_for(self, organization_id: UUID, actor_id: UUID) -> list[Product]:
        with self._sessions.session(organization_id, actor_id) as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, sku, name, base_uom, tracking_mode, lifecycle_state,
                           description, custom_fields, version, created_at, updated_at
                    FROM products WHERE organization_id = :organization_id
                    ORDER BY sku, id LIMIT 250
                    """
                ),
                {"organization_id": organization_id},
            ).mappings()
            return [
                Product(
                    id=UUID(str(row["id"])),
                    organization_id=organization_id,
                    sku=row["sku"],
                    name=row["name"],
                    base_uom=row["base_uom"],
                    tracking_mode=TrackingMode(row["tracking_mode"]),
                    lifecycle_state=LifecycleState(row["lifecycle_state"]),
                    description=row["description"],
                    custom_fields=dict(row["custom_fields"]),
                    version=row["version"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                for row in rows
            ]

    def create_variant(
        self, variant: ProductVariant, actor_id: UUID, correlation_id: UUID
    ) -> ProductVariant:
        try:
            with self._sessions.session(variant.organization_id, actor_id) as session:
                inserted = session.execute(
                    text(
                        """
                        INSERT INTO product_variants (
                          organization_id, id, product_id, sku, name, attributes,
                          lifecycle_state, version
                        ) VALUES (
                          :organization_id, :id, :product_id, :sku, :name,
                          CAST(:attributes AS jsonb), :lifecycle_state, :version
                        ) ON CONFLICT (organization_id, id) DO NOTHING RETURNING id
                        """
                    ),
                    {
                        "organization_id": variant.organization_id,
                        "id": variant.id,
                        "product_id": variant.product_id,
                        "sku": variant.sku,
                        "name": variant.name,
                        "attributes": json.dumps(variant.attributes),
                        "lifecycle_state": variant.lifecycle_state.value,
                        "version": variant.version,
                    },
                ).scalar_one_or_none()
                if inserted is None:
                    row = session.execute(
                        text(
                            """
                            SELECT product_id, sku, name, attributes, lifecycle_state, version
                            FROM product_variants
                            WHERE organization_id=:organization_id AND id=:id
                            """
                        ),
                        {"organization_id": variant.organization_id, "id": variant.id},
                    ).mappings().one_or_none()
                    if row is None or (
                        UUID(str(row["product_id"])) != variant.product_id
                        or row["sku"] != variant.sku
                        or row["name"] != variant.name
                        or dict(row["attributes"]) != variant.attributes
                    ):
                        raise DuplicateResource(
                            "idempotency key was reused with different variant data"
                        )
                    return ProductVariant(
                        variant.id, variant.organization_id, variant.product_id,
                        row["sku"], row["name"], dict(row["attributes"]),
                        LifecycleState(row["lifecycle_state"]), row["version"]
                    )
                self._record(
                    session,
                    organization_id=variant.organization_id,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    action="catalog.variant_created",
                    resource_type="product_variant",
                    resource_id=variant.id,
                    topic="catalog.variant_created",
                    payload={
                        "variant_id": str(variant.id),
                        "product_id": str(variant.product_id),
                        "sku": variant.sku,
                    },
                )
        except IntegrityError as exc:
            raise DuplicateResource("variant SKU already exists or product is invalid") from exc
        return variant

    def add_conversion(
        self, conversion: UomConversion, actor_id: UUID, correlation_id: UUID
    ) -> UomConversion:
        try:
            with self._sessions.session(conversion.organization_id, actor_id) as session:
                inserted = session.execute(
                    text(
                        """
                        INSERT INTO uom_conversions (
                          organization_id, id, product_id, from_uom, to_uom, factor, version
                        ) VALUES (
                          :organization_id, :id, :product_id, :from_uom, :to_uom, :factor, :version
                        ) ON CONFLICT (organization_id, id) DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "organization_id": conversion.organization_id,
                        "id": conversion.id,
                        "product_id": conversion.product_id,
                        "from_uom": conversion.from_uom,
                        "to_uom": conversion.to_uom,
                        "factor": conversion.factor,
                        "version": conversion.version,
                    },
                ).scalar_one_or_none()
                if inserted is None:
                    row = session.execute(
                        text(
                            """
                            SELECT id, product_id, from_uom, to_uom, factor, version
                            FROM uom_conversions
                            WHERE organization_id=:organization_id AND id=:id
                            """
                        ),
                        {"organization_id": conversion.organization_id, "id": conversion.id},
                    ).mappings().one_or_none()
                    if row is None or (
                        UUID(str(row["product_id"])) != conversion.product_id
                        or row["from_uom"] != conversion.from_uom
                        or row["to_uom"] != conversion.to_uom
                        or row["factor"] != conversion.factor
                    ):
                        raise DuplicateResource(
                            "idempotency key was reused with different UOM conversion data"
                        )
                    return UomConversion(
                        UUID(str(row["id"])),
                        conversion.organization_id,
                        UUID(str(row["product_id"])),
                        row["from_uom"],
                        row["to_uom"],
                        row["factor"],
                        row["version"],
                    )
                self._record(
                    session,
                    organization_id=conversion.organization_id,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    action="catalog.uom_conversion_created",
                    resource_type="uom_conversion",
                    resource_id=conversion.id,
                    topic="catalog.uom_conversion_created",
                    payload={
                        "product_id": str(conversion.product_id),
                        "from_uom": conversion.from_uom,
                        "to_uom": conversion.to_uom,
                        "factor": str(conversion.factor),
                    },
                )
        except IntegrityError as exc:
            raise DuplicateResource("UOM conversion already exists or product is invalid") from exc
        return conversion

    def create_warehouse(
        self, warehouse: Warehouse, actor_id: UUID, correlation_id: UUID
    ) -> Warehouse:
        try:
            with self._sessions.session(warehouse.organization_id, actor_id) as session:
                inserted = session.execute(
                    text(
                        """
                        INSERT INTO warehouses (
                          organization_id, id, code, name, timezone, active, version
                        ) VALUES (
                          :organization_id, :id, :code, :name, :timezone, :active, :version
                        ) ON CONFLICT (organization_id, id) DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "organization_id": warehouse.organization_id,
                        "id": warehouse.id,
                        "code": warehouse.code,
                        "name": warehouse.name,
                        "timezone": warehouse.timezone,
                        "active": warehouse.active,
                        "version": warehouse.version,
                    },
                ).scalar_one_or_none()
                if inserted is None:
                    row = session.execute(
                        text(
                            """
                            SELECT id, code, name, timezone, active, version
                            FROM warehouses WHERE organization_id=:organization_id AND id=:id
                            """
                        ),
                        {"organization_id": warehouse.organization_id, "id": warehouse.id},
                    ).mappings().one_or_none()
                    if row is None or (
                        row["code"] != warehouse.code
                        or row["name"] != warehouse.name
                        or row["timezone"] != warehouse.timezone
                    ):
                        raise DuplicateResource(
                            "idempotency key was reused with different warehouse data"
                        )
                    return Warehouse(
                        UUID(str(row["id"])),
                        warehouse.organization_id,
                        row["code"],
                        row["name"],
                        row["timezone"],
                        row["active"],
                        row["version"],
                    )
                self._record(
                    session,
                    organization_id=warehouse.organization_id,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    action="warehouse.created",
                    resource_type="warehouse",
                    resource_id=warehouse.id,
                    topic="warehouse.created",
                    payload={"warehouse_id": str(warehouse.id), "code": warehouse.code},
                )
        except IntegrityError as exc:
            raise DuplicateResource("warehouse id or code already exists") from exc
        return warehouse

    def warehouses_for(self, organization_id: UUID, actor_id: UUID) -> list[Warehouse]:
        with self._sessions.session(organization_id, actor_id) as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, code, name, timezone, active, version
                    FROM warehouses WHERE organization_id = :organization_id
                    ORDER BY code, id LIMIT 250
                    """
                ),
                {"organization_id": organization_id},
            ).mappings()
            return [
                Warehouse(
                    id=UUID(str(row["id"])),
                    organization_id=organization_id,
                    code=row["code"],
                    name=row["name"],
                    timezone=row["timezone"],
                    active=row["active"],
                    version=row["version"],
                )
                for row in rows
            ]

    def create_bin(
        self, location: BinLocation, actor_id: UUID, correlation_id: UUID
    ) -> BinLocation:
        try:
            with self._sessions.session(location.organization_id, actor_id) as session:
                inserted = session.execute(
                    text(
                        """
                        INSERT INTO locations (
                          organization_id, id, warehouse_id, code, location_type,
                          active, pick_sequence, version
                        ) VALUES (
                          :organization_id, :id, :warehouse_id, :code, :location_type,
                          :active, :pick_sequence, :version
                        ) ON CONFLICT (organization_id, id) DO NOTHING RETURNING id
                        """
                    ),
                    {
                        "organization_id": location.organization_id,
                        "id": location.id,
                        "warehouse_id": location.warehouse_id,
                        "code": location.code,
                        "location_type": location.location_type,
                        "active": location.active,
                        "pick_sequence": location.pick_sequence,
                        "version": location.version,
                    },
                ).scalar_one_or_none()
                if inserted is None:
                    row = session.execute(
                        text(
                            """
                            SELECT warehouse_id, code, location_type, active, pick_sequence, version
                            FROM locations WHERE organization_id=:organization_id AND id=:id
                            """
                        ),
                        {"organization_id": location.organization_id, "id": location.id},
                    ).mappings().one_or_none()
                    if row is None or (
                        UUID(str(row["warehouse_id"])) != location.warehouse_id
                        or row["code"] != location.code
                        or row["location_type"] != location.location_type
                        or row["pick_sequence"] != location.pick_sequence
                    ):
                        raise DuplicateResource(
                            "idempotency key was reused with different bin data"
                        )
                    return BinLocation(
                        location.id,
                        location.organization_id,
                        UUID(str(row["warehouse_id"])),
                        row["code"],
                        row["location_type"],
                        row["active"],
                        row["pick_sequence"],
                        row["version"],
                    )
                self._record(
                    session,
                    organization_id=location.organization_id,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    action="warehouse.bin_created",
                    resource_type="location",
                    resource_id=location.id,
                    topic="warehouse.bin_created",
                    payload={
                        "warehouse_id": str(location.warehouse_id),
                        "location_id": str(location.id),
                        "code": location.code,
                    },
                )
        except IntegrityError as exc:
            raise DuplicateResource("bin code already exists or warehouse is invalid") from exc
        return location

    def bins_for(
        self, organization_id: UUID, actor_id: UUID, warehouse_id: UUID
    ) -> list[BinLocation]:
        with self._sessions.session(organization_id, actor_id) as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, warehouse_id, code, location_type, active,
                           pick_sequence, version
                    FROM locations
                    WHERE organization_id=:organization_id
                      AND warehouse_id=:warehouse_id
                    ORDER BY pick_sequence, lower(code), id
                    LIMIT 250
                    """
                ),
                {
                    "organization_id": organization_id,
                    "warehouse_id": warehouse_id,
                },
            ).mappings()
            return [
                BinLocation(
                    id=UUID(str(row["id"])),
                    organization_id=organization_id,
                    warehouse_id=UUID(str(row["warehouse_id"])),
                    code=row["code"],
                    location_type=row["location_type"],
                    active=row["active"],
                    pick_sequence=row["pick_sequence"],
                    version=row["version"],
                )
                for row in rows
            ]

    def create_supplier(
        self, supplier: Supplier, actor_id: UUID, correlation_id: UUID
    ) -> Supplier:
        self._create_party("suppliers", supplier, actor_id, correlation_id)
        return supplier

    def suppliers_for(self, organization_id: UUID, actor_id: UUID) -> list[Supplier]:
        return [
            Supplier(
                UUID(str(row["id"])), organization_id, row["code"], row["name"],
                row["currency"], row["active"], row["version"]
            )
            for row in self._party_rows("suppliers", organization_id, actor_id)
        ]

    def add_product_supplier(
        self, source: ProductSupplier, actor_id: UUID, correlation_id: UUID
    ) -> ProductSupplier:
        try:
            with self._sessions.session(source.organization_id, actor_id) as session:
                inserted = session.execute(
                    text(
                        """
                        INSERT INTO product_suppliers (
                          organization_id, id, product_id, supplier_id, supplier_sku,
                          purchase_uom, minimum_order_quantity, case_pack, lead_time_days,
                          preferred, last_unit_cost, currency, version
                        ) VALUES (
                          :organization_id, :id, :product_id, :supplier_id, :supplier_sku,
                          :purchase_uom, :minimum_order_quantity, :case_pack, :lead_time_days,
                          :preferred, :last_unit_cost, :currency, :version
                        ) ON CONFLICT (organization_id, id) DO NOTHING RETURNING id
                        """
                    ),
                    {
                        "organization_id": source.organization_id,
                        "id": source.id,
                        "product_id": source.product_id,
                        "supplier_id": source.supplier_id,
                        "supplier_sku": source.supplier_sku,
                        "purchase_uom": source.purchase_uom,
                        "minimum_order_quantity": source.minimum_order_quantity,
                        "case_pack": source.case_pack,
                        "lead_time_days": source.lead_time_days,
                        "preferred": source.preferred,
                        "last_unit_cost": source.last_unit_cost,
                        "currency": source.currency,
                        "version": source.version,
                    },
                ).scalar_one_or_none()
                if inserted is None:
                    row = session.execute(
                        text(
                            """
                            SELECT product_id, supplier_id, supplier_sku, purchase_uom,
                              minimum_order_quantity, case_pack, lead_time_days, preferred,
                              last_unit_cost, currency, version
                            FROM product_suppliers
                            WHERE organization_id=:organization_id AND id=:id
                            """
                        ),
                        {"organization_id": source.organization_id, "id": source.id},
                    ).mappings().one_or_none()
                    if row is None or (
                        UUID(str(row["product_id"])) != source.product_id
                        or UUID(str(row["supplier_id"])) != source.supplier_id
                        or row["supplier_sku"] != source.supplier_sku
                        or row["purchase_uom"] != source.purchase_uom
                        or row["minimum_order_quantity"] != source.minimum_order_quantity
                        or row["case_pack"] != source.case_pack
                        or row["currency"] != source.currency
                    ):
                        raise DuplicateResource(
                            "idempotency key was reused with different supplier data"
                        )
                    return source
                for price_break in source.price_breaks:
                    session.execute(
                        text(
                            """
                            INSERT INTO supplier_price_breaks (
                              organization_id, product_supplier_id, minimum_quantity, unit_price
                            ) VALUES (
                              :organization_id, :product_supplier_id, :minimum_quantity, :unit_price
                            )
                            """
                        ),
                        {
                            "organization_id": source.organization_id,
                            "product_supplier_id": source.id,
                            "minimum_quantity": price_break.minimum_quantity,
                            "unit_price": price_break.unit_price,
                        },
                    )
                self._record(
                    session,
                    organization_id=source.organization_id,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    action="catalog.product_supplier_created",
                    resource_type="product_supplier",
                    resource_id=source.id,
                    topic="catalog.product_supplier_created",
                    payload={
                        "product_supplier_id": str(source.id),
                        "product_id": str(source.product_id),
                        "supplier_id": str(source.supplier_id),
                    },
                )
        except IntegrityError as exc:
            raise DuplicateResource("supplier is already assigned or references are invalid") from exc
        return source

    def create_customer(
        self, customer: Customer, actor_id: UUID, correlation_id: UUID
    ) -> Customer:
        self._create_party("customers", customer, actor_id, correlation_id)
        return customer

    def customers_for(self, organization_id: UUID, actor_id: UUID) -> list[Customer]:
        return [
            Customer(
                UUID(str(row["id"])), organization_id, row["code"], row["name"],
                row["currency"], row["active"], row["version"]
            )
            for row in self._party_rows("customers", organization_id, actor_id)
        ]

    def define_kit(
        self,
        organization_id: UUID,
        product_id: UUID,
        components: tuple[KitComponent, ...],
        actor_id: UUID,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> tuple[KitComponent, ...]:
        request_body = {
            "command": "catalog.define_kit",
            "product_id": str(product_id),
            "components": sorted(
                (
                    {
                        "product_id": str(component.product_id),
                        "quantity": str(component.quantity),
                        "uom": component.uom,
                    }
                    for component in components
                ),
                key=lambda item: (item["product_id"], item["uom"]),
            ),
        }
        request_hash = sha256(
            json.dumps(request_body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        try:
            with self._sessions.session(organization_id, actor_id) as session:
                claimed = session.execute(
                    text(
                        """
                        INSERT INTO idempotency_records (
                          organization_id, key, request_hash, response_status,
                          response_body, expires_at
                        ) VALUES (
                          :organization_id, :key, :request_hash, 0, '{}'::jsonb, :expires_at
                        ) ON CONFLICT (organization_id, key) DO NOTHING RETURNING key
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "key": idempotency_key,
                        "request_hash": request_hash,
                        "expires_at": datetime.now(UTC) + timedelta(days=1),
                    },
                ).scalar_one_or_none()
                if claimed is None:
                    prior = session.execute(
                        text(
                            """
                            SELECT request_hash, response_body FROM idempotency_records
                            WHERE organization_id=:organization_id AND key=:key FOR UPDATE
                            """
                        ),
                        {"organization_id": organization_id, "key": idempotency_key},
                    ).mappings().one()
                    if prior["request_hash"] != request_hash:
                        raise IdempotencyConflict(
                            "idempotency key was reused with a different kit definition"
                        )
                    if not prior["response_body"]:
                        raise IdempotencyConflict("prior kit definition did not complete")
                    return components
                exists = session.execute(
                    text(
                        "SELECT 1 FROM products WHERE organization_id=:organization_id AND id=:id"
                    ),
                    {"organization_id": organization_id, "id": product_id},
                ).scalar_one_or_none()
                if exists is None:
                    raise ResourceNotFound("kit product not found")
                existing_rows = session.execute(
                    text(
                        """
                        SELECT component_product_id, quantity, uom FROM kit_components
                        WHERE organization_id=:organization_id AND kit_product_id=:id
                        ORDER BY component_product_id
                        """
                    ),
                    {"organization_id": organization_id, "id": product_id},
                ).mappings().all()
                requested = sorted(components, key=lambda item: str(item.product_id))
                unchanged = existing_rows and len(existing_rows) == len(requested) and all(
                    UUID(str(row["component_product_id"])) == component.product_id
                    and row["quantity"] == component.quantity
                    and row["uom"] == component.uom
                    for row, component in zip(existing_rows, requested, strict=True)
                )
                if not unchanged:
                    session.execute(
                        text(
                            """
                            INSERT INTO kits (organization_id, product_id)
                            VALUES (:organization_id, :id)
                            ON CONFLICT (organization_id, product_id)
                            DO UPDATE SET version = kits.version + 1, active = true
                            """
                        ),
                        {"organization_id": organization_id, "id": product_id},
                    )
                    session.execute(
                        text(
                            "DELETE FROM kit_components WHERE organization_id=:organization_id AND kit_product_id=:id"
                        ),
                        {"organization_id": organization_id, "id": product_id},
                    )
                    for component in components:
                        session.execute(
                            text(
                                """
                                INSERT INTO kit_components (
                                  organization_id, kit_product_id, component_product_id,
                                  quantity, uom
                                ) VALUES (
                                  :organization_id, :kit_product_id, :component_product_id,
                                  :quantity, :uom
                                )
                                """
                            ),
                            {
                                "organization_id": organization_id,
                                "kit_product_id": product_id,
                                "component_product_id": component.product_id,
                                "quantity": component.quantity,
                                "uom": component.uom,
                            },
                        )
                    self._record(
                        session,
                        organization_id=organization_id,
                        actor_id=actor_id,
                        correlation_id=correlation_id,
                        action="catalog.kit_defined",
                        resource_type="kit",
                        resource_id=product_id,
                        topic="catalog.kit_defined",
                        payload={
                            "product_id": str(product_id),
                            "component_count": len(components),
                        },
                    )
                session.execute(
                    text(
                        """
                        UPDATE idempotency_records
                        SET response_status=200, response_body=CAST(:response_body AS jsonb)
                        WHERE organization_id=:organization_id AND key=:key
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "key": idempotency_key,
                        "response_body": json.dumps(request_body),
                    },
                )
        except IntegrityError as exc:
            raise DuplicateResource("kit contains an invalid or duplicate component") from exc
        return components

    def create_lot(self, lot: Lot, actor_id: UUID, correlation_id: UUID) -> Lot:
        try:
            with self._sessions.session(lot.organization_id, actor_id) as session:
                inserted = session.execute(
                    text(
                        """
                        INSERT INTO lots (
                          organization_id, id, product_id, lot_number, manufactured_on,
                          expires_on, status, version
                        ) VALUES (
                          :organization_id, :id, :product_id, :lot_number, :manufactured_on,
                          :expires_on, :status, :version
                        ) ON CONFLICT (organization_id, id) DO NOTHING RETURNING id
                        """
                    ),
                    {
                        "organization_id": lot.organization_id,
                        "id": lot.id,
                        "product_id": lot.product_id,
                        "lot_number": lot.lot_number,
                        "manufactured_on": lot.manufactured_on,
                        "expires_on": lot.expires_on,
                        "status": lot.status,
                        "version": lot.version,
                    },
                ).scalar_one_or_none()
                if inserted is None:
                    row = session.execute(
                        text(
                            """
                            SELECT product_id, lot_number, manufactured_on, expires_on, status, version
                            FROM lots WHERE organization_id=:organization_id AND id=:id
                            """
                        ),
                        {"organization_id": lot.organization_id, "id": lot.id},
                    ).mappings().one_or_none()
                    if row is None or (
                        UUID(str(row["product_id"])) != lot.product_id
                        or row["lot_number"] != lot.lot_number
                        or row["manufactured_on"] != lot.manufactured_on
                        or row["expires_on"] != lot.expires_on
                    ):
                        raise DuplicateResource(
                            "idempotency key was reused with different lot data"
                        )
                    return Lot(
                        lot.id, lot.organization_id, lot.product_id, row["lot_number"],
                        row["manufactured_on"], row["expires_on"], row["status"], row["version"]
                    )
                self._record(
                    session,
                    organization_id=lot.organization_id,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    action="inventory.lot_created",
                    resource_type="lot",
                    resource_id=lot.id,
                    topic="inventory.lot_created",
                    payload={
                        "lot_id": str(lot.id),
                        "product_id": str(lot.product_id),
                        "lot_number": lot.lot_number,
                    },
                )
        except IntegrityError as exc:
            raise DuplicateResource("lot number already exists or product is invalid") from exc
        return lot

    def create_serial(
        self, serial: SerialNumber, actor_id: UUID, correlation_id: UUID
    ) -> SerialNumber:
        try:
            with self._sessions.session(serial.organization_id, actor_id) as session:
                inserted = session.execute(
                    text(
                        """
                        INSERT INTO serial_numbers (
                          organization_id, id, product_id, serial_number, status, version
                        ) VALUES (
                          :organization_id, :id, :product_id, :serial_number, :status, :version
                        ) ON CONFLICT (organization_id, id) DO NOTHING RETURNING id
                        """
                    ),
                    {
                        "organization_id": serial.organization_id,
                        "id": serial.id,
                        "product_id": serial.product_id,
                        "serial_number": serial.serial_number,
                        "status": serial.status,
                        "version": serial.version,
                    },
                ).scalar_one_or_none()
                if inserted is None:
                    row = session.execute(
                        text(
                            """
                            SELECT product_id, serial_number, status, version FROM serial_numbers
                            WHERE organization_id=:organization_id AND id=:id
                            """
                        ),
                        {"organization_id": serial.organization_id, "id": serial.id},
                    ).mappings().one_or_none()
                    if row is None or (
                        UUID(str(row["product_id"])) != serial.product_id
                        or row["serial_number"] != serial.serial_number
                    ):
                        raise DuplicateResource(
                            "idempotency key was reused with different serial data"
                        )
                    return SerialNumber(
                        serial.id, serial.organization_id, serial.product_id,
                        row["serial_number"], row["status"], row["version"]
                    )
                self._record(
                    session,
                    organization_id=serial.organization_id,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    action="inventory.serial_created",
                    resource_type="serial_number",
                    resource_id=serial.id,
                    topic="inventory.serial_created",
                    payload={
                        "serial_id": str(serial.id),
                        "product_id": str(serial.product_id),
                        "serial_number": serial.serial_number,
                    },
                )
        except IntegrityError as exc:
            raise DuplicateResource("serial number already exists or product is invalid") from exc
        return serial

    def _create_party(
        self,
        table: str,
        party: Supplier | Customer,
        actor_id: UUID,
        correlation_id: UUID,
    ) -> None:
        if table not in {"suppliers", "customers"}:
            raise ValueError("unsupported party table")
        resource_type = table[:-1]
        try:
            with self._sessions.session(party.organization_id, actor_id) as session:
                inserted = session.execute(
                    text(
                        f"""
                        INSERT INTO {table} (
                          organization_id, id, code, name, currency, active, version
                        ) VALUES (
                          :organization_id, :id, :code, :name, :currency, :active, :version
                        ) ON CONFLICT (organization_id, id) DO NOTHING RETURNING id
                        """
                    ),
                    {
                        "organization_id": party.organization_id,
                        "id": party.id,
                        "code": party.code,
                        "name": party.name,
                        "currency": party.currency,
                        "active": party.active,
                        "version": party.version,
                    },
                ).scalar_one_or_none()
                if inserted is None:
                    row = session.execute(
                        text(
                            f"""
                            SELECT code, name, currency, active, version FROM {table}
                            WHERE organization_id=:organization_id AND id=:id
                            """
                        ),
                        {"organization_id": party.organization_id, "id": party.id},
                    ).mappings().one_or_none()
                    if row is None or (
                        row["code"] != party.code
                        or row["name"] != party.name
                        or row["currency"] != party.currency
                    ):
                        raise DuplicateResource(
                            f"idempotency key was reused with different {resource_type} data"
                        )
                    return
                self._record(
                    session,
                    organization_id=party.organization_id,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    action=f"catalog.{resource_type}_created",
                    resource_type=resource_type,
                    resource_id=party.id,
                    topic=f"catalog.{resource_type}_created",
                    payload={f"{resource_type}_id": str(party.id), "code": party.code},
                )
        except IntegrityError as exc:
            raise DuplicateResource(f"{resource_type} id or code already exists") from exc

    def _party_rows(self, table: str, organization_id: UUID, actor_id: UUID) -> list[Any]:
        if table not in {"suppliers", "customers"}:
            raise ValueError("unsupported party table")
        with self._sessions.session(organization_id, actor_id) as session:
            return list(
                session.execute(
                    text(
                        f"""
                        SELECT id, code, name, currency, active, version FROM {table}
                        WHERE organization_id=:organization_id ORDER BY code, id LIMIT 250
                        """
                    ),
                    {"organization_id": organization_id},
                ).mappings()
            )

    @staticmethod
    def _product_by_id(session: Any, organization_id: UUID, product_id: UUID) -> Product | None:
        row = session.execute(
            text(
                """
                SELECT id, sku, name, base_uom, tracking_mode, lifecycle_state,
                       description, custom_fields, version, created_at, updated_at
                FROM products WHERE organization_id=:organization_id AND id=:id
                """
            ),
            {"organization_id": organization_id, "id": product_id},
        ).mappings().one_or_none()
        if row is None:
            return None
        return Product(
            id=UUID(str(row["id"])),
            organization_id=organization_id,
            sku=row["sku"],
            name=row["name"],
            base_uom=row["base_uom"],
            tracking_mode=TrackingMode(row["tracking_mode"]),
            lifecycle_state=LifecycleState(row["lifecycle_state"]),
            description=row["description"],
            custom_fields=dict(row["custom_fields"]),
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _same_product(left: Product, right: Product) -> bool:
        return (
            left.sku == right.sku
            and left.name == right.name
            and left.base_uom == right.base_uom
            and left.tracking_mode == right.tracking_mode
            and left.lifecycle_state == right.lifecycle_state
            and left.description == right.description
            and left.custom_fields == right.custom_fields
        )

    @staticmethod
    def _record(
        session: Any,
        *,
        organization_id: UUID,
        actor_id: UUID,
        correlation_id: UUID,
        action: str,
        resource_type: str,
        resource_id: UUID,
        topic: str,
        payload: dict[str, object],
    ) -> None:
        serialized = json.dumps(payload)
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
                "action": action,
                "resource_type": resource_type,
                "resource_id": str(resource_id),
                "correlation_id": correlation_id,
                "payload": serialized,
            },
        )
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
                "aggregate_id": resource_id,
                "correlation_id": correlation_id,
                "actor_id": actor_id,
                "payload": serialized,
            },
        )
