from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from smartstock_api.api.auth import Principal, PrincipalDependency
from smartstock_api.api.reporting_schemas import (
    ReceiptsTodayResponse,
    ReceiptTodayItemResponse,
    ReorderSuggestionResponse,
    ReorderSuggestionsResponse,
    StockSummaryItemResponse,
    StockSummaryResponse,
)
from smartstock_api.domain.inventory import StockCondition
from smartstock_api.domain.operations import OperationsStore, OrderKind
from smartstock_api.domain.reporting import receipt_summaries, reorder_suggestions, stock_summaries

router = APIRouter(prefix="/v1/reports", tags=["reports"])


def _operations(request: Request) -> OperationsStore:
    return request.app.state.operations_store


def _report_inputs(request: Request, principal: Principal, warehouse_id: UUID | None):
    positions = request.app.state.inventory_ledger.positions_for(
        principal.organization_id, principal.user_id
    )
    purchase_orders = _operations(request).orders_for(
        principal.organization_id, principal.user_id, OrderKind.PURCHASE
    )
    if principal.warehouse_grants:
        positions = [
            position
            for position in positions
            if position.key.warehouse_id in principal.warehouse_grants
        ]
        purchase_orders = [
            order for order in purchase_orders if order.warehouse_id in principal.warehouse_grants
        ]
    if warehouse_id is not None:
        if principal.warehouse_grants and warehouse_id not in principal.warehouse_grants:
            principal.require("inventory.all_warehouses")
        positions = [
            position for position in positions if position.key.warehouse_id == warehouse_id
        ]
        purchase_orders = [
            order for order in purchase_orders if order.warehouse_id == warehouse_id
        ]
    products = request.app.state.catalog_store.products_for(
        principal.organization_id, principal.user_id
    )
    return products, positions, purchase_orders


@router.get("/stock-summary", response_model=StockSummaryResponse)
def stock_summary_report(
    request: Request,
    principal: Principal = PrincipalDependency,
    warehouse_id: UUID | None = None,
    condition: StockCondition | None = None,
) -> StockSummaryResponse:
    principal.require("inventory.view")
    products, positions, purchase_orders = _report_inputs(request, principal, warehouse_id)
    if condition is not None:
        positions = [
            position for position in positions if position.key.condition == condition
        ]
    return StockSummaryResponse(
        items=[
            StockSummaryItemResponse.from_domain(item)
            for item in stock_summaries(products, positions, purchase_orders)
        ],
        generated_at=datetime.now(UTC),
    )


@router.get("/reorder-suggestions", response_model=ReorderSuggestionsResponse)
def reorder_suggestions_report(
    request: Request,
    principal: Principal = PrincipalDependency,
    warehouse_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
) -> ReorderSuggestionsResponse:
    principal.require("inventory.view")
    products, positions, purchase_orders = _report_inputs(request, principal, warehouse_id)
    items = reorder_suggestions(products, positions, purchase_orders)[:limit]
    return ReorderSuggestionsResponse(
        items=[ReorderSuggestionResponse.from_domain(item) for item in items],
        generated_at=datetime.now(UTC),
    )


@router.get("/receipts-today", response_model=ReceiptsTodayResponse)
def receipts_today_report(
    request: Request,
    principal: Principal = PrincipalDependency,
    warehouse_id: UUID | None = None,
    business_date: date | None = None,
) -> ReceiptsTodayResponse:
    principal.require("purchasing.view")
    selected_date = business_date or datetime.now(UTC).date()
    receipts = _operations(request).receipts_for(
        principal.organization_id, principal.user_id
    )
    if principal.warehouse_grants:
        receipts = [
            receipt for receipt in receipts if receipt.warehouse_id in principal.warehouse_grants
        ]
    if warehouse_id is not None:
        if principal.warehouse_grants and warehouse_id not in principal.warehouse_grants:
            principal.require("inventory.all_warehouses")
        receipts = [receipt for receipt in receipts if receipt.warehouse_id == warehouse_id]
    receipts = [receipt for receipt in receipts if receipt.posted_at.date() == selected_date]
    return ReceiptsTodayResponse(
        items=[
            ReceiptTodayItemResponse.from_domain(item)
            for item in receipt_summaries(receipts)
        ],
        generated_at=datetime.now(UTC),
    )
