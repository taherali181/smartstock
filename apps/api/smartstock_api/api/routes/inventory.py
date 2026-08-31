from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response

from smartstock_api.api.auth import Principal, PrincipalDependency
from smartstock_api.api.schemas import (
    AdjustmentResponse,
    CountPostRequest,
    CountPostResponse,
    InventoryAdjustmentRequest,
    InventoryPositionResponse,
    PositionListResponse,
    ReconciliationItemResponse,
    ReconciliationResponse,
    ReservationCreateRequest,
    ReservationResponse,
    TransferCreateRequest,
    TransferResponse,
)
from smartstock_api.domain.inventory import (
    AdjustmentCommand,
    CountCommand,
    InventoryStore,
    ReleaseReservationCommand,
    ReserveCommand,
    StockCondition,
    StockKey,
    TransferCommand,
)

router = APIRouter(prefix="/v1/inventory", tags=["inventory"])


def _ledger(request: Request) -> InventoryStore:
    return request.app.state.inventory_ledger


@router.get("/positions", response_model=PositionListResponse)
def list_positions(
    request: Request,
    principal: Principal = PrincipalDependency,
    warehouse_id: UUID | None = None,
    bin_id: UUID | None = None,
    condition: StockCondition | None = None,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
) -> PositionListResponse:
    principal.require("inventory.view")
    if (
        warehouse_id
        and principal.warehouse_grants
        and warehouse_id not in principal.warehouse_grants
    ):
        principal.require("inventory.all_warehouses")
    positions = _ledger(request).positions_for(
        principal.organization_id, principal.user_id
    )
    if principal.warehouse_grants:
        positions = [
            position
            for position in positions
            if position.key.warehouse_id in principal.warehouse_grants
        ]
    if warehouse_id:
        positions = [
            position for position in positions if position.key.warehouse_id == warehouse_id
        ]
    if bin_id:
        positions = [position for position in positions if position.key.location_id == bin_id]
    if condition:
        positions = [
            position for position in positions if position.key.condition == condition
        ]
    positions = positions[:limit]
    return PositionListResponse(
        items=[InventoryPositionResponse.from_domain(position) for position in positions]
    )


