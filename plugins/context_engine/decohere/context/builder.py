"""Context message assembly. Pure functions — receive data, return messages."""

from __future__ import annotations

from typing import Any

from .formatter import (
    format_proc_layer,
    format_entry_layer,
    format_structural_from_raw,
    format_raw_compressed,
    format_turn_index,
)
from ..core.indexer import build_turn_index, pick_turns_from_index


def build_ledger_context(turns: list[dict[str, object]], max_turns: int) -> list[dict[str, Any]]:
    """Build exactly 2 messages: L1 Entry block + L2 Proc block.

    Skips entry_skipped turns. Truncates to max_turns.
    Returns [] if no valid turns remain.
    """
    valid = [t for t in turns if not t.get("entry_skipped")]
    if not valid:
        return []

    recent = valid[-max_turns:]

    entry_blocks = [format_entry_layer(t) for t in recent]
    proc_blocks = [format_proc_layer(t) for t in recent]

    l1 = "\n\n".join(entry_blocks)
    l2 = "\n\n".join(proc_blocks)

    return [
        {"role": "user", "name": "ledger_l1", "content": f"## Ledger Entries (Layer 1 — Spec)\n\n{l1}"},
        {"role": "user", "name": "turn_context", "content": f"## Ledger Entries (Layer 2 — Proc)\n\n{l2}"},
    ]


def build_fallback_context(
    turns: list[dict[str, object]],
    max_turns: int,
    last_turn_msgs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build context when latest entry not ready.

    Older turns: ledger context. Latest turn: raw compressed fallback.
    """
    if len(turns) <= 1:
        return [
            {"role": "user", "name": "ledger_l1", "content": format_structural_from_raw(last_turn_msgs)},
            {"role": "user", "name": "turn_context", "content": format_raw_compressed(last_turn_msgs)},
        ]

    # Older turns (all except the last placeholder)
    older = [t for t in turns[:-1] if not t.get("entry_skipped")]
    entry_msgs = build_ledger_context(older, max_turns)

    # Latest turn: raw fallback
    entry_msgs.append(
        {"role": "user", "name": "ledger_l1", "content": format_structural_from_raw(last_turn_msgs)}
    )
    entry_msgs.append(
        {"role": "user", "name": "turn_context", "content": format_raw_compressed(last_turn_msgs)}
    )
    return entry_msgs


def build_raw_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Legacy passthrough — return messages unchanged."""
    return messages


def build_indexed_context(turns: list[dict[str, object]], max_turns: int) -> list[dict[str, Any]]:
    """Build 3 messages: turn_index + selected L1 Entry + selected L2 Proc.

    Threshold: >20 turns → use index. ≤20 → delegate to build_ledger_context.
    """
    index = build_turn_index(turns)
    if index is None:
        return build_ledger_context(turns, max_turns)

    # For now, include recent turns. LLM-driven selection is future work.
    recent_ns = [t.get("n", 0) for t in turns[-max_turns:]]
    selected = pick_turns_from_index(index, recent_ns)
    selected_turns = [t for t in turns if t.get("n") in selected]

    entry_msgs = build_ledger_context(selected_turns, max_turns)
    return [
        {"role": "user", "name": "turn_index", "content": format_turn_index(index)},
        *entry_msgs,
    ]
