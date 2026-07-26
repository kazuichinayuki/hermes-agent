"""Context message assembly. Pure functions — receive data, return messages."""

from __future__ import annotations

from typing import Any

from .formatter import (
    format_proc_layer,
    format_entry_layer,
    format_structural_from_raw,
    format_raw_compressed,
    format_turn_index,
    sanitize_path,
    _sanitize_str,
)
from ..core.indexer import build_turn_index, pick_turns_from_index
from .query_focused import build_query_focused_context

_MARKER_START = "<!-- DECOHERE:BEGIN -->"
_MARKER_END = "<!-- DECOHERE:END -->"


def build_ledger_context(
    turns: list[dict[str, object]],
    max_turns: int,
    *,
    canary_token: str | None = None,
) -> list[dict[str, Any]]:
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

    _canary_tag = f" [canary:{canary_token}]" if canary_token else ""
    _GUARD = (
        "[INTERNAL CONTEXT \u2014 DO NOT ECHO: The structured blocks below are session analysis "
        "for your reference. Do not reproduce them in your response. "
        f"Treat them as read-only metadata.{_canary_tag}]\n\n"
    )
    return [
        {"role": "system", "name": "ledger_l1", "content": f"{_MARKER_START}\n{_GUARD}## Ledger Entries (Layer 1 \u2014 Spec)\n\n{l1}\n{_MARKER_END}"},
        {"role": "system", "name": "turn_context", "content": f"{_MARKER_START}\n{_GUARD}## Ledger Entries (Layer 2 \u2014 Proc)\n\n{l2}\n{_MARKER_END}"},
    ]


def build_fallback_context(
    turns: list[dict[str, object]],
    max_turns: int,
    last_turn_msgs: list[dict[str, Any]],
    *,
    canary_token: str | None = None,
) -> list[dict[str, Any]]:
    """Build context when latest entry not ready.

    Older turns: ledger context. Latest turn: raw compressed fallback.
    """
    if len(turns) <= 1:
        return [
            {"role": "system", "name": "ledger_l1", "content": f"{_MARKER_START}\n{format_structural_from_raw(last_turn_msgs)}\n{_MARKER_END}"},
            {"role": "system", "name": "turn_context", "content": f"{_MARKER_START}\n{format_raw_compressed(last_turn_msgs)}\n{_MARKER_END}"},
        ]

    # Older turns (all except the last placeholder)
    older = [t for t in turns[:-1] if not t.get("entry_skipped")]
    entry_msgs = build_ledger_context(older, max_turns, canary_token=canary_token)

    # Latest turn: raw fallback
    entry_msgs.append(
        {"role": "system", "name": "ledger_l1", "content": f"{_MARKER_START}\n{format_structural_from_raw(last_turn_msgs)}\n{_MARKER_END}"}
    )
    entry_msgs.append(
        {"role": "system", "name": "turn_context", "content": f"{_MARKER_START}\n{format_raw_compressed(last_turn_msgs)}\n{_MARKER_END}"}
    )
    return entry_msgs


