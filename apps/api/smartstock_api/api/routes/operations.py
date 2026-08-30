from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid5

from fastapi import APIRouter, Header, Query, Request, Response

from smartstock_api.api.auth import Principal, PrincipalDependency
from smartstock_api.api.operations_schemas import (
    AllocationPostRequest,
    AllocationResponse,
    OrderCreateRequest,
    OrderListResponse,
    OrderResponse,
    ReceiptPostRequest,
    ReceiptResponse,
    ShipmentPostRequest,
    ShipmentResponse,
    WarehouseTaskCommandRequest,
    WarehouseTaskCreateRequest,
    WarehouseTaskListResponse,
    WarehouseTaskResponse,
    WorkflowCommandRequest,
)
from smartstock_api.domain.errors import InvalidStateTransition
from smartstock_api.domain.operations import (
    AllocationPostingLine,
    OperationalOrder,
    OperationsStore,
    OrderKind,
    OrderLine,
    ReceiptPostingLine,
    ShipmentPostingLine,
    WarehouseTask,
    WarehouseTaskState,
)

router = APIRouter(prefix="/v1", tags=["operations"])
CommandKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]

ORDER_COMMANDS: dict[OrderKind, dict[str, str]] = {
    OrderKind.PURCHASE: {
        "submit": "pending_approval",
        "return-to-draft": "draft",
        "approve": "approved",
        "send": "sent",
        "acknowledge": "acknowledged",
        "mark-partially-received": "partially_received",
        "mark-received": "received",
        "close": "closed",
        "cancel": "cancelled",
    },
    OrderKind.SALES: {
        "convert-to-draft": "draft",
        "confirm": "confirmed",
        "backorder": "backordered",
        "start-picking": "picking",
        "mark-delivered": "delivered",
        "close": "closed",
        "cancel": "cancelled",
    },
}

TASK_COMMANDS = {
    "assign": WarehouseTaskState.ASSIGNED,
    "start": WarehouseTaskState.IN_PROGRESS,
    "complete": WarehouseTaskState.COMPLETED,
    "report-exception": WarehouseTaskState.EXCEPTION,
    "reopen": WarehouseTaskState.OPEN,
    "cancel": WarehouseTaskState.CANCELLED,
}


def _store(request: Request) -> OperationsStore:
    return request.app.state.operations_store


def _correlation_id(request: Request) -> UUID:
    return UUID(str(request.state.correlation_id))


def _resource_id(organization_id: UUID, resource: str, key: str) -> UUID:
    return uuid5(organization_id, f"{resource}:{key}")


def _require_order_permission(principal: Principal, kind: OrderKind, action: str) -> None:
    prefix = "purchasing" if kind == OrderKind.PURCHASE else "orders"
    principal.require(f"{prefix}.{action}")


def _create_order(
    kind: OrderKind,
    body: OrderCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: str,
    principal: Principal,
) -> OrderResponse:
    _require_order_permission(principal, kind, "propose")
    if principal.warehouse_grants and body.warehouse_id not in principal.warehouse_grants:
        principal.require("inventory.all_warehouses")
    now = datetime.now(UTC)
    order_id = _resource_id(principal.organization_id, f"{kind.value}-order", idempotency_key)
    order = OperationalOrder(
        id=order_id,
        organization_id=principal.organization_id,
        kind=kind,
        order_number=body.order_number,
        party_id=body.party_id,
        warehouse_id=body.warehouse_id,
        state="draft" if kind == OrderKind.PURCHASE else "quote",
        lines=tuple(
            OrderLine(
                id=uuid5(order_id, f"line:{index}"),
                product_id=line.product_id,
                quantity=line.quantity,
                uom=line.uom,
                unit_price=line.unit_price,
                currency=line.currency,
            )
            for index, line in enumerate(body.lines, start=1)
        ),
        currency=body.currency,
        expected_on=body.expected_on,
        notes=body.notes,
        created_at=now,
        updated_at=now,
    )
    stored, replayed = _store(request).create_order(
        order, principal.user_id, _correlation_id(request), idempotency_key
    )
    if replayed:
        response.status_code = 200
    response.headers["ETag"] = f'"{stored.version}"'
    return OrderResponse.from_domain(stored, replayed)


