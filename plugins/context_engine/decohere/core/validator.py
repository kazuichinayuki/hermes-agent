"""Validate and repair ledger entries. Pure functions only."""

from __future__ import annotations

_L1_DEFAULTS: dict[str, object] = {
    "reference_documentation": (),
    "relevant_metadata": {"task": "", "reference_class": ""},
}

_L2_DEFAULTS: dict[str, object] = {
    "concepts_and_definitions": (),
    "narrative": {"summary": "", "cross_references": ()},
    "user_intent": "",
    "decisions_and_rationale": (),
    "procedures": (),
    "insights_and_learnings": (),
    "critical_reflection": {
        "ignored_perspectives": (),
        "logical_gaps": (),
        "improvement_directions": (),
    },
}


def validate_entry(raw: dict[str, object]) -> dict[str, object]:
    """Repair a ledger entry to meet schema guarantees. Returns NEW dict.

    Guarantees: all 9 fields present, insights flat strings,
    critical_reflection sub-fields present, user_intent is string,
    relevant_metadata has task + reference_class only.
    """
    repaired: dict = {}

    # L1
    repaired["reference_documentation"] = tuple(
        raw.get("reference_documentation", ()) or ()
    )
    rm = raw.get("relevant_metadata", {}) or {}
    existing_intent = raw.get("user_intent", "")
    cleaned_rm, merged_intent = _migrate_stale_user_intent(rm, existing_intent)
    repaired["relevant_metadata"] = {
        "task": cleaned_rm.get("task", ""),
        "reference_class": cleaned_rm.get("reference_class", ""),
    }

    # L2
    repaired["concepts_and_definitions"] = tuple(
        raw.get("concepts_and_definitions", ()) or ()
    )
    repaired["narrative"] = {
        "summary": (raw.get("narrative", {}) or {}).get("summary", ""),
        "cross_references": tuple(
            (raw.get("narrative", {}) or {}).get("cross_references", ()) or ()
        ),
    }
    repaired["user_intent"] = str(merged_intent or "")
    repaired["decisions_and_rationale"] = tuple(
        raw.get("decisions_and_rationale", ()) or ()
    )
    repaired["procedures"] = tuple(raw.get("procedures", ()) or ())
    repaired["insights_and_learnings"] = _parse_categorized_nodes(
        raw.get("insights_and_learnings", ())
    )

    cr = raw.get("critical_reflection", {}) or {}
    repaired["critical_reflection"] = {
        "ignored_perspectives": tuple(cr.get("ignored_perspectives", ()) or ()),
        "logical_gaps": tuple(cr.get("logical_gaps", ()) or ()),
        "tool_failures": tuple(cr.get("tool_failures", ()) or ()),
        "execution_blockers": _parse_categorized_nodes(cr.get("execution_blockers", ())),
        "improvement_directions": tuple(cr.get("improvement_directions", ()) or ()),
    }

    return repaired


def _parse_categorized_nodes(raw: object) -> tuple[dict[str, str], ...]:
    """Ensure nodes follow the category/title/content schema."""
    if not raw:
        return ()
    if not isinstance(raw, (list, tuple)):
        return ({"category": "General", "title": "Insight", "content": str(raw)},)

    result: list = []
    for item in raw:
        if isinstance(item, str):
            result.append({"category": "General", "title": "Insight", "content": item})
        elif isinstance(item, dict):
            content = str(item.get("content") or item.get("insight") or item.get("learning") or item.get("takeaway") or "")
            if content:
                result.append({
                    "category": str(item.get("category") or "General"),
                    "title": str(item.get("title") or "Observation"),
                    "content": content
                })
    return tuple(result)


def _migrate_stale_user_intent(
    relevant_metadata: dict[str, object],
    existing_intent: str,
) -> tuple[dict[str, object], str]:
    """Move stray user_intent from relevant_metadata to stand-alone field."""
    cleaned = dict(relevant_metadata)
    stray = cleaned.pop("user_intent", None)
    intent = existing_intent or ""
    if stray and not intent:
        intent = str(stray)
    return cleaned, intent
