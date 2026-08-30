from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from smartstock_api.api.schemas import CountPostResponse, InventoryPositionResponse
from smartstock_api.domain.inventory import StockCondition
from smartstock_api.domain.operations import (
    AllocationPostingLine,
    OperationalOrder,
    OrderKind,
    OrderLine,
    Receipt,
    ReceiptPostingLine,
    ReceiptResult,
    ReturnAuthorization,
    ReturnLine,
    SalesAllocation,
    Shipment,
    ShipmentPostingLine,
    WarehouseTask,
    WarehouseTaskCountResult,
    WarehouseTaskState,
    WarehouseTaskType,
    WarehouseTransferReceiptResult,
    WarehouseTransferShipmentResult,
)

Quantity = Annotated[Decimal, Field(gt=0, max_digits=28, decimal_places=9)]
NonnegativeQuantity = Annotated[Decimal, Field(ge=0, max_digits=28, decimal_places=9)]
Money = Annotated[Decimal, Field(ge=0, max_digits=28, decimal_places=9)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrderLineCreateRequest(StrictModel):
    product_id: UUID
    quantity: Quantity
    uom: str = Field(min_length=1, max_length=16)
    unit_price: Money
    currency: str = Field(min_length=3, max_length=3)


class OrderCreateRequest(StrictModel):
    order_number: str = Field(min_length=1, max_length=128, pattern=r"^[^\s]+$")
    party_id: UUID
    warehouse_id: UUID
    currency: str = Field(min_length=3, max_length=3)
    expected_on: date | None = None
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[OrderLineCreateRequest] = Field(min_length=1, max_length=500)


class OrderLineResponse(StrictModel):
    id: UUID
    product_id: UUID
    quantity: Decimal
    processed_quantity: Decimal
    open_quantity: Decimal
    uom: str
    unit_price: Decimal
    currency: str
    line_total: Decimal

    @classmethod
    def from_domain(cls, line: OrderLine) -> "OrderLineResponse":
        return cls(
            id=line.id,
            product_id=line.product_id,
            quantity=line.quantity,
            processed_quantity=line.received_or_shipped_quantity,
            open_quantity=line.open_quantity,
            uom=line.uom,
            unit_price=line.unit_price,
            currency=line.currency,
            line_total=line.line_total,
        )


class OrderResponse(StrictModel):
    id: UUID
    kind: OrderKind
    order_number: str
    party_id: UUID
    warehouse_id: UUID
    state: str
    currency: str
    expected_on: date | None
    notes: str | None
    lines: list[OrderLineResponse]
    total: Decimal
    version: int
    created_at: datetime
    updated_at: datetime
    replayed: bool = False

    @classmethod
    def from_domain(cls, order: OperationalOrder, replayed: bool = False) -> "OrderResponse":
        return cls(
            id=order.id,
            kind=order.kind,
            order_number=order.order_number,
            party_id=order.party_id,
            warehouse_id=order.warehouse_id,
            state=order.state,
            currency=order.currency,
            expected_on=order.expected_on,
            notes=order.notes,
            lines=[OrderLineResponse.from_domain(line) for line in order.lines],
            total=order.total,
            version=order.version,
            created_at=order.created_at,
            updated_at=order.updated_at,
            replayed=replayed,
        )


class OrderListResponse(StrictModel):
    items: list[OrderResponse]
    next_cursor: str | None = None


class WorkflowCommandRequest(StrictModel):
    expected_version: int = Field(ge=1)


class ReceiptLinePostRequest(StrictModel):
    order_line_id: UUID
    location_id: UUID
    accepted_quantity: NonnegativeQuantity = Decimal("0")
    rejected_quantity: NonnegativeQuantity = Decimal("0")
    expected_sellable_version: int = Field(default=0, ge=0)
    expected_quarantine_version: int = Field(default=0, ge=0)


class ReceiptPostRequest(StrictModel):
    receipt_number: str = Field(min_length=1, max_length=128, pattern=r"^[^\s]+$")
    expected_order_version: int = Field(ge=1)
    over_receipt_tolerance_percent: Decimal = Field(
        default=Decimal("0"), ge=0, le=100, max_digits=6, decimal_places=3
    )
    lines: list[ReceiptLinePostRequest] = Field(min_length=1, max_length=500)


class ReceiptLineResponse(StrictModel):
    id: UUID
    order_line_id: UUID
    location_id: UUID
    accepted_quantity: Decimal
    rejected_quantity: Decimal

    @classmethod
    def from_domain(cls, line: ReceiptPostingLine) -> "ReceiptLineResponse":
        return cls(
            id=line.id,
            order_line_id=line.order_line_id,
            location_id=line.location_id,
            accepted_quantity=line.accepted_quantity,
            rejected_quantity=line.rejected_quantity,
        )


class ReceiptResponse(StrictModel):
    id: UUID
    receipt_number: str
    purchase_order_id: UUID
    warehouse_id: UUID
    state: str
    inventory_transaction_ids: list[UUID]
    lines: list[ReceiptLineResponse]
    version: int
    posted_at: datetime
    order: OrderResponse
    replayed: bool = False

    @classmethod
    def from_domain(
        cls, receipt: Receipt, order: OperationalOrder, replayed: bool = False
    ) -> "ReceiptResponse":
        return cls(
            id=receipt.id,
            receipt_number=receipt.receipt_number,
            purchase_order_id=receipt.purchase_order_id,
            warehouse_id=receipt.warehouse_id,
            state=receipt.state,
            inventory_transaction_ids=list(receipt.inventory_transaction_ids),
            lines=[ReceiptLineResponse.from_domain(line) for line in receipt.lines],
            version=receipt.version,
            posted_at=receipt.posted_at,
            order=OrderResponse.from_domain(order),
            replayed=replayed,
        )


class AllocationLinePostRequest(StrictModel):
    order_line_id: UUID
    location_id: UUID
    quantity: Quantity
    expected_position_version: int = Field(ge=0)


class AllocationPostRequest(StrictModel):
    expected_order_version: int = Field(ge=1)
    lines: list[AllocationLinePostRequest] = Field(min_length=1, max_length=500)


class AllocationLineResponse(StrictModel):
    id: UUID
    order_line_id: UUID
    location_id: UUID
    quantity: Decimal

    @classmethod
    def from_domain(cls, line: AllocationPostingLine) -> "AllocationLineResponse":
        return cls(
            id=line.id,
            order_line_id=line.order_line_id,
            location_id=line.location_id,
            quantity=line.quantity,
        )


class AllocationResponse(StrictModel):
    id: UUID
    sales_order_id: UUID
    warehouse_id: UUID
    state: str
    reservation_ids: list[UUID]
    lines: list[AllocationLineResponse]
    version: int
    created_at: datetime
    order: OrderResponse
    replayed: bool = False

    @classmethod
    def from_domain(
        cls, allocation: SalesAllocation, order: OperationalOrder, replayed: bool = False
    ) -> "AllocationResponse":
        return cls(
            id=allocation.id,
            sales_order_id=allocation.sales_order_id,
            warehouse_id=allocation.warehouse_id,
            state=allocation.state,
            reservation_ids=list(allocation.reservation_ids),
            lines=[AllocationLineResponse.from_domain(line) for line in allocation.lines],
            version=allocation.version,
            created_at=allocation.created_at,
            order=OrderResponse.from_domain(order),
            replayed=replayed,
        )


class ShipmentLinePostRequest(StrictModel):
    order_line_id: UUID
    reservation_id: UUID
    expected_reservation_version: int = Field(default=1, ge=1)
    expected_position_version: int = Field(ge=1)


class ShipmentPostRequest(StrictModel):
    expected_order_version: int = Field(ge=1)
    lines: list[ShipmentLinePostRequest] = Field(min_length=1, max_length=500)


class ShipmentResponse(StrictModel):
    id: UUID
    sales_order_id: UUID
    warehouse_id: UUID
    state: str
    inventory_transaction_ids: list[UUID]
    version: int
    shipped_at: datetime
    order: OrderResponse
    replayed: bool = False

    @classmethod
    def from_domain(
        cls, shipment: Shipment, order: OperationalOrder, replayed: bool = False
    ) -> "ShipmentResponse":
        return cls(
            id=shipment.id, sales_order_id=shipment.sales_order_id,
            warehouse_id=shipment.warehouse_id, state=shipment.state,
            inventory_transaction_ids=list(shipment.inventory_transaction_ids),
            version=shipment.version, shipped_at=shipment.shipped_at,
            order=OrderResponse.from_domain(order), replayed=replayed,
        )


class ReturnLineCreateRequest(StrictModel):
    order_line_id: UUID
    quantity: Quantity
    reason_code: str = Field(min_length=1, max_length=64)


class ReturnCreateRequest(StrictModel):
    return_number: str = Field(min_length=1, max_length=128, pattern=r"^[^\s]+$")
    sales_order_id: UUID
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[ReturnLineCreateRequest] = Field(min_length=1, max_length=500)


class ReturnLineResponse(StrictModel):
    id: UUID
    order_line_id: UUID
    product_id: UUID
    quantity: Decimal
    received_quantity: Decimal
    uom: str
    reason_code: str


class ReturnResponse(StrictModel):
    id: UUID
    return_number: str
    sales_order_id: UUID
    warehouse_id: UUID
    state: str
    notes: str | None
    lines: list[ReturnLineResponse]
    version: int
    created_at: datetime
    updated_at: datetime
    replayed: bool = False

    @classmethod
    def from_domain(cls, item: ReturnAuthorization, replayed: bool = False) -> "ReturnResponse":
        return cls(
            id=item.id, return_number=item.return_number, sales_order_id=item.sales_order_id,
            warehouse_id=item.warehouse_id, state=item.state, notes=item.notes,
            lines=[ReturnLineResponse(**{
                name: getattr(line, name) for name in (
                    "id", "order_line_id", "product_id", "quantity", "received_quantity",
                    "uom", "reason_code"
                )
            }) for line in item.lines],
            version=item.version, created_at=item.created_at, updated_at=item.updated_at,
            replayed=replayed,
        )


class ReturnListResponse(StrictModel):
    items: list[ReturnResponse]


class ReturnReceiptLineRequest(StrictModel):
    return_line_id: UUID
    location_id: UUID
    expected_quarantine_version: int = Field(default=0, ge=0)


class ReturnReceiptRequest(StrictModel):
    expected_version: int = Field(ge=1)
    lines: list[ReturnReceiptLineRequest] = Field(min_length=1, max_length=500)


class ReturnReceiptResponse(StrictModel):
    return_authorization: ReturnResponse
    inventory_transaction_ids: list[UUID]
    replayed: bool = False


class WarehouseTaskCreateRequest(StrictModel):
    task_number: str = Field(min_length=1, max_length=128, pattern=r"^[^\s]+$")
    task_type: WarehouseTaskType
    warehouse_id: UUID
    destination_warehouse_id: UUID | None = None
    source_location_id: UUID | None = None
    destination_location_id: UUID | None = None
    product_id: UUID | None = None
    quantity: Quantity | None = None
    uom: str | None = Field(default=None, min_length=1, max_length=16)
    condition: StockCondition = StockCondition.SELLABLE
    ownership: str = Field(default="owned", min_length=1, max_length=32)
    lot_id: UUID | None = None
    serial_id: UUID | None = None
    expected_position_version: int | None = Field(default=None, ge=0)
    reference_type: str | None = Field(default=None, max_length=64)
    reference_id: UUID | None = None
    assigned_to: UUID | None = None
    priority: int = Field(default=100, ge=1, le=999)


class WarehouseTaskCommandRequest(StrictModel):
    expected_version: int = Field(ge=1)
    assigned_to: UUID | None = None


class WarehouseTaskCountRequest(StrictModel):
    expected_task_version: int = Field(ge=1)
    counted_quantity: NonnegativeQuantity


class WarehousePurchaseReceiptRequest(ReceiptPostRequest):
    expected_task_version: int = Field(ge=1)


class WarehouseTransferShipRequest(StrictModel):
    expected_task_version: int = Field(ge=1)


class WarehouseTransferReceiveRequest(StrictModel):
    expected_task_version: int = Field(ge=1)
    received_quantity: NonnegativeQuantity


class WarehouseTaskResponse(StrictModel):
    id: UUID
    task_number: str
    task_type: WarehouseTaskType
    warehouse_id: UUID
    destination_warehouse_id: UUID | None
    state: WarehouseTaskState
    source_location_id: UUID | None
    destination_location_id: UUID | None
    product_id: UUID | None
    quantity: Decimal | None
    uom: str | None
    condition: StockCondition
    ownership: str
    lot_id: UUID | None
    serial_id: UUID | None
    expected_position_version: int | None
    reference_type: str | None
    reference_id: UUID | None
    assigned_to: UUID | None
    priority: int
    version: int
    created_at: datetime
    updated_at: datetime
    replayed: bool = False

    @classmethod
    def from_domain(
        cls, task: WarehouseTask, replayed: bool = False
    ) -> "WarehouseTaskResponse":
        return cls(
            **{
                name: getattr(task, name)
                for name in (
                    "id",
                    "task_number",
                    "task_type",
                    "warehouse_id",
                    "destination_warehouse_id",
                    "state",
                    "source_location_id",
                    "destination_location_id",
                    "product_id",
                    "quantity",
                    "uom",
                    "condition",
                    "ownership",
                    "lot_id",
                    "serial_id",
                    "expected_position_version",
                    "reference_type",
                    "reference_id",
                    "assigned_to",
                    "priority",
                    "version",
                    "created_at",
                    "updated_at",
                )
            },
            replayed=replayed,
        )


class WarehouseTaskCountResponse(StrictModel):
    task: WarehouseTaskResponse
    count: CountPostResponse
    replayed: bool = False

    @classmethod
    def from_domain(cls, result: WarehouseTaskCountResult) -> "WarehouseTaskCountResponse":
        return cls(
            task=WarehouseTaskResponse.from_domain(result.task, result.replayed),
            count=CountPostResponse.from_domain(result.count),
            replayed=result.replayed,
        )


class WarehousePurchaseReceiptResponse(StrictModel):
    task: WarehouseTaskResponse
    receipt: ReceiptResponse
    follow_up_task: WarehouseTaskResponse | None = None
    replayed: bool = False

    @classmethod
    def from_domain(cls, result: ReceiptResult) -> "WarehousePurchaseReceiptResponse":
        if result.task is None:
            raise ValueError("task-bound receipt result is missing its warehouse task")
        return cls(
            task=WarehouseTaskResponse.from_domain(result.task, result.replayed),
            receipt=ReceiptResponse.from_domain(
                result.receipt, result.order, result.replayed
            ),
            follow_up_task=(
                WarehouseTaskResponse.from_domain(result.follow_up_task)
                if result.follow_up_task
                else None
            ),
            replayed=result.replayed,
        )


class WarehouseTransferShipmentResponse(StrictModel):
    task: WarehouseTaskResponse
    receipt_task: WarehouseTaskResponse
    transfer_id: UUID
    transaction_id: UUID
    source_position: InventoryPositionResponse
    destination_position: InventoryPositionResponse
    quantity: Decimal
    replayed: bool = False

    @classmethod
    def from_domain(
        cls, result: WarehouseTransferShipmentResult
    ) -> "WarehouseTransferShipmentResponse":
        return cls(
            task=WarehouseTaskResponse.from_domain(result.task, result.replayed),
            receipt_task=WarehouseTaskResponse.from_domain(result.receipt_task),
            transfer_id=result.shipment.transfer_id,
            transaction_id=result.shipment.transaction.id,
            source_position=InventoryPositionResponse.from_domain(
                result.shipment.source_position
            ),
            destination_position=InventoryPositionResponse.from_domain(
                result.shipment.destination_position
            ),
            quantity=result.shipment.quantity,
            replayed=result.replayed,
        )


class WarehouseTransferReceiptResponse(StrictModel):
    task: WarehouseTaskResponse
    transfer_id: UUID
    transaction_id: UUID
    destination_position: InventoryPositionResponse
    shipped_quantity: Decimal
    received_quantity: Decimal
    discrepancy_quantity: Decimal
    state: str
    replayed: bool = False

    @classmethod
    def from_domain(
        cls, result: WarehouseTransferReceiptResult
    ) -> "WarehouseTransferReceiptResponse":
        return cls(
            task=WarehouseTaskResponse.from_domain(result.task, result.replayed),
            transfer_id=result.receipt.transfer_id,
            transaction_id=result.receipt.transaction.id,
            destination_position=InventoryPositionResponse.from_domain(
                result.receipt.destination_position
            ),
            shipped_quantity=result.receipt.shipped_quantity,
            received_quantity=result.receipt.received_quantity,
            discrepancy_quantity=result.receipt.discrepancy_quantity,
            state=result.receipt.state,
            replayed=result.replayed,
        )


class WarehouseTaskListResponse(StrictModel):
    items: list[WarehouseTaskResponse]
    next_cursor: str | None = None
