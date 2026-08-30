from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid5

from fastapi import APIRouter, Header, Query, Request, Response

from smartstock_api.api.auth import Principal, PrincipalDependency
from smartstock_api.api.catalog_schemas import (
    BinCreateRequest,
    BinResponse,
    KitComponentRequest,
    KitDefinitionRequest,
    KitDefinitionResponse,
    LotCreateRequest,
    LotResponse,
    PartyCreateRequest,
    PartyListResponse,
    PartyResponse,
    PriceBreakRequest,
    ProductCreateRequest,
    ProductListResponse,
    ProductResponse,
    ProductSupplierRequest,
    ProductSupplierResponse,
    SerialCreateRequest,
    SerialResponse,
    UomConversionRequest,
    UomConversionResponse,
    VariantCreateRequest,
    VariantResponse,
    WarehouseCreateRequest,
    WarehouseListResponse,
    WarehouseResponse,
)
from smartstock_api.domain.catalog import (
    BinLocation,
    CatalogStore,
    Customer,
    KitComponent,
    Lot,
    Product,
    PriceBreak,
    ProductSupplier,
    ProductVariant,
    SerialNumber,
    Supplier,
    UomConversion,
    Warehouse,
)

router = APIRouter(prefix="/v1", tags=["catalog"])
CommandKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]


def _store(request: Request) -> CatalogStore:
    return request.app.state.catalog_store


def _resource_id(organization_id: UUID, resource: str, idempotency_key: str) -> UUID:
    return uuid5(organization_id, f"{resource}:{idempotency_key}")


def _correlation_id(request: Request) -> UUID:
    return UUID(str(request.state.correlation_id))


@router.get("/products", response_model=ProductListResponse)
def list_products(
    request: Request,
    principal: Principal = PrincipalDependency,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
) -> ProductListResponse:
    principal.require("catalog.view")
    products = _store(request).products_for(principal.organization_id, principal.user_id)[:limit]
    return ProductListResponse(items=[ProductResponse.from_domain(product) for product in products])


