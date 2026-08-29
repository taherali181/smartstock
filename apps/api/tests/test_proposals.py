from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from smartstock_api.domain.errors import ConcurrencyConflict
from smartstock_api.domain.proposals import (
    ActionProposal,
    InvalidProposalTransition,
    ProposalState,
)


def proposal() -> ActionProposal:
    return ActionProposal(
        id=uuid4(),
        organization_id=uuid4(),
        created_by=uuid4(),
        state=ProposalState.AWAITING_REVIEW,
        source_versions={"inventory-position:1": 4, "supplier:2": 7},
        command_payload={"type": "create_purchase_order", "quantity": "96"},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def test_approval_requires_exact_source_versions() -> None:
    draft = proposal()
    with pytest.raises(ConcurrencyConflict):
        draft.transition(
            ProposalState.APPROVED,
            organization_id=draft.organization_id,
            actor_id=uuid4(),
            expected_version=1,
            current_source_versions={"inventory-position:1": 5, "supplier:2": 7},
        )


def test_approved_proposal_is_still_not_executed() -> None:
    draft = proposal()
    approved = draft.transition(
        ProposalState.APPROVED,
        organization_id=draft.organization_id,
        actor_id=uuid4(),
        expected_version=1,
        current_source_versions=draft.source_versions,
    )

    assert approved.state == ProposalState.APPROVED
    assert approved.version == 2
    executing = approved.transition(
        ProposalState.EXECUTING,
        organization_id=approved.organization_id,
        actor_id=uuid4(),
        expected_version=2,
    )
    assert executing.state == ProposalState.EXECUTING


def test_terminal_proposals_cannot_be_reopened() -> None:
    item = proposal()
    rejected = item.transition(
        ProposalState.REJECTED,
        organization_id=item.organization_id,
        actor_id=uuid4(),
        expected_version=1,
    )
    with pytest.raises(InvalidProposalTransition):
        rejected.transition(
            ProposalState.VALIDATING,
            organization_id=rejected.organization_id,
            actor_id=uuid4(),
            expected_version=2,
        )
