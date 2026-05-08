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
