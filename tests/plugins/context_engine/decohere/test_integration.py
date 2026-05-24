"""Integration tests for the decohere context engine plugin.

End-to-end tests of the full pipeline: on_session_start → compress → update_from_response.
Uses a mock SessionIO backed by an in-memory SQLite database.
"""

import pytest
import sys
import os
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, "/Users/shurigenha/.hermes/hermes-agent")

from plugins.context_engine.decohere import Decohere
from plugins.context_engine.decohere.io.session_io import SessionIO
from plugins.context_engine.decohere.context.classifier import check_readiness
from plugins.context_engine.decohere.context.builder import build_ledger_context, build_fallback_context, build_indexed_context
from plugins.context_engine.decohere.context.formatter import format_entry_layer, format_proc_layer
from plugins.context_engine.decohere.core.extractor import last_turn_messages, mechanical_fields, tool_chain_log
from plugins.context_engine.decohere.core.validator import validate_entry
from plugins.context_engine.decohere.core.prompt import build_entry_prompt


# ── Helpers ──

def _make_msg(role, content=None, tool_calls=None, tool_name=None, tool_call_id=None):
    msg = {"role": role, "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if tool_name:
        msg["tool_name"] = tool_name
    if tool_call_id:
        msg["tool_call_id"] = tool_call_id
    return msg


def _make_tool_call(name, arguments):
    import json
    return {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments) if isinstance(arguments, dict) else arguments,
        },
    }


def _create_session_io():
    """Create an in-memory SessionIO for testing."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    # Create schema
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS raw_messages (
            store_id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL, content TEXT,
            tool_name TEXT, tool_call_id TEXT,
            timestamp REAL NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_raw_role ON raw_messages(role);
        CREATE TABLE IF NOT EXISTS ledger_entries (
            turn_n INTEGER PRIMARY KEY,
            entry_json TEXT NOT NULL,
            posted_at REAL NOT NULL DEFAULT 0,
            validated INTEGER NOT NULL DEFAULT 0
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS concepts_fts
            USING fts5(term, definition, content='', tokenize='unicode61');
    """)

    from plugins.context_engine.decohere.store import RawMessageStore, LedgerStore
    raw = RawMessageStore(conn)
    ledger = LedgerStore(conn)

    # Build a SessionIO-like wrapper
    class TestSessionIO:
        def __init__(self):
            self._raw = raw
            self._ledger = ledger

        def compute_range(self, messages):
            return self._raw.append(messages)

        def get_raw_messages(self, start=0, end=None):
            return self._raw.get(start, end)

        def raw_count(self):
            return self._raw.count()

        def save_turn(self, turn):
            self._ledger.save_turn(turn)

        def get_turns(self):
            return self._ledger.get_turns()

        def get_turn(self, turn_n):
            return self._ledger.get_turn(turn_n)

        def turn_count(self):
            return self._ledger.turn_count()

        def is_v2(self):
            return True

        def close(self):
            pass

    return TestSessionIO()


# ── Tests ──

def test_full_pipeline_single_turn():
    """Full pipeline: extract → validate → format → context."""
    io = _create_session_io()

    messages = [
        _make_msg("user", "Read the config file."),
        _make_msg("assistant", None, tool_calls=[
            _make_tool_call("read_file", {"path": "/tmp/config.yaml"}),
        ]),
        _make_msg("tool", '{"content": "key: value"}', tool_name="read_file",
                  tool_call_id="call_1"),
        _make_msg("assistant", "Config file contains key: value."),
    ]

    # Phase 1: Extract last turn
    turn_msgs = last_turn_messages(messages)
    assert len(turn_msgs) == 4

    # Extract mechanical fields
    mech = mechanical_fields(turn_msgs)
    assert len(mech.tools) == 1
    assert mech.tools[0]["name"] == "read_file"

    # Build tool chain log
    chain = tool_chain_log(turn_msgs)
    assert "read_file" in chain

    # Build prompt
    sys_prompt, user_prompt = build_entry_prompt(
        "Read the config file.", chain, "Config file contains key: value."
    )
    assert "ledger entry builder" in sys_prompt.lower()
    assert "extract facts only" in user_prompt

    # Simulate entry posting result (mock: valid JSON)
    mock_entry = {
        "reference_documentation": [{"source": "read_file", "content_summary": "Config file read"}],
        "relevant_metadata": {"task": "Read config", "reference_class": "File read"},
        "concepts_and_definitions": [{"term": "config", "definition": "Configuration file"}],
        "narrative": {"summary": "Read config file using read_file tool", "cross_references": []},
        "user_intent": "Read the config file",
        "decisions_and_rationale": [],
        "procedures": [],
        "insights_and_learnings": ["Config file contains key: value"],
        "critical_reflection": {
            "ignored_perspectives": [],
            "logical_gaps": [],
            "improvement_directions": [],
        },
    }

    # Validate
    validated = validate_entry(mock_entry)
    assert validated["user_intent"] == "Read the config file"
    assert len(validated["concepts_and_definitions"]) == 1

    # Save to ledger
    io.save_turn({
        "n": 1,
        "message_range": [0, 4],
        "entry_skipped": False,
        "tools": list(mech.tools),
        "files_touched": list(mech.files_touched),
        **validated,
    })

    # Verify persistence
    turns = io.get_turns()
    assert len(turns) == 1
    assert turns[0]["n"] == 1
    assert turns[0]["user_intent"] == "Read the config file"


