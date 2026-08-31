"""Tenant-scoped proposal storage.

In process for this slice. The durable table arrives with the Phase 4 schema;
what must not change is that a proposal is inert until approved, that approval
revalidates the versions it was built from, and that execution runs the same
command the manual UI runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import UUID

from smartstock_api.domain.proposals import ActionProposal


@dataclass(frozen=True, slots=True)
class StoredProposal:
    proposal: ActionProposal
    command: str
    title: str
    impact: tuple[str, ...]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    result: dict[str, Any] | None = None
    failure: str | None = None

    @property
    def id(self) -> UUID:
        return self.proposal.id


class ProposalStore:
    """Never returns a proposal belonging to another organization."""

    def __init__(self) -> None:
        self._items: dict[tuple[UUID, UUID], StoredProposal] = {}
        self._lock = Lock()

    def add(self, item: StoredProposal) -> StoredProposal:
        with self._lock:
            self._items[(item.proposal.organization_id, item.id)] = item
        return item

    def get(self, organization_id: UUID, proposal_id: UUID) -> StoredProposal | None:
        with self._lock:
            return self._items.get((organization_id, proposal_id))

    def list(self, organization_id: UUID) -> list[StoredProposal]:
        with self._lock:
            items = [
                item for (org, _), item in self._items.items() if org == organization_id
            ]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def update(
        self,
        item: StoredProposal,
        *,
        proposal: ActionProposal | None = None,
        result: dict[str, Any] | None = None,
        failure: str | None = None,
    ) -> StoredProposal:
        updated = replace(
            item,
            proposal=proposal or item.proposal,
            result=result if result is not None else item.result,
            failure=failure if failure is not None else item.failure,
        )
        with self._lock:
            self._items[(updated.proposal.organization_id, updated.id)] = updated
        return updated