@router.post("/adjustments", response_model=AdjustmentResponse, status_code=201)
def adjust_inventory(
    body: InventoryAdjustmentRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    principal: Principal = PrincipalDependency,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> AdjustmentResponse:
    principal.require("inventory.adjust")
    if principal.warehouse_grants and body.warehouse_id not in principal.warehouse_grants:
        principal.require("inventory.all_warehouses")
    expected_tag = f'"{body.expected_version}"'
    if if_match is not None and if_match != expected_tag:
        from smartstock_api.domain.errors import ConcurrencyConflict

        raise ConcurrencyConflict("If-Match does not agree with expected_version")

    command = AdjustmentCommand(
        organization_id=principal.organization_id,
        actor_id=principal.user_id,
        stock_key=StockKey(
            organization_id=principal.organization_id,
            product_id=body.product_id,
            warehouse_id=body.warehouse_id,
            location_id=body.location_id,
            uom=body.uom,
            condition=body.condition,
            lot_id=body.lot_id,
            serial_id=body.serial_id,
            ownership=body.ownership,
        ),
        quantity_delta=body.quantity_delta,
        reason_code=body.reason_code,
        business_reference=body.business_reference,
        idempotency_key=idempotency_key,
        correlation_id=UUID(str(request.state.correlation_id)),
        expected_version=body.expected_version,
        allow_negative=body.allow_negative,
        unit_cost=body.unit_cost,
        currency=body.currency,
    )
    result = _ledger(request).adjust(command)
    if result.replayed:
        response.status_code = 200
    response.headers["ETag"] = f'"{result.position.version}"'
    return AdjustmentResponse(
        transaction_id=result.transaction.id,
        replayed=result.replayed,
        position=InventoryPositionResponse.from_domain(result.position),
    )


@router.post("/reservations", response_model=ReservationResponse, status_code=201)
def create_reservation(
    body: ReservationCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    principal: Principal = PrincipalDependency,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ReservationResponse:
    principal.require("inventory.adjust")
    if principal.warehouse_grants and body.warehouse_id not in principal.warehouse_grants:
        principal.require("inventory.all_warehouses")
    expected_tag = f'"{body.expected_position_version}"'
    if if_match is not None and if_match != expected_tag:
        from smartstock_api.domain.errors import ConcurrencyConflict

        raise ConcurrencyConflict("If-Match does not agree with expected_position_version")
    result = _ledger(request).reserve(
        ReserveCommand(
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            stock_key=StockKey(
                organization_id=principal.organization_id,
                product_id=body.product_id,
                warehouse_id=body.warehouse_id,
                location_id=body.location_id,
                uom=body.uom,
                condition=body.condition,
                lot_id=body.lot_id,
                serial_id=body.serial_id,
                ownership=body.ownership,
            ),
            source_type=body.source_type,
            source_id=body.source_id,
            quantity=body.quantity,
            expected_position_version=body.expected_position_version,
            idempotency_key=idempotency_key,
            correlation_id=UUID(str(request.state.correlation_id)),
        )
    )
    if result.replayed:
        response.status_code = 200
    response.headers["ETag"] = f'"{result.reservation.version}"'
    return ReservationResponse.from_domain(result)


@router.post(
    "/reservations/{reservation_id}/release",
    response_model=ReservationResponse,
    status_code=200,
)
def release_reservation(
    reservation_id: UUID,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    if_match: Annotated[str, Header(alias="If-Match", pattern=r'^"[0-9]+"$')],
    principal: Principal = PrincipalDependency,
) -> ReservationResponse:
    principal.require("inventory.adjust")
    expected_version = int(if_match.strip('"'))
    result = _ledger(request).release_reservation(
        ReleaseReservationCommand(
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            reservation_id=reservation_id,
            expected_reservation_version=expected_version,
            idempotency_key=idempotency_key,
            correlation_id=UUID(str(request.state.correlation_id)),
        )
    )
    response.headers["ETag"] = f'"{result.reservation.version}"'
    return ReservationResponse.from_domain(result)


@router.get("/reconciliation", response_model=ReconciliationResponse)
def reconcile_inventory(
    request: Request,
    principal: Principal = PrincipalDependency,
) -> ReconciliationResponse:
    principal.require("inventory.view")
    results = _ledger(request).reconcile(principal.organization_id, principal.user_id)
    items = [
        ReconciliationItemResponse(
            product_id=item.stock_key.product_id,
            warehouse_id=item.stock_key.warehouse_id,
            location_id=item.stock_key.location_id,
            projected_on_hand=item.projected_on_hand,
            ledger_on_hand=item.ledger_on_hand,
            projected_reserved=item.projected_reserved,
            reservation_total=item.reservation_total,
            reconciled=item.reconciled,
        )
        for item in results
    ]
    return ReconciliationResponse(items=items, exact=all(item.reconciled for item in items))


@router.post("/transfers", response_model=TransferResponse, status_code=201)
def transfer_inventory(
    body: TransferCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    principal: Principal = PrincipalDependency,
) -> TransferResponse:
    principal.require("inventory.adjust")
    if principal.warehouse_grants and (
        body.source_warehouse_id not in principal.warehouse_grants
        or body.destination_warehouse_id not in principal.warehouse_grants
    ):
        principal.require("inventory.all_warehouses")
    common = {
        "organization_id": principal.organization_id,
        "product_id": body.product_id,
        "uom": body.uom,
        "condition": body.condition,
        "ownership": body.ownership,
        "lot_id": body.lot_id,
        "serial_id": body.serial_id,
    }
    result = _ledger(request).transfer(
        TransferCommand(
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            transfer_number=body.transfer_number,
            source_key=StockKey(
                warehouse_id=body.source_warehouse_id,
                location_id=body.source_location_id,
                **common,
            ),
            destination_key=StockKey(
                warehouse_id=body.destination_warehouse_id,
                location_id=body.destination_location_id,
                **common,
            ),
            quantity=body.quantity,
            expected_source_version=body.expected_source_version,
            expected_destination_version=body.expected_destination_version,
            idempotency_key=idempotency_key,
            correlation_id=UUID(str(request.state.correlation_id)),
        )
    )
    if result.replayed:
        response.status_code = 200
    return TransferResponse.from_domain(result)


@router.post("/counts", response_model=CountPostResponse, status_code=201)
def post_cycle_count(
    body: CountPostRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    principal: Principal = PrincipalDependency,
) -> CountPostResponse:
    principal.require("inventory.adjust")
    if principal.warehouse_grants and body.warehouse_id not in principal.warehouse_grants:
        principal.require("inventory.all_warehouses")
    result = _ledger(request).post_count(
        CountCommand(
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            count_number=body.count_number,
            stock_key=StockKey(
                organization_id=principal.organization_id,
                product_id=body.product_id,
                warehouse_id=body.warehouse_id,
                location_id=body.location_id,
                uom=body.uom,
                condition=body.condition,
                ownership=body.ownership,
                lot_id=body.lot_id,
                serial_id=body.serial_id,
            ),
            counted_quantity=body.counted_quantity,
            expected_position_version=body.expected_position_version,
            idempotency_key=idempotency_key,
            correlation_id=UUID(str(request.state.correlation_id)),
        )
    )
    if result.replayed:
        response.status_code = 200
    response.headers["ETag"] = f'"{result.position.version}"'
    return CountPostResponse.from_domain(result)
