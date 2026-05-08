"""Health check logic. Pure functions — zero I/O."""

from __future__ import annotations

from .snapshots import CompressSnapshot, PersistCheck, RangeCheck
from ..types import Readiness


def check_message_range(actual_start: int, expected_start: int) -> RangeCheck:
    """Verify message_range start matches raw_store count."""
    delta = actual_start - expected_start
    ok = (delta == 0)
    return RangeCheck(ok=ok, expected=expected_start, actual=actual_start, delta=delta)


def check_persisted_turn(turns: tuple[dict[str, object], ...], expected_n: int) -> PersistCheck:
    """Verify placeholder was actually written to store."""
    found = any(t.get("n") == expected_n for t in turns)
    return PersistCheck(ok=found, turn_n=expected_n, found=found)


def classify_compress(readiness: Readiness) -> CompressSnapshot:
    """Classify which compression branch was taken + estimate token count."""
    branch_map = {
        "ready": "spec",
        "pending": "fallback",
        "legacy": "legacy",
        "empty": "empty",
    }
    branch = branch_map.get(readiness.state, "legacy")
    # Rough estimate: ~400 tokens per turn
    estimated = len(readiness.turns) * 400

    return CompressSnapshot(
        readiness=readiness.state,
        turn_count=len(readiness.turns),
        pending_turn_n=readiness.pending_turn_n,
        branch=branch,
        estimated_tokens=estimated,
    )
