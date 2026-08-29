from decimal import Decimal
from uuid import uuid4

import pytest

from smartstock_api.domain.availability import Availability
from smartstock_api.domain.workflows import (
    InvalidWorkflowTransition,
    Workflow,
    WorkflowEntity,
)


@pytest.mark.parametrize(
    ("workflow", "states"),
    [
        (
            Workflow.PURCHASE_ORDER,
            [
                "draft",
                "pending_approval",
                "approved",
                "sent",
                "acknowledged",
                "partially_received",
                "received",
                "closed",
            ],
        ),
        (
            Workflow.SALES_ORDER,
            [
                "quote",
                "draft",
                "confirmed",
                "partially_allocated",
                "allocated",
                "picking",
                "partially_shipped",
                "shipped",
                "delivered",
                "closed",
            ],
        ),
        (
            Workflow.TRANSFER,
            ["draft", "approved", "picking", "shipped", "partially_received", "received", "closed"],
        ),
        (
            Workflow.CYCLE_COUNT,
            ["scheduled", "frozen", "counting", "review", "approved", "posted"],
        ),
        (
            Workflow.RETURN,
            ["requested", "authorized", "received", "inspected", "refund", "closed"],
        ),
        (
            Workflow.SHIPMENT,
            ["planned", "picking", "packed", "labelled", "shipped", "delivered"],
        ),
    ],
)
def test_primary_workflow_paths_are_executable(workflow, states) -> None:
    organization_id = uuid4()
    entity = WorkflowEntity(uuid4(), organization_id, workflow, states[0])
    for target in states[1:]:
        entity = entity.transition(
            target, organization_id=organization_id, expected_version=entity.version
        )
    assert entity.state == states[-1]
    assert entity.version == len(states)


def test_workflow_cannot_skip_command_states() -> None:
    organization_id = uuid4()
    order = WorkflowEntity(uuid4(), organization_id, Workflow.PURCHASE_ORDER, "draft")
    with pytest.raises(InvalidWorkflowTransition):
        order.transition("received", organization_id=organization_id, expected_version=1)


def test_inventory_formulas_keep_reserved_and_committed_distinct() -> None:
    values = Availability(
        sellable_on_hand=Decimal("100"),
        reserved=Decimal("30"),
        committed=Decimal("50"),
        eligible_incoming=Decimal("25"),
        safety_stock=Decimal("10"),
    )
    assert values.available == Decimal("70")
    assert values.unreserved_committed == Decimal("20")
    assert values.backordered == Decimal("0")
    assert values.atp == Decimal("65")
