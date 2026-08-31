"""Action proposal review and execution.

A proposal is inert. Approval re-checks permission, re-reads every source
version the proposal was built from, refuses stale evidence, and only then runs
the ordinary domain command.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from smartstock_api.api.auth import Principal, PrincipalDependency
from smartstock_api.conversations.reads import OperationalReads
from smartstock_api.domain.errors import ConcurrencyConflict
from smartstock_api.domain.proposals import ProposalState
from smartstock_api.proposals.builder import current_source_versions
from smartstock_api.proposals.executor import execute_purchase_proposal
from smartstock_api.proposals.store import ProposalStore, StoredProposal

router = APIRouter(prefix="/v1/action-proposals", tags=["action-proposals"])


class ProposalView(BaseModel):
    id: UUID
    command: str
    title: str
    state: str
    impact: list[str]
    payload: dict[str, Any]
    source_versions: dict[str, int]
    created_at: datetime
    expires_at: datetime
    expired: bool
    version: int
    result: dict[str, Any] | None = None
    failure: str | None = None


def _view(item: StoredProposal) -> ProposalView:
    return ProposalView(
        id=item.id,
        command=item.command,
        title=item.title,
        state=item.proposal.state.value,
        impact=list(item.impact),
        payload=dict(item.proposal.command_payload),
        source_versions=dict(item.proposal.source_versions),
        created_at=item.created_at,
        expires_at=item.proposal.expires_at,
        expired=datetime.now(UTC) >= item.proposal.expires_at,
        version=item.proposal.version,
        result=item.result,
        failure=item.failure,
    )


def store_for(request: Request) -> ProposalStore:
    state = request.app.state
    if not hasattr(state, "proposal_store"):
        state.proposal_store = ProposalStore()
    return state.proposal_store


def _reads(request: Request, principal: Principal) -> OperationalReads:
    state = request.app.state
    return OperationalReads(
        catalog=state.catalog_store,
        inventory=state.inventory_ledger,
        operations=state.operations_store,
        organization_id=principal.organization_id,
        actor_id=principal.user_id,
        port=getattr(state, "operational_read_port", None),
    )


def _load(request: Request, principal: Principal, proposal_id: UUID) -> StoredProposal:
    item = store_for(request).get(principal.organization_id, proposal_id)
    if item is None:
        raise HTTPException(status_code=404, detail="action proposal not found")
    return item


@router.get("", response_model=list[ProposalView])
def list_proposals(
    request: Request, principal: Principal = PrincipalDependency
) -> list[ProposalView]:
    principal.require("ai.use")
    return [_view(item) for item in store_for(request).list(principal.organization_id)]


@router.get("/{proposal_id}", response_model=ProposalView)
def get_proposal(
    proposal_id: UUID, request: Request, principal: Principal = PrincipalDependency
) -> ProposalView:
    principal.require("ai.use")
    return _view(_load(request, principal, proposal_id))


@router.post("/{proposal_id}/reject", response_model=ProposalView)
def reject_proposal(
    proposal_id: UUID, request: Request, principal: Principal = PrincipalDependency
) -> ProposalView:
    principal.require("purchasing.approve")
    item = _load(request, principal, proposal_id)
    rejected = item.proposal.transition(
        ProposalState.REJECTED,
        organization_id=principal.organization_id,
        actor_id=principal.user_id,
        expected_version=item.proposal.version,
    )
    return _view(store_for(request).update(item, proposal=rejected))


@router.post("/{proposal_id}/approve", response_model=ProposalView)
def approve_proposal(
    proposal_id: UUID, request: Request, principal: Principal = PrincipalDependency
) -> ProposalView:
    # Approval is a separate permission from proposing, and is checked here
    # rather than trusting anything recorded when the draft was created.
    principal.require("purchasing.approve")
    principal.require("purchasing.execute")

    store = store_for(request)
    item = _load(request, principal, proposal_id)
    reads = _reads(request, principal)

    # Re-read the world. If a price, product or supplier moved since the draft
    # was built, the evidence is stale and the command must not run.
    live_versions = current_source_versions(reads, dict(item.proposal.source_versions))
    approved = item.proposal.transition(
        ProposalState.APPROVED,
        organization_id=principal.organization_id,
        actor_id=principal.user_id,
        expected_version=item.proposal.version,
        current_source_versions=live_versions,
    )
    executing = approved.transition(
        ProposalState.EXECUTING,
        organization_id=principal.organization_id,
        actor_id=principal.user_id,
        expected_version=approved.version,
    )
    item = store.update(item, proposal=executing)

    try:
        result = execute_purchase_proposal(
            item,
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            operations_store=request.app.state.operations_store,
            correlation_id=UUID(str(request.state.correlation_id)),
        )
    except ConcurrencyConflict:
        raise
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        failed = executing.transition(
            ProposalState.FAILED,
            organization_id=principal.organization_id,
            actor_id=principal.user_id,
            expected_version=executing.version,
        )
        store.update(item, proposal=failed, failure=f"{type(exc).__name__}: {exc}")
        raise HTTPException(status_code=409, detail=f"execution failed: {exc}") from exc

    succeeded = executing.transition(
        ProposalState.SUCCEEDED,
        organization_id=principal.organization_id,
        actor_id=principal.user_id,
        expected_version=executing.version,
    )
    return _view(store.update(item, proposal=succeeded, result=result))
