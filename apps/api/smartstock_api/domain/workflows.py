from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID

from .errors import ConcurrencyConflict, DomainError, TenantBoundaryViolation


class InvalidWorkflowTransition(DomainError):
    code = "invalid_workflow_transition"
    status_code = 409


class Workflow(StrEnum):
    PURCHASE_ORDER = "purchase_order"
    SALES_ORDER = "sales_order"
    TRANSFER = "transfer"
    CYCLE_COUNT = "cycle_count"
    RETURN = "return"
    SHIPMENT = "shipment"


WORKFLOWS: dict[Workflow, dict[str, frozenset[str]]] = {
    Workflow.PURCHASE_ORDER: {
        "draft": frozenset({"pending_approval", "cancelled"}),
        "pending_approval": frozenset({"approved", "draft", "cancelled"}),
        "approved": frozenset({"sent", "cancelled"}),
        "sent": frozenset({"acknowledged", "cancelled"}),
        "acknowledged": frozenset({"partially_received", "received", "cancelled"}),
        "partially_received": frozenset({"partially_received", "received", "supplier_return"}),
        "received": frozenset({"closed", "supplier_return"}),
        "supplier_return": frozenset({"closed"}),
        "closed": frozenset(),
        "cancelled": frozenset(),
    },
    Workflow.SALES_ORDER: {
        "quote": frozenset({"draft", "cancelled"}),
        "draft": frozenset({"confirmed", "cancelled"}),
        "confirmed": frozenset(
            {"partially_allocated", "allocated", "backordered", "dropship", "cancelled"}
        ),
        "partially_allocated": frozenset(
            {"partially_allocated", "allocated", "backordered", "cancelled"}
        ),
        "allocated": frozenset({"picking", "cancelled"}),
        "backordered": frozenset({"partially_allocated", "allocated", "cancelled"}),
        "dropship": frozenset({"shipped", "cancelled"}),
        "picking": frozenset({"partially_shipped", "shipped"}),
        "partially_shipped": frozenset({"picking", "shipped"}),
        "shipped": frozenset({"delivered"}),
        "delivered": frozenset({"closed"}),
        "closed": frozenset(),
        "cancelled": frozenset(),
    },
    Workflow.TRANSFER: {
        "draft": frozenset({"approved", "cancelled"}),
        "approved": frozenset({"picking", "cancelled"}),
        "picking": frozenset({"shipped", "cancelled"}),
        "shipped": frozenset({"partially_received", "received"}),
        "partially_received": frozenset({"received", "discrepancy_review"}),
        "received": frozenset({"discrepancy_review", "closed"}),
        "discrepancy_review": frozenset({"closed"}),
        "closed": frozenset(),
        "cancelled": frozenset(),
    },
    Workflow.CYCLE_COUNT: {
        "scheduled": frozenset({"frozen", "cancelled"}),
        "frozen": frozenset({"counting", "cancelled"}),
        "counting": frozenset({"review", "cancelled"}),
        "review": frozenset({"counting", "approved", "cancelled"}),
        "approved": frozenset({"posted"}),
        "posted": frozenset(),
        "cancelled": frozenset(),
    },
    Workflow.RETURN: {
        "requested": frozenset({"authorized", "rejected"}),
        "authorized": frozenset({"received", "cancelled"}),
        "received": frozenset({"inspected"}),
        "inspected": frozenset({"refund", "replacement", "credit"}),
        "refund": frozenset({"closed"}),
        "replacement": frozenset({"closed"}),
        "credit": frozenset({"closed"}),
        "closed": frozenset(),
        "rejected": frozenset(),
        "cancelled": frozenset(),
    },
    Workflow.SHIPMENT: {
        "planned": frozenset({"picking", "void"}),
        "picking": frozenset({"packed", "exception", "void"}),
        "packed": frozenset({"labelled", "exception", "void"}),
        "labelled": frozenset({"shipped", "exception", "void"}),
        "exception": frozenset({"picking", "packed", "labelled", "void"}),
        "shipped": frozenset({"delivered"}),
        "delivered": frozenset(),
        "void": frozenset(),
    },
}


@dataclass(frozen=True, slots=True)
class WorkflowEntity:
    id: UUID
    organization_id: UUID
    workflow: Workflow
    state: str
    version: int = 1

    def transition(
        self, target: str, *, organization_id: UUID, expected_version: int
    ) -> "WorkflowEntity":
        if organization_id != self.organization_id:
            raise TenantBoundaryViolation("workflow entity belongs to a different organization")
        if expected_version != self.version:
            raise ConcurrencyConflict("workflow entity version changed")
        allowed = WORKFLOWS[self.workflow].get(self.state)
        if allowed is None or target not in allowed:
            raise InvalidWorkflowTransition(
                f"cannot transition {self.workflow.value} from {self.state} to {target}"
            )
        return replace(self, state=target, version=self.version + 1)
