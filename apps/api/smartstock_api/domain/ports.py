from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from .catalog import Product, Supplier
from .inventory import (
    AdjustmentCommand,
    AdjustmentResult,
    CountCommand,
    CountResult,
    InventoryPosition,
    ReleaseReservationCommand,
    ReservationResult,
    ReserveCommand,
    StockCondition,
    TransferCommand,
    TransferResult,
)
from .operations import (
    AllocationPostingLine,
    AllocationResult,
    OperationalOrder,
    OrderKind,
    ReceiptPostingLine,
    ReceiptResult,
    ReturnAuthorization,
    ReturnReceiptLine,
    ReturnReceiptResult,
    Shipment,
    ShipmentPostingLine,
    ShipmentResult,
    WarehouseTask,
    WarehouseTaskCountResult,
    WarehouseTaskState,
    WarehouseTransferReceiptResult,
    WarehouseTransferShipmentResult,
)


class OperationalReadPort(Protocol):
    def inventory_positions(
        self,
        organization_id: UUID,
        actor_id: UUID,
        *,
        warehouse_id: UUID | None = None,
        location_id: UUID | None = None,
        condition: StockCondition | None = None,
        product_id: UUID | None = None,
    ) -> Sequence[InventoryPosition]: ...

    def atp_by_horizon(
        self,
        organization_id: UUID,
        actor_id: UUID,
        product_id: UUID,
        horizon: date,
        *,
        warehouse_id: UUID | None = None,
        location_id: UUID | None = None,
    ) -> Decimal: ...

    def product_lookup(
        self,
        organization_id: UUID,
        actor_id: UUID,
        *,
        product_id: UUID | None = None,
        sku: str | None = None,
        query: str | None = None,
    ) -> Sequence[Product]: ...

    def supplier_lookup(
        self,
        organization_id: UUID,
        actor_id: UUID,
        *,
        supplier_id: UUID | None = None,
        code: str | None = None,
        product_id: UUID | None = None,
        query: str | None = None,
    ) -> Sequence[Supplier]: ...

    def purchase_order_status(
        self,
        organization_id: UUID,
        actor_id: UUID,
        *,
        order_id: UUID | None = None,
        order_number: str | None = None,
    ) -> OperationalOrder: ...

    def sales_order_status(
        self,
        organization_id: UUID,
        actor_id: UUID,
        *,
        order_id: UUID | None = None,
        order_number: str | None = None,
    ) -> OperationalOrder: ...

    def shipment_status(
        self,
        organization_id: UUID,
        actor_id: UUID,
        *,
        shipment_id: UUID | None = None,
        business_reference: str | None = None,
    ) -> Shipment: ...

    def warehouse_task_status(
        self,
        organization_id: UUID,
        actor_id: UUID,
        *,
        task_id: UUID | None = None,
        task_number: str | None = None,
    ) -> WarehouseTask: ...

    def reporting_aggregate(
        self,
        organization_id: UUID,
        actor_id: UUID,
        report_name: str,
        *,
        filters: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]: ...

    def forecast_lookup(
        self,
        organization_id: UUID,
        actor_id: UUID,
        product_id: UUID,
        *,
        warehouse_id: UUID | None = None,
        horizon: date | None = None,
        forecast_id: UUID | None = None,
    ) -> Mapping[str, object]: ...


class OperationalCommandPort(Protocol):
    def create_order(
        self,
        order: OperationalOrder,
        actor_id: UUID,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> tuple[OperationalOrder, bool]: ...

    def transition_order(
        self,
        organization_id: UUID,
        actor_id: UUID,
        kind: OrderKind,
        order_id: UUID,
        target: str,
        expected_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> tuple[OperationalOrder, bool]: ...

    def post_receipt(
        self,
        organization_id: UUID,
        actor_id: UUID,
        purchase_order_id: UUID,
        receipt_id: UUID,
        receipt_number: str,
        lines: tuple[ReceiptPostingLine, ...],
        expected_order_version: int,
        over_receipt_tolerance_percent: Decimal,
        correlation_id: UUID,
        idempotency_key: str,
        task_id: UUID | None = None,
        expected_task_version: int | None = None,
    ) -> ReceiptResult: ...

    def allocate_sales_order(
        self,
        organization_id: UUID,
        actor_id: UUID,
        sales_order_id: UUID,
        allocation_id: UUID,
        lines: tuple[AllocationPostingLine, ...],
        expected_order_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> AllocationResult: ...

    def post_shipment(
        self,
        organization_id: UUID,
        actor_id: UUID,
        sales_order_id: UUID,
        shipment_id: UUID,
        lines: tuple[ShipmentPostingLine, ...],
        expected_order_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> ShipmentResult: ...

    def create_return(
        self,
        item: ReturnAuthorization,
        actor_id: UUID,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> tuple[ReturnAuthorization, bool]: ...

    def transition_return(
        self,
        organization_id: UUID,
        actor_id: UUID,
        return_id: UUID,
        target: str,
        expected_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> tuple[ReturnAuthorization, bool]: ...

    def receive_return(
        self,
        organization_id: UUID,
        actor_id: UUID,
        return_id: UUID,
        lines: tuple[ReturnReceiptLine, ...],
        expected_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> ReturnReceiptResult: ...

    def create_task(
        self,
        task: WarehouseTask,
        actor_id: UUID,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> tuple[WarehouseTask, bool]: ...

    def transition_task(
        self,
        organization_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        target: WarehouseTaskState,
        expected_version: int,
        correlation_id: UUID,
        idempotency_key: str,
        assigned_to: UUID | None = None,
    ) -> tuple[WarehouseTask, bool]: ...

    def complete_count_task(
        self,
        organization_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        counted_quantity: Decimal,
        expected_task_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> WarehouseTaskCountResult: ...

    def ship_transfer_task(
        self,
        organization_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        expected_task_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> WarehouseTransferShipmentResult: ...

    def receive_transfer_task(
        self,
        organization_id: UUID,
        actor_id: UUID,
        task_id: UUID,
        received_quantity: Decimal,
        expected_task_version: int,
        correlation_id: UUID,
        idempotency_key: str,
    ) -> WarehouseTransferReceiptResult: ...

    def adjust_inventory(self, command: AdjustmentCommand) -> AdjustmentResult: ...

    def reserve_inventory(self, command: ReserveCommand) -> ReservationResult: ...

    def release_inventory_reservation(
        self, command: ReleaseReservationCommand
    ) -> ReservationResult: ...

    def transfer_inventory(self, command: TransferCommand) -> TransferResult: ...

    def post_inventory_count(self, command: CountCommand) -> CountResult: ...