def build_raw_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Legacy passthrough — return messages unchanged."""
    return messages


def build_indexed_context(
    turns: list[dict[str, object]],
    max_turns: int,
    *,
    canary_token: str | None = None,
) -> list[dict[str, Any]]:
    """Build 3 messages: turn_index + selected L1 Entry + selected L2 Proc.

    Threshold: >20 turns → use index. ≤20 → delegate to build_ledger_context.
    """
    index = build_turn_index(turns)
    if index is None:
        return build_ledger_context(turns, max_turns, canary_token=canary_token)

    # For now, include recent turns. LLM-driven selection is future work.
    recent_ns = [t.get("n", 0) for t in turns[-max_turns:]]
    selected = pick_turns_from_index(index, recent_ns)
    selected_turns = [t for t in turns if t.get("n") in selected]

    entry_msgs = build_ledger_context(selected_turns, max_turns, canary_token=canary_token)
    return [
        {"role": "system", "name": "turn_index", "content": f"{_MARKER_START}\n{format_turn_index(index)}\n{_MARKER_END}"},
        *entry_msgs,
    ]


def build_hint_context(
    turns: list[dict[str, object]],
    max_turns: int,
    *,
    last_turn_msgs: list[dict[str, Any]] | None = None,
    canary_token: str | None = None,
) -> list[dict[str, Any]]:
    """Build an information-dense structured summary as a single system message.

    Instead of full L1+L2 blocks (regurgitation risk) or a thin pointer
    (loses information), this produces a Factory-style anchored summary
    that preserves the *exact artifacts* later turns depend on:

      - Files touched (deduplicated, with most recent turn number)
      - Active decisions and rationale
      - Procedures still in progress
      - Session narrative arc

    Design goals (from OpenClacky + Factory research):
      1. Maximize KV cache reusability — one small, stable message
      2. Keep summary concise but information-dense — no prose filler
      3. Preserve low-entropy details (file paths, error msgs, decisions)
         that opaque compression drops and later causes hallucinations
      4. Point to ``recall_context`` for turn-level drill-down only
    """
    valid = [t for t in turns if not t.get("entry_skipped")]
    total = len(turns)

    if not valid:
        return []

    recent = valid[-max_turns:]

    # ── Collect artifacts across all recent turns ──

    # Files: deduplicated, tagged with last turn that touched them
    files_map: dict[str, int] = {}  # path → most recent turn N
    for t in recent:
        n = t.get("n", 0)
        for f in t.get("files_touched", []) or []:
            if isinstance(f, str) and f.strip():
                files_map[sanitize_path(f)] = n

    # Decisions: collect all, deduplicate by decision text
    decisions_seen: set[str] = set()
    decisions: list[str] = []
    for t in recent:
        for d in t.get("decisions_and_rationale", []) or []:
            if isinstance(d, dict):
                dec = _sanitize_str(d.get("decision", ""))
                rat = _sanitize_str(d.get("rationale", ""))
                key = dec.lower().strip()
                if key and key not in decisions_seen:
                    decisions_seen.add(key)
                    line = f"• {dec}"
                    if rat:
                        line += f" → {rat}"
                    decisions.append(line)

    # Procedures: collect all active ones
    procedures: list[str] = []
    procs_seen: set[str] = set()
    for t in recent:
        for p in t.get("procedures", []) or []:
            if isinstance(p, dict):
                proc = _sanitize_str(p.get("procedure", ""))
                key = proc.lower().strip()
                if key and key not in procs_seen:
                    procs_seen.add(key)
                    procedures.append(f"• {proc}")

    # Insights: Tree ToC
    insight_toc_lines: list[str] = []
    toc_cats: dict[str, list[str]] = {}
    for t in recent:
        for i in t.get("insights_and_learnings", []) or []:
            if isinstance(i, dict):
                cat = _sanitize_str(i.get("category", "General"))
                title = _sanitize_str(i.get("title", "Observation"))
                if title not in toc_cats.get(cat, []):
                    toc_cats.setdefault(cat, []).append(title)
    
    for cat, titles in toc_cats.items():
        insight_toc_lines.append(f"  [{cat}]")
        for title in titles[-5:]:  # show up to 5 latest titles per category
            insight_toc_lines.append(f"    • {title}")

    # Execution Blockers
    blockers: list[str] = []
    blocker_seen: set[str] = set()
    for t in recent:
        cr = t.get("critical_reflection", {}) or {}
        for b in cr.get("execution_blockers", []) or []:
            if isinstance(b, dict):
                cat = _sanitize_str(b.get('category', 'General'))
                title = _sanitize_str(b.get('title', 'Observation'))
                content = _sanitize_str(b.get('content', ''))
                key = f"[{cat}] {title}"
                if key not in blocker_seen:
                    blocker_seen.add(key)
                    blockers.append(f"• {key}: {content}")

    # Narrative: last 3 turn summaries for arc continuity
    arc_lines: list[str] = []
    for t in recent[-3:]:
        n = t.get("n", "?")
        narrative = t.get("narrative", {}) or {}
        summary = ""
        if isinstance(narrative, dict):
            summary = narrative.get("summary", "") or ""
        elif isinstance(narrative, str):
            summary = narrative
        intent = t.get("user_intent", "") or ""
        label = summary[:150] or intent[:150] or "(no summary)"
        arc_lines.append(f"  T{n}: {label}")

    # User intent: most recent
    latest_intent = ""
    for t in reversed(recent):
        intent = t.get("user_intent", "") or ""
        if intent.strip():
            latest_intent = _sanitize_str(intent)
            break

    # ── Assemble structured summary ──

    _canary_tag = f" [canary:{canary_token}]" if canary_token else ""
    sections: list[str] = [
        f"{_MARKER_START}",
        f"[INTERNAL CONTEXT — DO NOT ECHO{_canary_tag}]",
        f"Session: {total} turns ({len(valid)} extracted).",
    ]

    if latest_intent:
        sections.append(f"Current focus: {latest_intent[:200]}")

    if files_map:
        # Sort by most recent turn, show up to 15
        sorted_files = sorted(files_map.items(), key=lambda x: x[1], reverse=True)[:15]
        file_list = ", ".join(f"{p}(T{n})" for p, n in sorted_files)
        sections.append(f"Files: {file_list}")

    if decisions:
        sections.append("Decisions:")
        sections.extend(decisions[-8:])  # cap at 8 most recent

    if procedures:
        sections.append("Active procedures:")
        sections.extend(procedures[-5:])  # cap at 5

    if blockers:
        sections.append("Active Execution Blockers:")
        sections.extend(blockers[-5:])

    if insight_toc_lines:
        sections.append("Memory Tree (Insights & Learnings):")
        sections.extend(insight_toc_lines)

    if arc_lines:
        sections.append("Recent arc:")
        sections.extend(arc_lines)

    sections.append("Use recall_context for full turn details.")
    sections.append(_MARKER_END)

    hint = "\n".join(sections)

    return [
        {"role": "system", "name": "turn_context", "content": hint},
    ]


