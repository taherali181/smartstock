"""Safety guards for the conversation layer.

Two rules this module exists to enforce:

1. Text is data. Neither user input nor anything retrieved from a record can
   change instructions, widen permissions, or cause a tool to be invoked that
   the model did not legitimately select from the registry.
2. Numbers are never authored. Anything numeric shown to a user must arrive
   from a tool result and carry a citation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MAX_MESSAGE_CHARS = 4000
MAX_ARG_CHARS = 200

# Patterns that indicate an attempt to steer the system rather than ask a
# question. Matching does not abort the turn; it strips authority from the text
# and records a warning, because false positives must not deny service.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above)\b",
        r"\bdisregard\s+(all\s+|any\s+|the\s+)?(previous|prior|above|instructions)\b",
        r"\b(you\s+are\s+now|act\s+as|pretend\s+to\s+be)\b",
        r"\b(system|developer)\s*(prompt|message|instructions)\b",
        r"\breveal\b.{0,30}\b(prompt|instructions|credentials|secret|token)\b",
        r"\b(grant|give)\s+(me\s+)?(admin|owner|all)\s+(access|permission|rights)\b",
        r"\b(an)?other\s+(organi[sz]ation|tenant|customer|company|account)s?('s)?\b",
        r"\bexecute\s+(this\s+)?(sql|command)\b|\bdrop\s+table\b",
    )
)


@dataclass(frozen=True, slots=True)
class InputAssessment:
    text: str
    injection_suspected: bool
    matched: tuple[str, ...]


def assess_input(raw: str) -> InputAssessment:
    """Normalise user text and flag steering attempts. Never raises."""
    text = (raw or "").strip()[:MAX_MESSAGE_CHARS]
    matched = tuple(
        pattern.pattern for pattern in _INJECTION_PATTERNS if pattern.search(text)
    )
    return InputAssessment(text=text, injection_suspected=bool(matched), matched=matched)


def sanitize_arguments(
    declared: dict[str, Any], supplied: Any
) -> tuple[dict[str, Any], list[str]]:
    """Keep only declared parameters, coerce to declared types, bound lengths.

    A model may propose any arguments it likes. Only the ones this tool declares
    survive, so a hallucinated or injected parameter cannot reach a store.
    """
    properties: dict[str, Any] = declared.get("properties", {})
    clean: dict[str, Any] = {}
    dropped: list[str] = []

    if not isinstance(supplied, dict):
        return clean, ["arguments were not an object"]

    for name, value in supplied.items():
        spec = properties.get(name)
        if spec is None:
            dropped.append(name)
            continue
        expected = spec.get("type")
        if expected == "string":
            if not isinstance(value, (str, int, float)):
                dropped.append(name)
                continue
            clean[name] = str(value)[:MAX_ARG_CHARS]
        elif expected == "number":
            try:
                clean[name] = float(value)
            except (TypeError, ValueError):
                dropped.append(name)
        else:
            clean[name] = value
    return clean, dropped


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def ground_arguments(
    arguments: dict[str, Any], question: str, declared: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Drop string arguments the user never wrote.

    A model is free to choose which tool answers a question. It is not free to
    supply the identifier. Left unchecked, granite3.1-moe will happily answer
    "which purchase orders are approved?" with order_number="PO-12345", which
    silently filters a correct query down to nothing. Comparison ignores case
    and punctuation so that "sku 1017" still grounds "SKU-1017".
    """
    properties: dict[str, Any] = declared.get("properties", {})
    haystack = _normalise(question)
    grounded: dict[str, Any] = {}
    ungrounded: list[str] = []

    for name, value in arguments.items():
        spec = properties.get(name, {})
        if spec.get("type") == "string" and isinstance(value, str) and value.strip():
            if _normalise(value) not in haystack:
                ungrounded.append(name)
                continue
        grounded[name] = value
    return grounded, ungrounded


def citations_cover(rows: list[dict[str, Any]], citations: list[Any]) -> bool:
    """A table of records may only be shown when something authorises it."""
    return not rows or bool(citations)
