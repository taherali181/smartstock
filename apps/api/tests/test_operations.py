from decimal import Decimal
from uuid import uuid4

import pytest

from smartstock_api.domain.errors import IdempotencyConflict, InvalidStateTransition
from smartstock_api.domain.operations import (
    InMemoryOperationsStore,
    OperationalOrder,
    OrderKind,
    OrderLine,
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
