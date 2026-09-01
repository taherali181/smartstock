"""Conversation endpoints.

Answers stream as typed SSE blocks. Conversation history is held in process for
this development slice; durable persistence arrives with the Phase 4 document
and provenance schema, and the block contract does not change when it does.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from smartstock_api.api.auth import Principal, PrincipalDependency
from smartstock_api.conversations.models import OllamaRoute
from smartstock_api.conversations.reads import OperationalReads
from smartstock_api.conversations.service import (
    CAPABILITIES,
    ConversationService,
    ProposalRefused,
    sse_stream,
)
from smartstock_api.conversations.tools import allowed_tools
from smartstock_api.proposals.builder import (
    ProposalNotPossible,
    build_purchase_proposal,
    detect_purchase_intent,
)
from smartstock_api.api.routes.proposals import store_for
from smartstock_api.config import get_settings

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])

_MAX_HISTORY = 50
_history: dict[tuple[UUID, UUID], deque[dict[str, Any]]] = defaultdict(
    lambda: deque(maxlen=_MAX_HISTORY)
)


class ConversationResponse(BaseModel):
    id: UUID
    created_at: datetime


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    client_message_id: str | None = Field(default=None, max_length=128)
    scope: str | None = Field(default=None, max_length=64)


class CapabilityResponse(BaseModel):
    tools: list[str]
    answers: list[str]
    route: str
    model: str


def _reads(request: Request, principal: Principal) -> OperationalReads:
    state = request.app.state
    return OperationalReads(
        catalog=state.catalog_store,
        inventory=state.inventory_ledger,
        operations=state.operations_store,
        organization_id=principal.organization_id,
        actor_id=principal.user_id,
        # Used when the core lane registers an OperationalReadPort; until then
        # the concrete stores above answer and filtering happens in memory.
        port=getattr(state, "operational_read_port", None),
    )


def _route(request: Request) -> OllamaRoute | None:
    """The configured model route, or None when the deterministic router is used.

    Health is checked once per process and cached: a cold or absent model must
    not add its timeout to every question.
    """
    settings = get_settings()
    if settings.llm_route == "deterministic":
        return None
    # Never reach for a model from the test environment. The health probe and
    # planning call are bounded but not instant, and a busy or absent model
    # would otherwise stall a unit test rather than fail it.
    if settings.environment == "test":
        return None
    state = request.app.state
    cached = getattr(state, "llm_route_instance", "unset")
    if cached != "unset":
        return cached
    candidate = OllamaRoute(
        endpoint=settings.ollama_endpoint,
        model=settings.ollama_model,
        timeout=settings.llm_timeout_seconds,
        keep_alive=settings.llm_keep_alive,
    )
    resolved = candidate if candidate.healthy() else None
    state.llm_route_instance = resolved
    return resolved


@router.post("", response_model=ConversationResponse, status_code=201)
def create_conversation(
    principal: Principal = PrincipalDependency,
) -> ConversationResponse:
    principal.require("ai.use")
    conversation_id = uuid4()
    _history[(principal.organization_id, conversation_id)].clear()
    return ConversationResponse(id=conversation_id, created_at=datetime.now(UTC))


@router.get("/capabilities", response_model=CapabilityResponse)
def capabilities(
    request: Request, principal: Principal = PrincipalDependency
) -> CapabilityResponse:
    principal.require("ai.use")
    settings = get_settings()
    route = _route(request)
    return CapabilityResponse(
        tools=sorted(allowed_tools(principal.permissions)),
        answers=list(CAPABILITIES),
        route=settings.llm_route if route else "deterministic",
        model=settings.ollama_model if route else "deterministic-router",
    )


@router.get("/{conversation_id}")
def conversation_history(
    conversation_id: UUID, principal: Principal = PrincipalDependency
) -> dict[str, Any]:
    principal.require("ai.use")
    key = (principal.organization_id, conversation_id)
    if key not in _history:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"id": conversation_id, "messages": list(_history[key])}


@router.post("/{conversation_id}/messages")
def post_message(
    conversation_id: UUID,
    body: MessageRequest,
    request: Request,
    principal: Principal = PrincipalDependency,
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> StreamingResponse:
    principal.require("ai.use")

    tools = allowed_tools(principal.permissions)
    reads = _reads(request, principal)

    def propose(question: str):
        intent = detect_purchase_intent(question)
        if intent is None:
            return None
        if "purchasing.propose" not in principal.permissions and "*" not in principal.permissions:
            raise ProposalRefused(
                "You do not have permission to propose purchasing actions, so I have "
                "not drafted anything."
            )
        try:
            drafted = build_purchase_proposal(
                reads, intent,
                organization_id=principal.organization_id,
                actor_id=principal.user_id,
            )
        except ProposalNotPossible as exc:
            raise ProposalRefused(str(exc)) from exc
        return store_for(request).add(drafted)

    settings = get_settings()
    service = ConversationService(
        reads=reads,
        route=_route(request),
        tools=tools,
        patterns_first=settings.llm_route == "hybrid",
        lead_in=settings.llm_lead_in,
        proposer=propose,
    )

    key = (principal.organization_id, conversation_id)
    _history[key].append(
        {
            "role": "user",
            "content": body.content,
            "client_message_id": body.client_message_id,
            "at": datetime.now(UTC).isoformat(),
        }
    )

    def generate():
        collected: list[str] = []
        for chunk in sse_stream(service, body.content):
            collected.append(chunk)
            yield chunk
        _history[key].append(
            {
                "role": "assistant",
                "blocks": len(collected),
                "at": datetime.now(UTC).isoformat(),
            }
        )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
