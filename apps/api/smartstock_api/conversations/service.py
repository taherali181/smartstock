"""Conversation orchestration.

One turn runs: assess input, plan tool calls, execute them, compose blocks.
The model influences only step two. Steps three and four are deterministic, so
what a user is shown is always a faithful rendering of authorised records.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from smartstock_api.conversations import blocks as B
from smartstock_api.conversations import guards
from smartstock_api.conversations.models import (
    DETERMINISTIC_PROFILE,
    PROMPT_VERSION,
    ModelProfile,
    OllamaRoute,
    ToolCall,
    deterministic_plan,
    known_tool_calls,
)
from smartstock_api.conversations.reads import OperationalReads, ReadUnavailable
from smartstock_api.conversations.tools import REGISTRY, Tool, ToolResult, versions

class ProposalRefused(Exception):
    """A write was understood but cannot be drafted; the reason is shown as-is."""


CAPABILITIES = (
    "inventory levels for a SKU or warehouse",
    "what is running low",
    "purchase orders and receiving",
    "sales orders, allocation and shipping",
    "the warehouse task queue",
    "product catalog lookups",
    "drafting a purchase order for approval",
)


@dataclass(slots=True)
class TurnOutcome:
    profile: ModelProfile
    tool_calls: list[ToolCall] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)
    citations: int = 0
    abstained: bool = False


class ConversationService:
    def __init__(
        self,
        reads: OperationalReads,
        route: OllamaRoute | None,
        tools: dict[str, Tool] | None = None,
        *,
        patterns_first: bool = True,
        lead_in: bool = False,
        proposer: Callable[[str], Any] | None = None,
    ) -> None:
        self._reads = reads
        self._route = route
        self._tools = dict(REGISTRY) if tools is None else tools
        self._patterns_first = patterns_first
        self._lead_in_enabled = lead_in
        # Injected so the service never imports the HTTP or storage layer.
        # Returns a StoredProposal, or raises ProposalRefused with a reason.
        self._proposer = proposer

    # -- planning ----------------------------------------------------------

    def _plan(self, question: str) -> tuple[list[ToolCall], ModelProfile, str | None]:
        """Ask the model to pick tools; fall back to patterns on any failure."""
        if self._patterns_first:
            # Common phrasings resolve in microseconds and never need a model.
            matched = self._patterns(question)
            if matched:
                return matched, DETERMINISTIC_PROFILE, None
        if self._route is None:
            return self._patterns(question), DETERMINISTIC_PROFILE, None
        try:
            calls = known_tool_calls(self._route.plan(question, self._tools), self._tools)
        except (httpx.HTTPError, ValueError) as exc:
            fallback = self._patterns(question)
            return fallback, DETERMINISTIC_PROFILE, f"model route unavailable ({exc.__class__.__name__}); used the deterministic router"
        if calls:
            return calls, self._route.profile, None
        # The model declined to call a tool. Patterns get the last word rather
        # than abstaining outright, because a miss here is usually phrasing.
        fallback = self._patterns(question)
        if fallback:
            return fallback, DETERMINISTIC_PROFILE, None
        return [], self._route.profile, None

    def _patterns(self, question: str) -> list[ToolCall]:
        """Pattern routing, filtered to the tools this principal may use."""
        return known_tool_calls(deterministic_plan(question), self._tools)

    # -- execution ---------------------------------------------------------

    def _execute(
        self, calls: list[ToolCall], question: str
    ) -> tuple[list[ToolResult], list[str]]:
        results: list[ToolResult] = []
        warnings: list[str] = []
        for call in calls[:3]:
            tool = self._tools.get(call.name)
            if tool is None:
                continue
            arguments, dropped = guards.sanitize_arguments(tool.parameters, call.arguments)
            if dropped:
                warnings.append(
                    f"{tool.name}: ignored undeclared argument(s) {', '.join(sorted(dropped))}"
                )
            arguments, ungrounded = guards.ground_arguments(
                arguments, question, tool.parameters
            )
            if ungrounded:
                warnings.append(
                    f"{tool.name}: ignored {', '.join(sorted(ungrounded))} because "
                    "the value did not appear in your question"
                )
            try:
                results.append(tool.run(self._reads, arguments))
            except ReadUnavailable as exc:
                warnings.append(str(exc))
            except Exception as exc:  # noqa: BLE001 - one tool must not fail the turn
                warnings.append(f"{tool.name} could not complete: {exc.__class__.__name__}")
        return results, warnings

    # -- streaming ---------------------------------------------------------

    def stream(self, question: str) -> Iterator[B.Block]:
        started = time.monotonic()
        assessment = guards.assess_input(question)
        outcome = TurnOutcome(profile=DETERMINISTIC_PROFILE)

        if not assessment.text:
            yield B.clarification("What would you like to know?", options=list(CAPABILITIES))
            yield self._completed(outcome, started, abstained=True)
            return

        if assessment.injection_suspected:
            # Treated as data, answered normally. Nothing about the request is
            # allowed to change tools, scope or permissions.
            yield B.warning(
                "This message contained instruction-like text. It was treated as a "
                "question only; permissions and tools are unchanged.",
                code="injection_suspected",
            )

        # A request to change something becomes an inert draft, never an action.
        if self._proposer is not None:
            try:
                drafted = self._proposer(assessment.text)
            except ProposalRefused as refusal:
                yield B.answer_text(str(refusal))
                outcome.abstained = True
                yield self._completed(outcome, started, abstained=True)
                return
            if drafted is not None:
                yield B.answer_text(
                    "I have prepared this as a draft. Nothing has changed yet, and it "
                    "will only run once an authorised approver reviews it."
                )
                yield B.action_proposal(
                    proposal_id=drafted.id,
                    command=drafted.command,
                    payload=dict(drafted.proposal.command_payload),
                    impact=list(drafted.impact),
                    source_versions=dict(drafted.proposal.source_versions),
                    expires_at=drafted.proposal.expires_at,
                )
                yield self._completed(outcome, started, abstained=False)
                return

        calls, profile, route_note = self._plan(assessment.text)
        outcome.profile = profile
        outcome.tool_calls = calls
        if route_note:
            yield B.warning(route_note, code="model_route_degraded")

        if not calls:
            outcome.abstained = True
            yield B.clarification(
                "I could not match that to an operational lookup. I can answer about:",
                options=list(CAPABILITIES),
            )
            yield self._completed(outcome, started, abstained=True)
            return

        results, warnings = self._execute(calls, assessment.text)
        outcome.results = results
        for message in warnings:
            yield B.warning(message, code="tool_degraded")

        if not results:
            outcome.abstained = True
            yield B.answer_text(
                "I could not read the records needed to answer that. Nothing has been guessed."
            )
            yield self._completed(outcome, started, abstained=True)
            return

        populated = [result for result in results if not result.is_empty]

        if not populated:
            for result in results:
                if result.empty_reason:
                    yield B.answer_text(result.empty_reason)
            outcome.abstained = True
            yield self._completed(outcome, started, abstained=True)
            return

        lead_in = self._lead_in(assessment.text, populated, profile)
        if lead_in:
            yield B.answer_text(lead_in)

        for result in populated:
            if not guards.citations_cover(result.rows, result.citations):
                yield B.warning(
                    f"{result.tool} returned rows without citations and was withheld.",
                    code="uncited_result",
                )
                continue
            yield B.answer_text(result.summary)
            yield B.record_summary(result.title, result.rows, tool=result.tool)
            for item in result.citations[:25]:
                yield B.citation(item)
                outcome.citations += 1

        yield self._completed(outcome, started, abstained=False)

    def _lead_in(
        self, question: str, results: list[ToolResult], profile: ModelProfile
    ) -> str | None:
        if not self._lead_in_enabled or self._route is None or profile.route != "ollama":
            return None
        try:
            return self._route.lead_in(question, results)
        except (httpx.HTTPError, ValueError):
            return None

    def _completed(self, outcome: TurnOutcome, started: float, *, abstained: bool) -> B.Block:
        return B.completed(
            model_profile=outcome.profile.name,
            model_revision=outcome.profile.revision,
            route=outcome.profile.route,
            tool_versions=versions(self._tools),
            prompt_version=PROMPT_VERSION,
            citation_count=outcome.citations,
            abstained=abstained,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def sse_stream(service: ConversationService, question: str) -> Iterator[str]:
    """Adapt the block stream to the SSE wire format, never leaking a traceback."""
    try:
        for block in service.stream(question):
            yield block.to_sse()
    except Exception as exc:  # noqa: BLE001
        yield B.error(
            f"The conversation failed: {exc.__class__.__name__}", code="conversation_failed"
        ).to_sse()
