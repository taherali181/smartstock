from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from smartstock_api.domain.operations import (
    OperationalOrder,
    OrderKind,
    OrderLine,
    WarehouseTask,
    WarehouseTaskState,
    WarehouseTaskType,
)

Quantity = Annotated[Decimal, Field(gt=0, max_digits=28, decimal_places=9)]
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


class WarehouseTaskCreateRequest(StrictModel):
    task_number: str = Field(min_length=1, max_length=128, pattern=r"^[^\s]+$")
    task_type: WarehouseTaskType
    warehouse_id: UUID
    source_location_id: UUID | None = None
    destination_location_id: UUID | None = None
    product_id: UUID | None = None
    quantity: Quantity | None = None
    uom: str | None = Field(default=None, min_length=1, max_length=16)
    reference_type: str | None = Field(default=None, max_length=64)
    reference_id: UUID | None = None
    assigned_to: UUID | None = None
    priority: int = Field(default=100, ge=1, le=999)


class WarehouseTaskCommandRequest(StrictModel):
    expected_version: int = Field(ge=1)
    assigned_to: UUID | None = None


class WarehouseTaskResponse(StrictModel):
    id: UUID
    task_number: str
    task_type: WarehouseTaskType
    warehouse_id: UUID
    state: WarehouseTaskState
    source_location_id: UUID | None
    destination_location_id: UUID | None
    product_id: UUID | None
    quantity: Decimal | None
    uom: str | None
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
                    "state",
                    "source_location_id",
                    "destination_location_id",
                    "product_id",
                    "quantity",
                    "uom",
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


class WarehouseTaskListResponse(StrictModel):
    items: list[WarehouseTaskResponse]
    next_cursor: str | None = None
