"""Context message assembly. Pure functions — receive data, return messages."""

from __future__ import annotations

from .formatter import (
    format_proc_layer,
    format_spec_layer,
    format_structural_from_raw,
    format_raw_compressed,
    format_turn_index,
)
from ..core.indexer import build_turn_index, pick_turns_from_index


def build_spec_context(turns: list, max_turns: int) -> list:
    """Build exactly 2 messages: L1 Spec block + L2 Proc block.

    Skips derivation_skipped turns. Truncates to max_turns.
    Returns [] if no valid turns remain.
    """
    valid = [t for t in turns if not t.get("derivation_skipped")]
    if not valid:
        return []

    recent = valid[-max_turns:]

    spec_blocks = [format_spec_layer(t) for t in recent]
    proc_blocks = [format_proc_layer(t) for t in recent]

    l1 = "\n\n".join(spec_blocks)
    l2 = "\n\n".join(proc_blocks)

    return [
        {"role": "tool", "content": f"## Turn Specifications (Layer 1 — Spec)\n\n{l1}"},
        {"role": "user", "name": "turn_context", "content": f"## Turn Specifications (Layer 2 — Proc)\n\n{l2}"},
    ]


def build_fallback_context(
    turns: list,
    max_turns: int,
    last_turn_msgs: list,
) -> list:
    """Build context when latest spec not ready.

    Older turns: spec context. Latest turn: raw compressed fallback.
    """
    if len(turns) <= 1:
        return [
            {"role": "tool", "content": format_structural_from_raw(last_turn_msgs)},
            {"role": "user", "name": "turn_context", "content": format_raw_compressed(last_turn_msgs)},
        ]

    # Older turns (all except the last placeholder)
    older = [t for t in turns[:-1] if not t.get("derivation_skipped")]
    spec_msgs = build_spec_context(older, max_turns)

    # Latest turn: raw fallback
    spec_msgs.append(
        {"role": "tool", "content": format_structural_from_raw(last_turn_msgs)}
    )
    spec_msgs.append(
        {"role": "user", "name": "turn_context", "content": format_raw_compressed(last_turn_msgs)}
    )
    return spec_msgs


def build_raw_context(messages: list) -> list:
    """Legacy passthrough — return messages unchanged."""
    return messages


def build_indexed_context(turns: list, max_turns: int) -> list:
    """Build 3 messages: turn_index + selected L1 Spec + selected L2 Proc.

    Threshold: >20 turns → use index. ≤20 → delegate to build_spec_context.
    """
    index = build_turn_index(turns)
    if index is None:
        return build_spec_context(turns, max_turns)

    # For now, include recent turns. LLM-driven selection is future work.
    recent_ns = [t.get("n", 0) for t in turns[-max_turns:]]
    selected = pick_turns_from_index(index, recent_ns)
    selected_turns = [t for t in turns if t.get("n") in selected]

    spec_msgs = build_spec_context(selected_turns, max_turns)
    return [
        {"role": "tool", "content": format_turn_index(index)},
        *spec_msgs,
    ]
