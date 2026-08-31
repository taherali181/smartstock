"""Typed conversation blocks streamed over SSE.

The block vocabulary is fixed by ORIGINAL_PLAN.md section 4. Every block a
client can receive is constructed here, so the transport can never invent a
shape the contract does not define.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID


class BlockType(StrEnum):
    ANSWER_TEXT = "answer_text"
    RECORD_SUMMARY = "record_summary"
    FORECAST_SUMMARY = "forecast_summary"
    RECOMMENDATION = "recommendation"
    CITATION = "citation"
    ACTION_PROPOSAL = "action_proposal"
    CLARIFICATION = "clarification"
    WARNING = "warning"
    ERROR = "error"
    COMPLETED = "completed"


def decimal_text(value: Decimal) -> str:
    """Exact decimal text without trailing-zero noise.

    The ledger stores nine decimal places, so a quantity of 31 arrives as
    31.000000000. Trailing zeros are removed for legibility; no rounding
    happens, and a genuinely fractional value keeps every significant digit.
    `normalize()` is not used because it renders 31 as 3.1E+1.
    """
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        # Money and quantities stay exact: serialised as strings, never floats.
        return decimal_text(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    # datetime is a subclass of date, so this must come second. An order's
    # expected_on is a plain date and previously raised here mid-stream.
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    raise TypeError(f"unserialisable value of type {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class Citation:
    """A pointer to an authorised record. Every displayed number needs one."""

    record_type: str
    record_id: str
    label: str
    version: int | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_payload(self) -> dict[str, Any]:
        return {
            "record_type": self.record_type,
            "record_id": self.record_id,
            "label": self.label,
            "version": self.version,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True, slots=True)
class Block:
    type: BlockType
    payload: dict[str, Any]

    def to_sse(self) -> str:
        body = json.dumps(
            {"type": self.type.value, **self.payload},
            default=_json_default,
            separators=(",", ":"),
        )
        return f"event: {self.type.value}\ndata: {body}\n\n"


def answer_text(text: str) -> Block:
    return Block(BlockType.ANSWER_TEXT, {"text": text})


def record_summary(
    title: str,
    rows: list[dict[str, Any]],
    *,
    columns: list[str] | None = None,
    tool: str | None = None,
) -> Block:
    return Block(
        BlockType.RECORD_SUMMARY,
        {
            "title": title,
            "columns": columns or (list(rows[0].keys()) if rows else []),
            "rows": rows,
            "row_count": len(rows),
            "tool": tool,
        },
    )


def citation(item: Citation) -> Block:
    return Block(BlockType.CITATION, item.to_payload())


def recommendation(text: str, *, rationale: str | None = None) -> Block:
    return Block(BlockType.RECOMMENDATION, {"text": text, "rationale": rationale})


def action_proposal(
    proposal_id: UUID,
    command: str,
    payload: dict[str, Any],
    impact: list[str],
    source_versions: dict[str, int],
    expires_at: datetime,
) -> Block:
    return Block(
        BlockType.ACTION_PROPOSAL,
        {
            "proposal_id": proposal_id,
            "command": command,
            "payload": payload,
            "impact": impact,
            "source_versions": source_versions,
            "expires_at": expires_at,
            "state": "awaiting_review",
            "executed": False,
        },
    )


def clarification(question: str, *, options: list[str] | None = None) -> Block:
    return Block(BlockType.CLARIFICATION, {"question": question, "options": options or []})


def warning(message: str, *, code: str | None = None) -> Block:
    return Block(BlockType.WARNING, {"message": message, "code": code})


def error(message: str, *, code: str = "conversation_failed") -> Block:
    return Block(BlockType.ERROR, {"message": message, "code": code})


def completed(
    *,
    model_profile: str,
    model_revision: str,
    route: str,
    tool_versions: dict[str, str],
    prompt_version: str,
    citation_count: int,
    abstained: bool,
    latency_ms: int,
) -> Block:
    """Terminal block. Carries the provenance the contract requires us to persist."""
    return Block(
        BlockType.COMPLETED,
        {
            "model_profile": model_profile,
            "model_revision": model_revision,
            "route": route,
            "tool_versions": tool_versions,
            "prompt_version": prompt_version,
            "citation_count": citation_count,
            "abstained": abstained,
            "latency_ms": latency_ms,
        },
    )
