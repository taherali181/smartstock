from decimal import Decimal
from uuid import uuid4

import pytest

from smartstock_api.domain.errors import IdempotencyConflict, InvalidStateTransition
from smartstock_api.domain.inventory import (
    AdjustmentCommand,
    InventoryLedger,
    StockCondition,
    StockKey,
)
from smartstock_api.domain.operations import (
    AllocationPostingLine,
    InMemoryOperationsStore,
    OperationalOrder,
    OrderKind,
    OrderLine,
    ReceiptPostingLine,
    ReturnAuthorization,
    ReturnLine,
    ReturnReceiptLine,
    ShipmentPostingLine,
    WarehouseTask,
    WarehouseTaskState,
    WarehouseTaskType,
)
from smartstock_api.domain.workflows import InvalidWorkflowTransition


def purchase_order(organization_id):
    return OperationalOrder(
        id=uuid4(),
        organization_id=organization_id,
        kind=OrderKind.PURCHASE,
        order_number="PO-1001",
        party_id=uuid4(),
        warehouse_id=uuid4(),
        state="draft",
        lines=(
            OrderLine(
                uuid4(), uuid4(), Decimal("12.5"), "ea", Decimal("4.25"), "USD"
            ),
        ),
        currency="USD",
    )


def test_purchase_order_commands_are_versioned_and_idempotent() -> None:
    store = InMemoryOperationsStore()
    organization_id, actor_id, correlation_id = uuid4(), uuid4(), uuid4()
    order = purchase_order(organization_id)

    created, replayed = store.create_order(
        order, actor_id, correlation_id, "create-po-1001"
    )
    assert replayed is False
    assert created.total == Decimal("53.125")
    assert store.create_order(
        order, actor_id, correlation_id, "create-po-1001"
    )[1] is True

    submitted, replayed = store.transition_order(
        organization_id,
        actor_id,
        OrderKind.PURCHASE,
        order.id,
        "pending_approval",
        1,
        correlation_id,
        "submit-po-1001",
    )
    assert submitted.state == "pending_approval"
    assert submitted.version == 2
    assert replayed is False
    assert store.transition_order(
        organization_id,
        actor_id,
        OrderKind.PURCHASE,
        order.id,
        "pending_approval",
        1,
        correlation_id,
        "submit-po-1001",
    )[1] is True

    with pytest.raises(InvalidWorkflowTransition):
        store.transition_order(
            organization_id,
            actor_id,
            OrderKind.PURCHASE,
            order.id,
            "received",
            2,
            correlation_id,
            "skip-po-receiving",
        )

    with pytest.raises(IdempotencyConflict):
        store.transition_order(
            organization_id,
            actor_id,
            OrderKind.PURCHASE,
            order.id,
            "cancelled",
            2,
            correlation_id,
            "submit-po-1001",
        )


def test_warehouse_task_assignment_execution_and_exception_recovery() -> None:
    store = InMemoryOperationsStore()
    organization_id, actor_id, correlation_id = uuid4(), uuid4(), uuid4()
    task = WarehouseTask(
        id=uuid4(),
        organization_id=organization_id,
        task_number="PICK-1001",
        task_type=WarehouseTaskType.PICK,
        warehouse_id=uuid4(),
        product_id=uuid4(),
        quantity=Decimal("2.5"),
        uom="ea",
        priority=10,
    )
    store.create_task(task, actor_id, correlation_id, "create-pick-1001")

    with pytest.raises(InvalidStateTransition):
        store.transition_task(
            organization_id,
            actor_id,
            task.id,
            WarehouseTaskState.ASSIGNED,
            1,
            correlation_id,
            "assign-without-user",
        )

    assigned, _ = store.transition_task(
        organization_id,
        actor_id,
        task.id,
        WarehouseTaskState.ASSIGNED,
        1,
        correlation_id,
        "assign-pick-1001",
        actor_id,
    )
    started, _ = store.transition_task(
        organization_id,
        actor_id,
        task.id,
        WarehouseTaskState.IN_PROGRESS,
        assigned.version,
        correlation_id,
        "start-pick-1001",
    )
    exception, _ = store.transition_task(
        organization_id,
        actor_id,
        task.id,
        WarehouseTaskState.EXCEPTION,
        started.version,
        correlation_id,
        "exception-pick-1001",
    )
    reopened, _ = store.transition_task(
        organization_id,
        actor_id,
        task.id,
        WarehouseTaskState.OPEN,
        exception.version,
        correlation_id,
        "reopen-pick-1001",
    )
    assert reopened.state == WarehouseTaskState.OPEN
    assert reopened.assigned_to == actor_id


