"""Build lightweight turn index for LLM-driven selection.

Pure functions — zero I/O, zero side-effects.
Build an index first, then let the LLM choose which nodes to expand.
"""

from __future__ import annotations


def build_turn_index(turns: list[dict[str, object]]) -> dict[str, object] | None:
    """Build a lightweight turn index for LLM navigation.

    For ≤20 turns, returns None (use full spec context).
    For >20 turns, returns index dict with:
        entries: [{n, title, summary_1line, tools_used, key_concepts}]
        concept_map: {term: [turn_n, ...]}
        file_map: {path: [turn_n, ...]}
    """
    if len(turns) <= 20:
        return None

    entries: list = []
    concept_map: dict[str, list[int]] = {}
    file_map: dict[str, list[int]] = {}

    for turn in turns:
        n = turn.get("n", 0)
        narrative = turn.get("narrative", {}) or {}
        summary = narrative.get("summary", "")
        # First line or first 80 chars as title
        title = summary.split("\n")[0][:80] if summary else f"Turn {n}"
        summary_1line = summary[:120] if summary else ""

        tools = turn.get("tools", []) or []
        tools_used = [t.get("name", "") for t in tools if isinstance(t, dict)]

        concepts = turn.get("concepts_and_definitions", []) or []
        key_concepts = [
            c.get("term", "") for c in concepts if isinstance(c, dict)
        ]

        entries.append({
            "n": n,
            "title": title,
            "summary_1line": summary_1line,
            "tools_used": tools_used,
            "key_concepts": key_concepts,
            "files_touched": [str(f) for f in (turn.get("files_touched", []) or [])],
        })

        # Build concept_map
        for term in key_concepts:
            concept_map.setdefault(term, []).append(n)

        # Build file_map
        for path in turn.get("files_touched", []) or []:
            file_map.setdefault(path, []).append(n)

    return {
        "entries": entries,
        "concept_map": {k: tuple(v) for k, v in concept_map.items()},
        "file_map": {k: tuple(v) for k, v in file_map.items()},
    }


def pick_turns_from_index(index: dict[str, object], turn_ns: list[int]) -> list[int]:
    """Resolve concept + file references from selected turns.

    If LLM picks turn 3 (key_concepts: ["architecture"]), also include
    turns that share that concept via concept_map.
    """
    if not index:
        return list(turn_ns)

    concept_map = index.get("concept_map", {})
    file_map = index.get("file_map", {})

    expanded: set = set(turn_ns)

    for entry in index.get("entries", []):
        n = entry["n"]
        if n in turn_ns:
            # Follow concept links
            for term in entry.get("key_concepts", []):
                for linked_n in concept_map.get(term, ()):
                    expanded.add(linked_n)
            # Follow file links via files_touched in index
            for path in entry.get("files_touched", []):
                for linked_n in file_map.get(path, ()):
                    expanded.add(linked_n)

    return sorted(expanded)