def _list_orders(
    kind: OrderKind, request: Request, principal: Principal, limit: int
) -> OrderListResponse:
    _require_order_permission(principal, kind, "view")
    orders = _store(request).orders_for(principal.organization_id, principal.user_id, kind)
    if principal.warehouse_grants:
        orders = [order for order in orders if order.warehouse_id in principal.warehouse_grants]
    return OrderListResponse(items=[OrderResponse.from_domain(order) for order in orders[:limit]])


def _get_order(
    kind: OrderKind, order_id: UUID, request: Request, principal: Principal
) -> OrderResponse:
    _require_order_permission(principal, kind, "view")
    order = _store(request).order(principal.organization_id, principal.user_id, kind, order_id)
    if principal.warehouse_grants and order.warehouse_id not in principal.warehouse_grants:
        principal.require("inventory.all_warehouses")
    return OrderResponse.from_domain(order)


def _transition_order(
    kind: OrderKind,
    order_id: UUID,
    command: str,
    body: WorkflowCommandRequest,
    request: Request,
    response: Response,
    idempotency_key: str,
    principal: Principal,
) -> OrderResponse:
    target = ORDER_COMMANDS[kind].get(command)
    if target is None:
        raise InvalidStateTransition(f"unknown {kind.value} order command: {command}")
    _require_order_permission(
        principal, kind, "approve" if command == "approve" else "execute"
    )
    stored, replayed = _store(request).transition_order(
        principal.organization_id,
        principal.user_id,
        kind,
        order_id,
        target,
        body.expected_version,
        _correlation_id(request),
        idempotency_key,
    )
    response.headers["ETag"] = f'"{stored.version}"'
    return OrderResponse.from_domain(stored, replayed)


@router.get("/purchase-orders", response_model=OrderListResponse)
def list_purchase_orders(
    request: Request,
    principal: Principal = PrincipalDependency,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
) -> OrderListResponse:
    return _list_orders(OrderKind.PURCHASE, request, principal, limit)