def test_acknowledged_purchase_order_generates_receiving_task_once() -> None:
    store = InMemoryOperationsStore()
    organization_id, actor_id, correlation_id = uuid4(), uuid4(), uuid4()
    order = purchase_order(organization_id)
    store.create_order(order, actor_id, correlation_id, "create-auto-task-po")
    version = 1
    for target in ("pending_approval", "approved", "sent", "acknowledged"):
        transitioned, _ = store.transition_order(
            organization_id,
            actor_id,
            OrderKind.PURCHASE,
            order.id,
            target,
            version,
            correlation_id,
            f"po-{target}",
        )
        version = transitioned.version
    tasks = store.tasks_for(organization_id, actor_id, order.warehouse_id)
    assert len(tasks) == 1
    assert tasks[0].task_type == WarehouseTaskType.RECEIVE
    assert tasks[0].reference_id == order.id


def test_purchase_receipt_posts_inventory_and_creates_putaway_work() -> None:
    inventory = InventoryLedger()
    store = InMemoryOperationsStore(inventory)
    organization_id, actor_id, correlation_id = uuid4(), uuid4(), uuid4()
    order = purchase_order(organization_id)
    location_id = uuid4()
    store.create_order(order, actor_id, correlation_id, "create-receivable-po")
    version = 1
    for target in ("pending_approval", "approved", "sent", "acknowledged"):
        transitioned, _ = store.transition_order(
            organization_id,
            actor_id,
            OrderKind.PURCHASE,
            order.id,
            target,
            version,
            correlation_id,
            f"receipt-po-{target}",
        )
        version = transitioned.version

    receipt_id = uuid4()
    posting = ReceiptPostingLine(
        uuid4(), order.lines[0].id, location_id, Decimal("8"), Decimal("1")
    )
    result = store.post_receipt(
        organization_id,
        actor_id,
        order.id,
        receipt_id,
        "RCPT-1001",
        (posting,),
        version,
        Decimal("0"),
        correlation_id,
        "post-receipt-1001",
    )
    assert result.replayed is False
    assert result.order.state == "partially_received"
    assert result.order.lines[0].received_or_shipped_quantity == Decimal("9")
    assert len(result.receipt.inventory_transaction_ids) == 2

    sellable = inventory.position(
        StockKey(
            organization_id,
            order.lines[0].product_id,
            order.warehouse_id,
            location_id,
            "ea",
        )
    )
    quarantined = inventory.position(
        StockKey(
            organization_id,
            order.lines[0].product_id,
            order.warehouse_id,
            location_id,
            "ea",
            condition=StockCondition.QUARANTINED,
        )
    )
    assert sellable.on_hand == Decimal("8")
    assert quarantined.on_hand == Decimal("1")
    assert all(item.reconciled for item in inventory.reconcile(organization_id, actor_id))
    assert any(
        task.task_type == WarehouseTaskType.PUTAWAY and task.reference_id == receipt_id
        for task in store.tasks_for(organization_id, actor_id, order.warehouse_id)
    )

    replay = store.post_receipt(
        organization_id,
        actor_id,
        order.id,
        receipt_id,
        "RCPT-1001",
        (posting,),
        version,
        Decimal("0"),
        correlation_id,
        "post-receipt-1001",
    )
    assert replay.replayed is True
    assert inventory.position(sellable.key).on_hand == Decimal("8")

    final_posting = ReceiptPostingLine(
        uuid4(), order.lines[0].id, location_id, Decimal("4"), Decimal("0"), 1, 1
    )
    completed = store.post_receipt(
        organization_id,
        actor_id,
        order.id,
        uuid4(),
        "RCPT-1002",
        (final_posting,),
        result.order.version,
        Decimal("10"),
        correlation_id,
        "post-receipt-1002",
    )
    assert completed.order.state == "received"
    assert completed.order.lines[0].received_or_shipped_quantity == Decimal("13")
    assert completed.order.lines[0].open_quantity == Decimal("0")


