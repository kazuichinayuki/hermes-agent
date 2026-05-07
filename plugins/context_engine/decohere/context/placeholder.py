"""Placeholder assembly. Pure function — no I/O."""

from __future__ import annotations

from ..types import MechanicalFields


def build_placeholder(
    turn_n: int,
    message_range: tuple[int, int],
    mechanical: MechanicalFields,
    skipped: bool,
) -> dict:
    """Build a turn placeholder dict. Returns NEW dict, no mutations.

    If skipped: compact placeholder (4 fields + entry_skipped).
    If not skipped: full placeholder with all semantic fields = None.
    """
    placeholder: dict = {
        "n": turn_n,
        "message_range": list(message_range),
        "entry_skipped": skipped,
        "tools": [
            {"name": t["name"], "args_summary": t["args_summary"]}
            for t in mechanical.tools
        ],
        "files_touched": list(mechanical.files_touched),
    }

    if not skipped:
        placeholder.update({
            "reference_documentation": None,
            "relevant_metadata": None,
            "concepts_and_definitions": None,
            "narrative": None,
            "user_intent": None,
            "decisions_and_rationale": None,
            "procedures": None,
            "insights_and_learnings": None,
            "critical_reflection": None,
        })

    return placeholder