@router.post("/products", response_model=ProductResponse, status_code=201)
def create_product(
    body: ProductCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> ProductResponse:
    principal.require("catalog.manage")
    now = datetime.now(UTC)
    product = Product(
        id=_resource_id(principal.organization_id, "product", idempotency_key),
        organization_id=principal.organization_id,
        sku=body.sku,
        name=body.name,
        base_uom=body.base_uom,
        tracking_mode=body.tracking_mode,
        lifecycle_state=body.lifecycle_state,
        description=body.description,
        custom_fields=body.custom_fields,
        created_at=now,
        updated_at=now,
    )
    created = _store(request).create_product(
        product, principal.user_id, _correlation_id(request)
    )
    response.headers["ETag"] = f'"{created.version}"'
    return ProductResponse.from_domain(created)


@router.post(
    "/products/{product_id}/uom-conversions",
    response_model=UomConversionResponse,
    status_code=201,
)
def create_uom_conversion(
    product_id: UUID,
    body: UomConversionRequest,
    request: Request,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> UomConversionResponse:
    principal.require("catalog.manage")
    conversion = UomConversion(
        id=_resource_id(principal.organization_id, "uom-conversion", idempotency_key),
        organization_id=principal.organization_id,
        product_id=product_id,
        from_uom=body.from_uom,
        to_uom=body.to_uom,
        factor=body.factor,
    )
    created = _store(request).add_conversion(
        conversion, principal.user_id, _correlation_id(request)
    )
    return UomConversionResponse(
        id=created.id,
        product_id=created.product_id,
        from_uom=created.from_uom,
        to_uom=created.to_uom,
        factor=created.factor,
        version=created.version,
    )


@router.post(
    "/products/{product_id}/variants", response_model=VariantResponse, status_code=201
)
def create_variant(
    product_id: UUID,
    body: VariantCreateRequest,
    request: Request,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> VariantResponse:
    principal.require("catalog.manage")
    variant = ProductVariant(
        id=_resource_id(principal.organization_id, "variant", idempotency_key),
        organization_id=principal.organization_id,
        product_id=product_id,
        sku=body.sku,
        name=body.name,
        attributes=body.attributes,
        lifecycle_state=body.lifecycle_state,
    )
    created = _store(request).create_variant(
        variant, principal.user_id, _correlation_id(request)
    )
    return VariantResponse(
        id=created.id,
        product_id=created.product_id,
        sku=created.sku,
        name=created.name,
        attributes=created.attributes,
        lifecycle_state=created.lifecycle_state,
        version=created.version,
    )


@router.put("/products/{product_id}/kit", response_model=KitDefinitionResponse)
def define_kit(
    product_id: UUID,
    body: KitDefinitionRequest,
    request: Request,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> KitDefinitionResponse:
    principal.require("catalog.manage")
    components = tuple(
        KitComponent(item.product_id, item.quantity, item.uom) for item in body.components
    )
    stored = _store(request).define_kit(
        principal.organization_id,
        product_id,
        components,
        principal.user_id,
        _correlation_id(request),
        idempotency_key,
    )
    return KitDefinitionResponse(
        product_id=product_id,
        components=[
            KitComponentRequest(product_id=item.product_id, quantity=item.quantity, uom=item.uom)
            for item in stored
        ],
        version=1,
    )


@router.get("/warehouses", response_model=WarehouseListResponse)
def list_warehouses(
    request: Request,
    principal: Principal = PrincipalDependency,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
) -> WarehouseListResponse:
    principal.require("inventory.view")
    warehouses = _store(request).warehouses_for(principal.organization_id, principal.user_id)
    if principal.warehouse_grants:
        warehouses = [item for item in warehouses if item.id in principal.warehouse_grants]
    return WarehouseListResponse(
        items=[WarehouseResponse.from_domain(item) for item in warehouses[:limit]]
    )


@router.post("/warehouses", response_model=WarehouseResponse, status_code=201)
def create_warehouse(
    body: WarehouseCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> WarehouseResponse:
    principal.require("warehouse.manage")
    warehouse = Warehouse(
        id=_resource_id(principal.organization_id, "warehouse", idempotency_key),
        organization_id=principal.organization_id,
        code=body.code,
        name=body.name,
        timezone=body.timezone,
    )
    created = _store(request).create_warehouse(
        warehouse, principal.user_id, _correlation_id(request)
    )
    response.headers["ETag"] = f'"{created.version}"'
    return WarehouseResponse.from_domain(created)


@router.post("/warehouses/{warehouse_id}/bins", response_model=BinResponse, status_code=201)
def create_bin(
    warehouse_id: UUID,
    body: BinCreateRequest,
    request: Request,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> BinResponse:
    principal.require("warehouse.manage")
    location = BinLocation(
        id=_resource_id(principal.organization_id, "bin", idempotency_key),
        organization_id=principal.organization_id,
        warehouse_id=warehouse_id,
        code=body.code,
        location_type=body.location_type,
        pick_sequence=body.pick_sequence,
    )
    created = _store(request).create_bin(
        location, principal.user_id, _correlation_id(request)
    )
    return BinResponse(
        id=created.id,
        warehouse_id=created.warehouse_id,
        code=created.code,
        location_type=created.location_type,
        active=created.active,
        pick_sequence=created.pick_sequence,
        version=created.version,
    )


def _create_party(
    kind: str,
    body: PartyCreateRequest,
    request: Request,
    idempotency_key: str,
    principal: Principal,
) -> PartyResponse:
    principal.require("catalog.manage")
    common = {
        "id": _resource_id(principal.organization_id, kind, idempotency_key),
        "organization_id": principal.organization_id,
        "code": body.code,
        "name": body.name,
        "currency": body.currency,
    }
    if kind == "supplier":
        party = _store(request).create_supplier(
            Supplier(**common), principal.user_id, _correlation_id(request)
        )
    else:
        party = _store(request).create_customer(
            Customer(**common), principal.user_id, _correlation_id(request)
        )
    return PartyResponse(
        id=party.id,
        code=party.code,
        name=party.name,
        currency=party.currency,
        active=party.active,
        version=party.version,
    )


@router.post("/suppliers", response_model=PartyResponse, status_code=201)
def create_supplier(
    body: PartyCreateRequest,
    request: Request,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> PartyResponse:
    return _create_party("supplier", body, request, idempotency_key, principal)


@router.get("/suppliers", response_model=PartyListResponse)
def list_suppliers(
    request: Request,
    principal: Principal = PrincipalDependency,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
) -> PartyListResponse:
    principal.require("catalog.view")
    items = _store(request).suppliers_for(principal.organization_id, principal.user_id)[:limit]
    return PartyListResponse(
        items=[
            PartyResponse(
                id=item.id,
                code=item.code,
                name=item.name,
                currency=item.currency,
                active=item.active,
                version=item.version,
            )
            for item in items
        ]
    )


@router.post(
    "/products/{product_id}/suppliers",
    response_model=ProductSupplierResponse,
    status_code=201,
)
def add_product_supplier(
    product_id: UUID,
    body: ProductSupplierRequest,
    request: Request,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> ProductSupplierResponse:
    principal.require("catalog.manage")
    source = ProductSupplier(
        id=_resource_id(principal.organization_id, "product-supplier", idempotency_key),
        organization_id=principal.organization_id,
        product_id=product_id,
        supplier_id=body.supplier_id,
        supplier_sku=body.supplier_sku,
        purchase_uom=body.purchase_uom,
        minimum_order_quantity=body.minimum_order_quantity,
        case_pack=body.case_pack,
        lead_time_days=body.lead_time_days,
        preferred=body.preferred,
        last_unit_cost=body.last_unit_cost,
        currency=body.currency,
        price_breaks=tuple(
            PriceBreak(item.minimum_quantity, item.unit_price) for item in body.price_breaks
        ),
    )
    created = _store(request).add_product_supplier(
        source, principal.user_id, _correlation_id(request)
    )
    return ProductSupplierResponse(
        id=created.id,
        product_id=created.product_id,
        supplier_id=created.supplier_id,
        supplier_sku=created.supplier_sku,
        purchase_uom=created.purchase_uom,
        minimum_order_quantity=created.minimum_order_quantity,
        case_pack=created.case_pack,
        lead_time_days=created.lead_time_days,
        preferred=created.preferred,
        last_unit_cost=created.last_unit_cost,
        currency=created.currency,
        price_breaks=[
            PriceBreakRequest(
                minimum_quantity=item.minimum_quantity, unit_price=item.unit_price
            )
            for item in created.price_breaks
        ],
        version=created.version,
    )


@router.post("/customers", response_model=PartyResponse, status_code=201)
def create_customer(
    body: PartyCreateRequest,
    request: Request,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> PartyResponse:
    return _create_party("customer", body, request, idempotency_key, principal)


@router.get("/customers", response_model=PartyListResponse)
def list_customers(
    request: Request,
    principal: Principal = PrincipalDependency,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
) -> PartyListResponse:
    principal.require("catalog.view")
    items = _store(request).customers_for(principal.organization_id, principal.user_id)[:limit]
    return PartyListResponse(
        items=[
            PartyResponse(
                id=item.id,
                code=item.code,
                name=item.name,
                currency=item.currency,
                active=item.active,
                version=item.version,
            )
            for item in items
        ]
    )


@router.post("/inventory/lots", response_model=LotResponse, status_code=201)
def create_lot(
    body: LotCreateRequest,
    request: Request,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> LotResponse:
    principal.require("inventory.adjust")
    lot = Lot(
        id=_resource_id(principal.organization_id, "lot", idempotency_key),
        organization_id=principal.organization_id,
        product_id=body.product_id,
        lot_number=body.lot_number,
        manufactured_on=body.manufactured_on,
        expires_on=body.expires_on,
    )
    created = _store(request).create_lot(lot, principal.user_id, _correlation_id(request))
    return LotResponse(
        id=created.id,
        product_id=created.product_id,
        lot_number=created.lot_number,
        manufactured_on=created.manufactured_on,
        expires_on=created.expires_on,
        status=created.status,
        version=created.version,
    )


@router.post("/inventory/serials", response_model=SerialResponse, status_code=201)
def create_serial(
    body: SerialCreateRequest,
    request: Request,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> SerialResponse:
    principal.require("inventory.adjust")
    serial = SerialNumber(
        id=_resource_id(principal.organization_id, "serial", idempotency_key),
        organization_id=principal.organization_id,
        product_id=body.product_id,
        serial_number=body.serial_number,
    )
    created = _store(request).create_serial(
        serial, principal.user_id, _correlation_id(request)
    )
    return SerialResponse(
        id=created.id,
        product_id=created.product_id,
        serial_number=created.serial_number,
        status=created.status,
        version=created.version,
    )
