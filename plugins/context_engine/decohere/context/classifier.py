"""Classification logic. Pure functions — no I/O, no side-effects."""

from __future__ import annotations

from ..types import Readiness
from typing import Any


def should_skip_entry(messages: list[dict[str, Any]]) -> bool:
    """True if turn has ≤3 messages and no tool_calls."""
    if len(messages) <= 3:
        has_tool_calls = any(
            msg.get("role") == "assistant" and msg.get("tool_calls")
            for msg in messages
        )
        return not has_tool_calls
    return False


def check_readiness(turns: list[dict[str, object]] | None, turn_count: int) -> Readiness:
    """Determine context readiness state.

    States:
    - "ready": all specs complete (no pending)
    - "pending": latest spec has critical_reflection = None (placeholder)
    - "legacy": turns is None or empty (format_version < 2)
    - "empty": no turns yet (turn_count == 0)
    """
    if turns is None:
        return Readiness(state="legacy", turns=(), pending_turn_n=None)

    if turn_count == 0:
        return Readiness(state="empty", turns=(), pending_turn_n=None)

    # Check if latest turn is still a placeholder
    if turns:
        latest = turns[-1]
        if latest.get("critical_reflection") is None:
            return Readiness(
                state="pending", turns=tuple(turns), pending_turn_n=latest.get("n")
            )

    return Readiness(state="ready", turns=tuple(turns), pending_turn_n=None)


import re

_HIGH_TIER_KEYWORDS = re.compile(
    r"\b(?:why|remember|earlier|previous|before|decision|rationale|history|recap|recall|reason|what was|how did we|past|former)\b",
    re.IGNORECASE,
)

_MEDIUM_TIER_KEYWORDS = re.compile(
    r"\b(?:continue|next|procedure|step|progress|status|blocker|issue|error|file|modified)\b",
    re.IGNORECASE,
)


def classify_context_budget(
    user_query: str,
    turn_count: int = 0,
    llm_fn: Any | None = None,
) -> BudgetTier:
    """Classify the memory context requirement into a BudgetTier (LOW, MEDIUM, HIGH).

    Context-aware routing: evaluates contextual dependency rather than task type.
    Uses LLM pre-check if provided, falling back to a deterministic heuristic guard.
    """
    from ..types import BudgetTier

    if not user_query or turn_count <= 1:
        return BudgetTier.LOW

    # 1. Attempt LLM Pre-check if callable provided
    if callable(llm_fn):
        try:
            res = llm_fn(user_query)
            if isinstance(res, str):
                cleaned = res.strip().upper()
                if "HIGH" in cleaned:
                    return BudgetTier.HIGH
                if "MEDIUM" in cleaned:
                    return BudgetTier.MEDIUM
                if "LOW" in cleaned:
                    return BudgetTier.LOW
        except Exception:
            pass  # Fall back to heuristic guard

    # 2. Deterministic Heuristic Guard
    query_str = user_query.strip()

    if _HIGH_TIER_KEYWORDS.search(query_str):
        return BudgetTier.HIGH

    if _MEDIUM_TIER_KEYWORDS.search(query_str) or len(query_str) > 150:
        return BudgetTier.MEDIUM

    return BudgetTier.LOW

