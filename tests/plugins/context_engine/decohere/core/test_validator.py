"""Tests for core/validator.py — pure validation and repair functions."""

import pytest
import sys
import os

sys.path.insert(0, '/Users/shurigenha/.hermes/hermes-agent')

from plugins.context_engine.decohere.core.validator import (
    validate_entry,
    _flatten_insights,
    _migrate_stale_user_intent,
)


# ── validate_entry ──

def test_validate_entry_empty_input():
    """Empty dict gets all defaults filled."""
    result = validate_entry({})
    assert result["reference_documentation"] == ()
    assert result["relevant_metadata"] == {"task": "", "reference_class": ""}
    assert result["concepts_and_definitions"] == ()
    assert result["narrative"] == {"summary": "", "cross_references": ()}
    assert result["user_intent"] == ""
    assert result["decisions_and_rationale"] == ()
    assert result["procedures"] == ()
    assert result["insights_and_learnings"] == ()
    assert result["critical_reflection"] == {
        "ignored_perspectives": (),
        "logical_gaps": (),
        "improvement_directions": (),
    }


def test_validate_entry_preserves_good_data():
    """All fields present and correct should pass through."""
    raw = {
        "reference_documentation": [{"source": "web_search", "content_summary": "Found docs"}],
        "relevant_metadata": {"task": "Test task", "reference_class": "unit test"},
        "concepts_and_definitions": [{"term": "API", "definition": "Application Programming Interface"}],
        "narrative": {"summary": "Turn summary here", "cross_references": ["Turn 1"]},
        "user_intent": "Test the validator",
        "decisions_and_rationale": [{"decision": "Use tuple", "rationale": "Immutable"}],
        "procedures": [{"procedure": "Run tests", "context": "CI"}],
        "insights_and_learnings": ["Tuple is immutable", "Dict is mutable"],
        "critical_reflection": {
            "ignored_perspectives": [],
            "logical_gaps": ["None found"],
            "improvement_directions": ["Add more tests"],
        },
    }
    result = validate_entry(raw)
    assert result["user_intent"] == "Test the validator"
    assert len(result["insights_and_learnings"]) == 2


def test_validate_entry_none_fields():
    """None values should be replaced with empty defaults."""
    result = validate_entry({
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
    assert result["reference_documentation"] == ()
    assert result["user_intent"] == ""
    assert result["critical_reflection"]["logical_gaps"] == ()


def test_validate_entry_returns_new_dict():
    """Must return a new dict, not mutate the input."""
    raw = {"user_intent": "original"}
    result = validate_entry(raw)
    assert result is not raw
    assert raw["user_intent"] == "original"


# ── _flatten_insights ──

def test_flatten_insights_strings():
    result = _flatten_insights(["insight one", "insight two"])
    assert result == ("insight one", "insight two")


def test_flatten_insights_objects():
    """Object arrays dicts get flattened to strings."""
    result = _flatten_insights([
        {"insight": "First insight"},
        {"learning": "Second learning"},
    ])
    assert len(result) == 2


def test_flatten_insights_mixed():
    result = _flatten_insights(["string insight", {"insight": "object insight"}])
    assert len(result) == 2


def test_flatten_insights_empty():
    result = _flatten_insights([])
    assert result == ()


def test_flatten_insights_none():
    result = _flatten_insights(None)
    assert result == ()


def test_flatten_insights_single_string():
    """Non-list input wrapped as single-element tuple."""
    result = _flatten_insights("single string")
    assert result == ("single string",)


# ── _migrate_stale_user_intent ──

def test_migrate_user_intent_stray_in_metadata():
    """user_intent in relevant_metadata moves to stand-alone."""
    cleaned, intent = _migrate_stale_user_intent(
        {"task": "t", "user_intent": "I want X"}, "",
    )
    assert intent == "I want X"
    assert "user_intent" not in cleaned


def test_migrate_user_intent_keep_existing():
    """Existing user_intent takes priority over stray."""
    cleaned, intent = _migrate_stale_user_intent(
        {"task": "t", "user_intent": "stray"}, "existing intent",
    )
    assert intent == "existing intent"


def test_migrate_user_intent_no_stray():
    cleaned, intent = _migrate_stale_user_intent(
        {"task": "t"}, "",
    )
    assert intent == ""
    assert cleaned == {"task": "t"}