def test_sales_allocation_reserves_stock_and_generates_pick_work() -> None:
    inventory = InventoryLedger()
    store = InMemoryOperationsStore(inventory)
    organization_id, actor_id, correlation_id = uuid4(), uuid4(), uuid4()
    warehouse_id, first_location, second_location, product_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    order = OperationalOrder(
        id=uuid4(), organization_id=organization_id, kind=OrderKind.SALES,
        order_number="SO-1001", party_id=uuid4(), warehouse_id=warehouse_id,
        state="quote",
        lines=(OrderLine(uuid4(), product_id, Decimal("12.5"), "ea", Decimal("9"), "USD"),),
        currency="USD",
    )
    for index, location_id in enumerate((first_location, second_location), start=1):
        inventory.adjust(
            AdjustmentCommand(
                organization_id, actor_id,
                StockKey(organization_id, product_id, warehouse_id, location_id, "ea"),
                Decimal("8"), "opening_stock", "SO-1001", f"opening-{index}",
                correlation_id, 0, unit_cost=Decimal("4"), currency="USD",
            )
        )
    store.create_order(order, actor_id, correlation_id, "create-so-1001")
    version = 1
    for target in ("draft", "confirmed"):
        transitioned, _ = store.transition_order(
            organization_id, actor_id, OrderKind.SALES, order.id, target, version,
            correlation_id, f"so-{target}",
        )
        version = transitioned.version
    first_id = uuid4()
    first_line = AllocationPostingLine(
        uuid4(), order.lines[0].id, first_location, Decimal("5"), 1
    )
    partial = store.allocate_sales_order(
        organization_id, actor_id, order.id, first_id, (first_line,), version,
        correlation_id, "allocate-so-1001-a",
    )
    assert partial.order.state == "partially_allocated"
    assert inventory.position(
        StockKey(organization_id, product_id, warehouse_id, first_location, "ea")
    ).reserved == Decimal("5")
    replay = store.allocate_sales_order(
        organization_id, actor_id, order.id, first_id, (first_line,), version,
        correlation_id, "allocate-so-1001-a",
    )
    assert replay.replayed is True

    complete = store.allocate_sales_order(
        organization_id, actor_id, order.id, uuid4(),
        (AllocationPostingLine(
            uuid4(), order.lines[0].id, second_location, Decimal("7.5"), 1
        ),),
        partial.order.version, correlation_id, "allocate-so-1001-b",
    )
    assert complete.order.state == "allocated"
    assert all(item.reconciled for item in inventory.reconcile(organization_id, actor_id))
    pick_tasks = [
        task for task in store.tasks_for(organization_id, actor_id, warehouse_id)
        if task.task_type == WarehouseTaskType.PICK
    ]
    assert len(pick_tasks) == 2
    assert sum((task.quantity or Decimal("0") for task in pick_tasks), Decimal("0")) == Decimal(
        "12.5"
    )
    picking, _ = store.transition_order(
        organization_id, actor_id, OrderKind.SALES, order.id, "picking",
        complete.order.version, correlation_id, "start-picking-so-1001",
    )
    first_shipment = store.post_shipment(
        organization_id, actor_id, order.id, uuid4(),
        (ShipmentPostingLine(
            uuid4(), order.lines[0].id, partial.allocation.reservation_ids[0], 1, 2
        ),), picking.version, correlation_id, "ship-so-1001-a",
    )
    assert first_shipment.order.state == "partially_shipped"
    final_shipment = store.post_shipment(
        organization_id, actor_id, order.id, uuid4(),
        (ShipmentPostingLine(
            uuid4(), order.lines[0].id, complete.allocation.reservation_ids[0], 1, 2
        ),), first_shipment.order.version, correlation_id, "ship-so-1001-b",
    )
    assert final_shipment.order.state == "shipped"
    assert final_shipment.order.lines[0].open_quantity == Decimal("0")
    assert all(item.reconciled for item in inventory.reconcile(organization_id, actor_id))
    return_id = uuid4()
    rma = ReturnAuthorization(
        return_id, organization_id, "RMA-1001", order.id, warehouse_id, "requested",
        (ReturnLine(uuid4(), order.lines[0].id, product_id, Decimal("5"), "ea", "damaged"),),
    )
    store.create_return(rma, actor_id, correlation_id, "create-rma-1001")
    authorized, _ = store.transition_return(
        organization_id, actor_id, return_id, "authorized", 1,
        correlation_id, "authorize-rma-1001",
    )
    received = store.receive_return(
        organization_id, actor_id, return_id,
        (ReturnReceiptLine(rma.lines[0].id, first_location, 0),),
        authorized.version, correlation_id, "receive-rma-1001",
    )
    assert received.return_authorization.state == "received"
    assert inventory.position(StockKey(
        organization_id, product_id, warehouse_id, first_location, "ea",
        condition=StockCondition.QUARANTINED,
    )).on_hand == Decimal("5")
    inspected, _ = store.transition_return(
        organization_id, actor_id, return_id, "inspected",
        received.return_authorization.version, correlation_id, "inspect-rma-1001",
    )
    refunded, _ = store.transition_return(
        organization_id, actor_id, return_id, "refund", inspected.version,
        correlation_id, "refund-rma-1001",
    )
    closed, _ = store.transition_return(
        organization_id, actor_id, return_id, "closed", refunded.version,
        correlation_id, "close-rma-1001",
    )
    assert closed.state == "closed"
