from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response

from smartstock_api.api.auth import Principal, PrincipalDependency
from smartstock_api.api.schemas import (
    AdjustmentResponse,
    InventoryAdjustmentRequest,
    InventoryPositionResponse,
    PositionListResponse,
)
from smartstock_api.domain.inventory import AdjustmentCommand, InventoryStore, StockKey

router = APIRouter(prefix="/v1/inventory", tags=["inventory"])


def _ledger(request: Request) -> InventoryStore:
    return request.app.state.inventory_ledger


@router.get("/positions", response_model=PositionListResponse)
def list_positions(
    request: Request,
    principal: Principal = PrincipalDependency,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
) -> PositionListResponse:
    principal.require("inventory.view")
    positions = _ledger(request).positions_for(principal.organization_id, principal.user_id)[:limit]
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
