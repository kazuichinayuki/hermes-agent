"""Tests for context/formatter.py — pure formatting functions."""

import pytest
import sys
import os

sys.path.insert(0, '/Users/shurigenha/.hermes/hermes-agent')

from plugins.context_engine.decohere.context.formatter import (
    format_entry_layer,
    format_proc_layer,
    format_structural_from_raw,
    format_raw_compressed,
    format_turn_index,
    sanitize_path,
)


# ── sanitize_path ──

def test_sanitize_home_path():
    home = os.path.expanduser("~")
    result = sanitize_path(home + "/.hermes/config.yaml")
    assert result == "~/.hermes/config.yaml"


def test_sanitize_non_home_path():
    result = sanitize_path("/etc/hosts")
    assert result == "/etc/hosts"


def test_sanitize_already_relative():
    result = sanitize_path("relative/path.txt")
    assert result == "relative/path.txt"


def test_sanitize_root_home():
    """When home is '/' (container), don't mangle paths."""
    if os.path.expanduser("~") == "/":
        result = sanitize_path("/etc/passwd")
        assert result == "/etc/passwd"


# ── format_entry_layer ──

def test_format_entry_layer_simple():
    turn = {
        "n": 3,
        "message_range": [10, 25],
        "tools": [{"name": "read_file", "args_summary": "path=config.yaml"}],
        "files_touched": ["~/config.yaml"],
        "relevant_metadata": {"task": "Read config", "reference_class": "Config read"},
    }
    result = format_entry_layer(turn)
    assert "[Turn 3]" in result
    assert "read_file" in result
    assert "Read config" in result


def test_format_entry_layer_empty_tools():
    turn = {"n": 1, "message_range": [], "tools": [], "files_touched": [],
            "relevant_metadata": {"task": "", "reference_class": ""}}
    result = format_entry_layer(turn)
    assert "[Turn 1]" in result
    assert "none" in result


# ── format_proc_layer ──

def test_format_proc_layer_with_concepts():
    turn = {
        "n": 2,
        "concepts_and_definitions": [
            {"term": "API", "definition": "Interface for programs"},
        ],
        "narrative": {"summary": "Added API support", "cross_references": ["Turn 1"]},
        "decisions_and_rationale": [],
        "procedures": [],
        "insights_and_learnings": ["REST is simpler than SOAP"],
        "user_intent": "Add REST API",
        "critical_reflection": {"improvement_directions": ["Add error handling"]},
    }
    result = format_proc_layer(turn)
    assert "[Turn 2]" in result
    assert "API" in result
    assert "REST is simpler than SOAP" in result
    assert "improvements" in result.lower() or "↳" in result


def test_format_proc_layer_empty():
    turn = {
        "n": 1,
        "concepts_and_definitions": [],
        "narrative": {"summary": "", "cross_references": []},
        "decisions_and_rationale": [],
        "procedures": [],
        "insights_and_learnings": [],
        "user_intent": "",
        "critical_reflection": {"improvement_directions": []},
    }
    result = format_proc_layer(turn)
    assert "[Turn 1]" in result


# ── format_structural_from_raw ──

def test_format_structural_no_tools():
    msgs = [{"role": "user", "content": "hello"}]
    result = format_structural_from_raw(msgs)
    assert "raw fallback" in result


def test_format_structural_with_tools():
    msgs = [
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "read_file"}},
            {"function": {"name": "web_search"}},
        ]},
    ]
    result = format_structural_from_raw(msgs)
    assert "read_file" in result
    assert "web_search" in result


# ── format_raw_compressed ──

def test_format_raw_compressed():
    msgs = [
        {"role": "user", "content": "First sentence. Second sentence. Third sentence. Extra."},
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "terminal"}},
        ]},
    ]
    result = format_raw_compressed(msgs)
    assert "First sentence" in result
    assert "terminal" in result
    assert "raw compressed fallback" in result


def test_format_raw_compressed_no_user():
    msgs = [{"role": "assistant", "content": "no user here"}]
    result = format_raw_compressed(msgs)
    assert "raw compressed fallback" in result


# ── format_turn_index ──

def test_format_turn_index():
    index = {
        "entries": [
            {"n": 1, "title": "Setup project", "summary_1line": "Initialized repo",
             "tools_used": ["terminal"], "key_concepts": ["repo"], "files_touched": []},
            {"n": 2, "title": "Add tests", "summary_1line": "Wrote unit tests",
             "tools_used": ["write_file"], "key_concepts": ["testing"], "files_touched": []},
        ],
        "concept_map": {},
        "file_map": {},
    }
    result = format_turn_index(index)
    assert "[Turn 1]" in result
    assert "[Turn 2]" in result
    assert "Setup project" in result


def test_format_turn_index_empty():
    result = format_turn_index({})
    assert "(no turns)" in result
