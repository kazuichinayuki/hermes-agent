"""Format ledger entries into context blocks. Pure functions only."""

from __future__ import annotations

import os
from typing import Any


def sanitize_path(path: str) -> str:
    """Replace home dir with ~. Keep line range intact."""
    home = os.path.expanduser("~")
    if home != "/" and path.startswith(home):
        return "~" + path[len(home):]
    return path


def sanitize_text(text: str) -> str:
    """Replace all occurrences of home dir with ~ in arbitrary text.

    Unlike sanitize_path() which only handles prefix matching,
    this replaces ALL occurrences anywhere in the string.
    """
    home = os.path.expanduser("~")
    if home and home != "/":
        return text.replace(home, "~")
    return text


def _sanitize_str(s: object) -> str:
    """Sanitize a single string value. Returns empty string for non-strings."""
    if isinstance(s, str):
        return sanitize_text(s)
    return ""


def format_entry_layer(turn: dict[str, object]) -> str:
    """Format one turn's L1 Spec fields into a text block."""
    lines = [f"[Turn {turn.get('n', '?')}]"]
    lines.append(f"  message_range: {turn.get('message_range', [])}")
    lines.append(f"  tools: {_fmt_tools(turn.get('tools', []))}")
    lines.append(f"  files: {_fmt_files(turn.get('files_touched', []))}")
    meta = turn.get('relevant_metadata', {}) or {}
    lines.append(f"  task: {_sanitize_str(meta.get('task', ''))}")
    lines.append(f"  ref_class: {_sanitize_str(meta.get('reference_class', ''))}")
    return "\n".join(lines)


def format_proc_layer(turn: dict[str, object]) -> str:
    """Format one turn's L2 Proc fields into a text block."""
    lines = [f"[Turn {turn.get('n', '?')}]"]

    # Concepts
    concepts = turn.get("concepts_and_definitions", []) or []
    if concepts:
        lines.append("\nconcepts_and_definitions:")
        for c in concepts:
            if isinstance(c, dict):
                lines.append(f"  • {_sanitize_str(c.get('term', ''))}: {_sanitize_str(c.get('definition', ''))}")

    # Narrative
    narrative = turn.get("narrative", {}) or {}
    if narrative.get("summary"):
        lines.append(f"\nnarrative: {_sanitize_str(narrative['summary'])}")

    # Decisions
    decisions = turn.get("decisions_and_rationale", []) or []
    if decisions:
        lines.append("\ndecisions:")
        for d in decisions:
            if isinstance(d, dict):
                lines.append(f"  • {_sanitize_str(d.get('decision', ''))}")
                if d.get("rationale"):
                    lines.append(f"    → {_sanitize_str(d['rationale'])}")

    # Procedures
    procedures = turn.get("procedures", []) or []
    if procedures:
        lines.append("\nprocedures:")
        for p in procedures:
            if isinstance(p, dict):
                lines.append(f"  • {_sanitize_str(p.get('procedure', ''))}")

    # Insights
    insights = turn.get("insights_and_learnings", []) or []
    if insights:
        lines.append("\ninsights:")
        for i in insights:
            if isinstance(i, str):
                lines.append(f"  • {_sanitize_str(i)}")
            elif isinstance(i, dict):
                cat = _sanitize_str(i.get('category', 'General'))
                title = _sanitize_str(i.get('title', 'Observation'))
                content = _sanitize_str(i.get('content', ''))
                lines.append(f"  • [{cat}] {title}: {content}")

    # User intent
    intent = turn.get("user_intent", "")
    if intent:
        lines.append(f"\nuser_intent: {_sanitize_str(intent)}")

    # Critical reflection
    cr = turn.get("critical_reflection", {}) or {}
    
    blockers = cr.get("execution_blockers", []) or []
    if blockers:
        lines.append("\nexecution_blockers:")
        for b in blockers:
            if isinstance(b, str):
                lines.append(f"  • {_sanitize_str(b)}")
            elif isinstance(b, dict):
                cat = _sanitize_str(b.get('category', 'General'))
                title = _sanitize_str(b.get('title', 'Observation'))
                content = _sanitize_str(b.get('content', ''))
                lines.append(f"  • [{cat}] {title}: {content}")

    if cr.get("improvement_directions"):
        lines.append("\n↳ improvements:")
        for d in cr["improvement_directions"]:
            lines.append(f"  • {_sanitize_str(d)}")

    return "\n".join(lines)


def format_structural_from_raw(messages: list[dict[str, Any]]) -> str:
    """Fallback: mechanically extract structural info from raw messages."""
    tool_names = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []) or []:
                fn = tc.get("function", {})
                tool_names.add(fn.get("name", "?"))
    tools_str = ", ".join(sorted(tool_names)) if tool_names else "none"
    return f"[Turn ?]\n  tools: {tools_str}\n  source: raw fallback"


def format_raw_compressed(messages: list[dict[str, Any]]) -> str:
    """Fallback: user_msg verbatim + first 3 sentences + tool names."""
    user_msg = ""
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                user_msg = content
                break

    # First 3 sentences
    import re
    sentences = re.split(r"(?<=[.!?])\s+", user_msg)
    compressed = " ".join(sentences[:3])

    # Tool names
    tool_names = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []) or []:
                fn = tc.get("function", {})
                tool_names.add(fn.get("name", "?"))

    parts = [f"[Turn ?]"]
    if compressed:
        parts.append(f"  msg: {compressed}")
    if tool_names:
        parts.append(f"  tools: {', '.join(sorted(tool_names))}")
    parts.append("  source: raw compressed fallback")
    return "\n".join(parts)


def format_turn_index(index: dict[str, object]) -> str:
    """Format turn index for LLM navigation."""
    if not index:
        return "(no turns)"

    lines = ["## Turn Index\n"]
    for entry in index.get("entries", []):
        concepts = ", ".join(entry.get("key_concepts", [])[:3])
        tools = ", ".join(entry.get("tools_used", [])[:3])
        lines.append(
            f"[Turn {entry['n']}] {entry['title']}"
            f"  — {entry['summary_1line']}"
            f"  — tools: {tools}" if tools else ""
            f"  — concepts: {concepts}" if concepts else ""
        )
    return "\n".join(lines)


def _fmt_tools(tools: list) -> str:
    if not tools:
        return "none"
    names = [t.get("name", "?") for t in tools if isinstance(t, dict)]
    return ", ".join(names)


def _fmt_files(files: list) -> str:
    if not files:
        return "none"
    return ", ".join(sanitize_path(f) for f in files)