@router.post("/purchase-orders", response_model=OrderResponse, status_code=201)
def create_purchase_order(
    body: OrderCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> OrderResponse:
    return _create_order(OrderKind.PURCHASE, body, request, response, idempotency_key, principal)


@router.get("/purchase-orders/{order_id}", response_model=OrderResponse)
def get_purchase_order(
    order_id: UUID, request: Request, principal: Principal = PrincipalDependency
) -> OrderResponse:
    return _get_order(OrderKind.PURCHASE, order_id, request, principal)


@router.post("/purchase-orders/{order_id}/commands/{command}", response_model=OrderResponse)
def command_purchase_order(
    order_id: UUID,
    command: str,
    body: WorkflowCommandRequest,
    request: Request,
    response: Response,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> OrderResponse:
    return _transition_order(
        OrderKind.PURCHASE, order_id, command, body, request, response, idempotency_key, principal
    )


@router.post(
    "/purchase-orders/{order_id}/receipts", response_model=ReceiptResponse, status_code=201
)
def post_purchase_receipt(
    order_id: UUID,
    body: ReceiptPostRequest,
    request: Request,
    response: Response,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> ReceiptResponse:
    principal.require("warehouse.execute")
    order = _store(request).order(
        principal.organization_id, principal.user_id, OrderKind.PURCHASE, order_id
    )
    if principal.warehouse_grants and order.warehouse_id not in principal.warehouse_grants:
        principal.require("inventory.all_warehouses")
    receipt_id = _resource_id(principal.organization_id, "receipt", idempotency_key)
    result = _store(request).post_receipt(
        principal.organization_id,
        principal.user_id,
        order_id,
        receipt_id,
        body.receipt_number,
        tuple(
            ReceiptPostingLine(
                id=uuid5(receipt_id, f"line:{index}"),
                order_line_id=line.order_line_id,
                location_id=line.location_id,
                accepted_quantity=line.accepted_quantity,
                rejected_quantity=line.rejected_quantity,
                expected_sellable_version=line.expected_sellable_version,
                expected_quarantine_version=line.expected_quarantine_version,
            )
            for index, line in enumerate(body.lines, start=1)
        ),
        body.expected_order_version,
        body.over_receipt_tolerance_percent,
        _correlation_id(request),
        idempotency_key,
    )
    if result.replayed:
        response.status_code = 200
    response.headers["ETag"] = f'"{result.order.version}"'
    return ReceiptResponse.from_domain(result.receipt, result.order, result.replayed)


@router.get("/sales-orders", response_model=OrderListResponse)
def list_sales_orders(
    request: Request,
    principal: Principal = PrincipalDependency,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
) -> OrderListResponse:
    return _list_orders(OrderKind.SALES, request, principal, limit)


@router.post("/sales-orders", response_model=OrderResponse, status_code=201)
def create_sales_order(
    body: OrderCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> OrderResponse:
    return _create_order(OrderKind.SALES, body, request, response, idempotency_key, principal)


@router.get("/sales-orders/{order_id}", response_model=OrderResponse)
def get_sales_order(
    order_id: UUID, request: Request, principal: Principal = PrincipalDependency
) -> OrderResponse:
    return _get_order(OrderKind.SALES, order_id, request, principal)


@router.post("/sales-orders/{order_id}/commands/{command}", response_model=OrderResponse)
def command_sales_order(
    order_id: UUID,
    command: str,
    body: WorkflowCommandRequest,
    request: Request,
    response: Response,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> OrderResponse:
    return _transition_order(
        OrderKind.SALES, order_id, command, body, request, response, idempotency_key, principal
    )


@router.post(
    "/sales-orders/{order_id}/allocations", response_model=AllocationResponse, status_code=201
)
def allocate_sales_order(
    order_id: UUID,
    body: AllocationPostRequest,
    request: Request,
    response: Response,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> AllocationResponse:
    principal.require("orders.execute")
    order = _store(request).order(
        principal.organization_id, principal.user_id, OrderKind.SALES, order_id
    )
    if principal.warehouse_grants and order.warehouse_id not in principal.warehouse_grants:
        principal.require("inventory.all_warehouses")
    allocation_id = _resource_id(principal.organization_id, "sales-allocation", idempotency_key)
    result = _store(request).allocate_sales_order(
        principal.organization_id,
        principal.user_id,
        order_id,
        allocation_id,
        tuple(
            AllocationPostingLine(
                id=uuid5(allocation_id, f"line:{index}"),
                order_line_id=line.order_line_id,
                location_id=line.location_id,
                quantity=line.quantity,
                expected_position_version=line.expected_position_version,
            )
            for index, line in enumerate(body.lines, start=1)
        ),
        body.expected_order_version,
        _correlation_id(request),
        idempotency_key,
    )
    if result.replayed:
        response.status_code = 200
    response.headers["ETag"] = f'"{result.order.version}"'
    return AllocationResponse.from_domain(result.allocation, result.order, result.replayed)


@router.post(
    "/sales-orders/{order_id}/shipments", response_model=ShipmentResponse, status_code=201
)
def post_sales_shipment(
    order_id: UUID, body: ShipmentPostRequest, request: Request, response: Response,
    idempotency_key: CommandKey, principal: Principal = PrincipalDependency,
) -> ShipmentResponse:
    principal.require("warehouse.execute")
    order = _store(request).order(
        principal.organization_id, principal.user_id, OrderKind.SALES, order_id
    )
    if principal.warehouse_grants and order.warehouse_id not in principal.warehouse_grants:
        principal.require("inventory.all_warehouses")
    shipment_id = _resource_id(principal.organization_id, "shipment", idempotency_key)
    result = _store(request).post_shipment(
        principal.organization_id, principal.user_id, order_id, shipment_id,
        tuple(ShipmentPostingLine(
            uuid5(shipment_id, f"line:{index}"), line.order_line_id, line.reservation_id,
            line.expected_reservation_version, line.expected_position_version,
        ) for index, line in enumerate(body.lines, start=1)),
        body.expected_order_version, _correlation_id(request), idempotency_key,
    )
    if result.replayed:
        response.status_code = 200
    response.headers["ETag"] = f'"{result.order.version}"'
    return ShipmentResponse.from_domain(result.shipment, result.order, result.replayed)


@router.get("/warehouse-tasks", response_model=WarehouseTaskListResponse)
def list_warehouse_tasks(
    request: Request,
    principal: Principal = PrincipalDependency,
    warehouse_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
) -> WarehouseTaskListResponse:
    principal.require("warehouse.execute")
    if warehouse_id and principal.warehouse_grants and warehouse_id not in principal.warehouse_grants:
        principal.require("inventory.all_warehouses")
    tasks = _store(request).tasks_for(
        principal.organization_id, principal.user_id, warehouse_id
    )
    if principal.warehouse_grants:
        tasks = [task for task in tasks if task.warehouse_id in principal.warehouse_grants]
    return WarehouseTaskListResponse(
        items=[WarehouseTaskResponse.from_domain(task) for task in tasks[:limit]]
    )


@router.post("/warehouse-tasks", response_model=WarehouseTaskResponse, status_code=201)
def create_warehouse_task(
    body: WarehouseTaskCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> WarehouseTaskResponse:
    principal.require("warehouse.execute")
    if principal.warehouse_grants and body.warehouse_id not in principal.warehouse_grants:
        principal.require("inventory.all_warehouses")
    now = datetime.now(UTC)
    task = WarehouseTask(
        id=_resource_id(principal.organization_id, "warehouse-task", idempotency_key),
        organization_id=principal.organization_id,
        task_number=body.task_number,
        task_type=body.task_type,
        warehouse_id=body.warehouse_id,
        source_location_id=body.source_location_id,
        destination_location_id=body.destination_location_id,
        product_id=body.product_id,
        quantity=body.quantity,
        uom=body.uom,
        reference_type=body.reference_type,
        reference_id=body.reference_id,
        assigned_to=body.assigned_to,
        priority=body.priority,
        created_at=now,
        updated_at=now,
    )
    stored, replayed = _store(request).create_task(
        task, principal.user_id, _correlation_id(request), idempotency_key
    )
    if replayed:
        response.status_code = 200
    response.headers["ETag"] = f'"{stored.version}"'
    return WarehouseTaskResponse.from_domain(stored, replayed)


@router.post(
    "/warehouse-tasks/{task_id}/commands/{command}", response_model=WarehouseTaskResponse
)
def command_warehouse_task(
    task_id: UUID,
    command: str,
    body: WarehouseTaskCommandRequest,
    request: Request,
    response: Response,
    idempotency_key: CommandKey,
    principal: Principal = PrincipalDependency,
) -> WarehouseTaskResponse:
    principal.require("warehouse.execute")
    target = TASK_COMMANDS.get(command)
    if target is None:
        raise InvalidStateTransition(f"unknown warehouse task command: {command}")
    stored, replayed = _store(request).transition_task(
        principal.organization_id,
        principal.user_id,
        task_id,
        target,
        body.expected_version,
        _correlation_id(request),
        idempotency_key,
        body.assigned_to,
    )
    response.headers["ETag"] = f'"{stored.version}"'
    return WarehouseTaskResponse.from_domain(stored, replayed)
