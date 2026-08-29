from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from .errors import ConcurrencyConflict, DomainError, TenantBoundaryViolation


class InvalidProposalTransition(DomainError):
    code = "invalid_proposal_transition"
    status_code = 409


class ProposalState(StrEnum):
    DRAFT = "draft"
    VALIDATING = "validating"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


ALLOWED_TRANSITIONS: dict[ProposalState, frozenset[ProposalState]] = {
    ProposalState.DRAFT: frozenset({ProposalState.VALIDATING, ProposalState.EXPIRED}),
    ProposalState.VALIDATING: frozenset(
        {ProposalState.AWAITING_REVIEW, ProposalState.FAILED, ProposalState.EXPIRED}
    ),
    ProposalState.AWAITING_REVIEW: frozenset(
        {ProposalState.APPROVED, ProposalState.REJECTED, ProposalState.EXPIRED}
    ),
    ProposalState.APPROVED: frozenset({ProposalState.EXECUTING, ProposalState.EXPIRED}),
    ProposalState.EXECUTING: frozenset({ProposalState.SUCCEEDED, ProposalState.FAILED}),
    ProposalState.REJECTED: frozenset(),
    ProposalState.EXPIRED: frozenset(),
    ProposalState.SUCCEEDED: frozenset(),
    ProposalState.FAILED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ActionProposal:
    id: UUID
    organization_id: UUID
    created_by: UUID
    state: ProposalState
    source_versions: dict[str, int]
    command_payload: dict[str, object]
    expires_at: datetime
    version: int = 1
    reviewed_by: UUID | None = None

    def transition(
        self,
        target: ProposalState,
        *,
        organization_id: UUID,
        actor_id: UUID,
        expected_version: int,
        now: datetime | None = None,
        current_source_versions: dict[str, int] | None = None,
    ) -> "ActionProposal":
        now = now or datetime.now(UTC)
        if organization_id != self.organization_id:
            raise TenantBoundaryViolation("proposal belongs to a different organization")
        if expected_version != self.version:
            raise ConcurrencyConflict("proposal version changed")
        if now >= self.expires_at and target != ProposalState.EXPIRED:
            raise InvalidProposalTransition("proposal has expired")
        if target not in ALLOWED_TRANSITIONS[self.state]:
            raise InvalidProposalTransition(f"cannot transition {self.state} to {target}")
        if target == ProposalState.APPROVED:
            if current_source_versions is None:
                raise InvalidProposalTransition("approval requires source revalidation")
            if current_source_versions != self.source_versions:
                raise ConcurrencyConflict("proposal evidence is stale")
            return replace(self, state=target, version=self.version + 1, reviewed_by=actor_id)
        return replace(self, state=target, version=self.version + 1)
