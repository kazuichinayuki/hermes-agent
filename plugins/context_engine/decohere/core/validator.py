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
    repaired["insights_and_learnings"] = _flatten_insights(
        raw.get("insights_and_learnings", ())
    )

    cr = raw.get("critical_reflection", {}) or {}
    repaired["critical_reflection"] = {
        "ignored_perspectives": tuple(cr.get("ignored_perspectives", ()) or ()),
        "logical_gaps": tuple(cr.get("logical_gaps", ()) or ()),
        "improvement_directions": tuple(cr.get("improvement_directions", ()) or ()),
    }

    return repaired


def _flatten_insights(raw: object) -> tuple[str, ...]:
    """If model produced object array, flatten to string tuple."""
    if not raw:
        return ()
    if not isinstance(raw, (list, tuple)):
        return (str(raw),)

    result: list = []
    for item in raw:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            parts = []
            for key in ("insight", "learning", "observation", "takeaway"):
                val = item.get(key, "")
                if val:
                    parts.append(str(val))
            if parts:
                result.append(": ".join(parts))
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