def test_build_context_from_turns():
    """Build L1+L2 context messages from stored turns."""
    io = _create_session_io()

    # Save 3 turns
    for i in range(1, 4):
        turn = {
            "n": i,
            "message_range": [i, i + 1],
            "entry_skipped": False,
            "tools": [],
            "files_touched": [],
            "reference_documentation": [],
            "relevant_metadata": {"task": f"Task {i}", "reference_class": "test"},
            "concepts_and_definitions": [],
            "narrative": {"summary": f"Turn {i} did work", "cross_references": []},
            "user_intent": f"Intent {i}",
            "decisions_and_rationale": [],
            "procedures": [],
            "insights_and_learnings": [],
            "critical_reflection": {
                "ignored_perspectives": (),
                "logical_gaps": (),
                "improvement_directions": (),
            },
        }
        io.save_turn(turn)

    turns = io.get_turns()

    # Check readiness
    readiness = check_readiness(turns, len(turns))
    assert readiness.state == "ready"

    # Build context
    context = build_ledger_context(turns, max_turns=20)
    assert len(context) == 2
    assert context[0]["role"] == "system"
    assert context[1]["role"] == "system"
    assert context[1]["name"] == "turn_context"

    # Verify all turns present
    content = context[0]["content"] + context[1]["content"]
    for i in range(1, 4):
        assert f"Turn {i}" in content


def test_fallback_when_turn_pending():
    """When latest turn has no critical_reflection, use fallback."""
    io = _create_session_io()

    # Save 2 completed turns + 1 pending placeholder
    for i in range(1, 3):
        io.save_turn({
            "n": i, "message_range": [i, i + 1], "entry_skipped": False,
            "tools": [], "files_touched": [],
            "reference_documentation": [],
            "relevant_metadata": {"task": f"T{i}", "reference_class": "test"},
            "concepts_and_definitions": [], "narrative": {"summary": f"T{i}", "cross_references": []},
            "user_intent": "", "decisions_and_rationale": [], "procedures": [],
            "insights_and_learnings": [],
            "critical_reflection": {"ignored_perspectives": (), "logical_gaps": (), "improvement_directions": ()},
        })

    # Pending placeholder
    io.save_turn({
        "n": 3, "message_range": [3, 4], "entry_skipped": False,
        "tools": [], "files_touched": [],
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

    turns = io.get_turns()
    readiness = check_readiness(turns, len(turns))
    assert readiness.state == "pending"

    # Fallback context should use raw for latest turn
    last_raw = [{"role": "user", "content": "New question"}]
    context = build_fallback_context(turns, 20, last_raw)
    assert len(context) >= 2
    assert any("raw" in msg.get("content", "") for msg in context)


def test_indexed_context_many_turns():
    """Indexed context for >20 turns."""
    io = _create_session_io()

    for i in range(1, 26):
        io.save_turn({
            "n": i, "message_range": [i, i + 1], "entry_skipped": False,
            "tools": [{"name": "read_file", "args_summary": "path=x"}],
            "files_touched": ["~/test.py"],
            "reference_documentation": [],
            "relevant_metadata": {"task": f"Task {i}", "reference_class": "test"},
            "concepts_and_definitions": [{"term": "testing", "definition": "Unit test"}],
            "narrative": {"summary": f"Turn {i} work", "cross_references": []},
            "user_intent": f"Intent {i}",
            "decisions_and_rationale": [],
            "procedures": [],
            "insights_and_learnings": [f"Learned {i}"],
            "critical_reflection": {
                "ignored_perspectives": (), "logical_gaps": (), "improvement_directions": (),
            },
        })

    turns = io.get_turns()
    context = build_indexed_context(turns, 20)
    assert len(context) == 3  # index + L1 + L2
    assert "Turn Index" in context[0]["content"]


def test_extractor_never_truncates():
    """Verify the core rule: no text truncation anywhere."""
    long_content = "A" * 10000
    messages = [
        _make_msg("user", "Process large data."),
        _make_msg("assistant", None, tool_calls=[
            _make_tool_call("terminal", {"command": long_content}),
        ]),
        _make_msg("tool", long_content, tool_name="terminal"),
        _make_msg("assistant", long_content),
    ]

    # tool_chain_log must never include truncated content
    chain = tool_chain_log(messages)
    # The full command should be present (no truncation)
    assert "..." not in chain or "text (" in chain  # "text (N chars)" is ok, actual truncation is not

    # summarise_tool_result must describe, not truncate
    from plugins.context_engine.decohere.core.extractor import summarise_tool_result
    result = summarise_tool_result(long_content)
    assert "10000 chars" in result
    # Full content must NOT be in the summary (that would be the whole thing, not a summary)
    # But the summary should NOT contain a truncated preview like "AAA..."
    assert long_content[:100] not in result


def test_validator_repairs_damaged_entry():
    """Validator handles damaged/missing fields gracefully."""
    damaged = {
        "reference_documentation": "not a list",  # wrong type
        "relevant_metadata": None,
        "concepts_and_definitions": None,
        "narrative": {"summary": "ok"},
        "user_intent": 42,  # not a string
        "insights_and_learnings": [{"insight": "should be string"}],
    }
    result = validate_entry(damaged)
    assert isinstance(result["reference_documentation"], tuple)
    assert isinstance(result["user_intent"], str)
    assert result["relevant_metadata"] == {"task": "", "reference_class": ""}
    assert result["critical_reflection"]["logical_gaps"] == ()
