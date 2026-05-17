"""Build shared knowledge context messages for LLM injection.

Reads user config, filters concepts from SharedStore, and produces
a message block to be inserted into the conversation context.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .shared_store import SharedStore


def build_injection_message(
    store: SharedStore,
    knowledge_injection: bool,
    knowledge_sources: list[dict],
    knowledge_exclude: list[str],
    max_concepts: int = 20,
    max_tokens_pct: float = 0.10,
    embed_fn: Callable | None = None,
    user_intent: str = "",
) -> dict[str, str] | None:
    """Build the '## Shared Knowledge' message block.

    Returns a message dict (role="user", name="shared_knowledge") or None
    if injection is disabled or no concepts match.

    Parameters:
        store: Shared concept database
        knowledge_injection: Master toggle from config
        knowledge_sources: [{"session": "abc", "turns": [3,5]}, ...]
        knowledge_exclude: ["context window", "compression.*"]
        max_concepts: Hard cap on concept count
        max_tokens_pct: Fraction of context window budget (unused currently)
        embed_fn: Optional embedding function for semantic retrieval
        user_intent: Current turn user intent for relevance matching
    """
    if not knowledge_injection:
        return None

    # Collect all concepts
    all_concepts = store.get_all()
    if not all_concepts:
        return None

    concepts: list[dict[str, Any]] = []

    # Filter by knowledge_sources
    if knowledge_sources:
        allowed_sessions: set[str] = set()
        allowed_turns: dict[str, set[int]] = {}
        for src in knowledge_sources:
            sid = src.get("session", "")
            turns = src.get("turns", [])
            if sid:
                allowed_sessions.add(sid)
                if turns:
                    allowed_turns.setdefault(sid, set()).update(turns)

        for c in all_concepts:
            if c["source_session"] not in allowed_sessions:
                continue
            if c["source_session"] in allowed_turns:
                allowed_for_session = allowed_turns[c["source_session"]]
                if allowed_for_session and c["source_turn"] not in allowed_for_session:
                    continue
            concepts.append(c)
    else:
        concepts = all_concepts

    # Filter by knowledge_exclude
    if knowledge_exclude:
        patterns = []
        for pat in knowledge_exclude:
            try:
                patterns.append(re.compile(pat, re.IGNORECASE))
            except re.error:
                pass  # Skip invalid patterns
        if patterns:
            concepts = [
                c for c in concepts
                if not any(p.search(c["term"]) for p in patterns)
            ]

    if not concepts:
        return None

    # Cap
    concepts = concepts[:max_concepts]

    # Build message
    lines = [
        "## Shared Knowledge (from past sessions)",
        "",
        "Concepts from previous sessions, imported by user selection:",
        "",
    ]
    for c in concepts:
        session_short = c["source_session"][:12] if c["source_session"] else "?"
        lines.append(
            f"• **{c['term']}**: {c['definition']} "
            f"[session {session_short}, turn {c['source_turn']}]"
        )

    # Summary footer
    unique_sessions = len(set(c["source_session"] for c in concepts))
    lines.append("")
    lines.append(
        f"Source: {unique_sessions} session(s), "
        f"{len(concepts)} concept(s). Imported by user configuration."
    )

    return {
        "role": "user",
        "name": "shared_knowledge",
        "content": "\n".join(lines),
    }
