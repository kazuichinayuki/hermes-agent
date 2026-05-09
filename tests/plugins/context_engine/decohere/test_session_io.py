"""Integration tests for decohere SessionIO — local, no Discord needed.

Tests the full persistence pipeline: on_session_start → compress → verify DB.
Uses temporary directories, no network, no async event loop needed for
single-turn tests.
"""

import os
import sqlite3
import json
import tempfile
from pathlib import Path

import pytest

# Ensure the repo root is on the path
import sys
sys.path.insert(0, "/Users/shurigenha/.hermes/hermes-agent")

from plugins.context_engine.decohere import Decohere
from plugins.context_engine.decohere.io.session_io import SessionIO


@pytest.fixture
def temp_profile():
    """Create a temporary profile directory with a minimal config.yaml."""
    with tempfile.TemporaryDirectory() as td:
        profile = Path(td) / "test_profile"
        profile.mkdir()
        (profile / "sessions").mkdir()
        # Minimal config so _read_aux_config doesn't crash
        (profile / "config.yaml").write_text("auxiliary:\n  compression:\n    model: test-model\n")
        yield profile


@pytest.fixture
def decohere_session(temp_profile):
    """Create a Decohere instance with on_session_start called."""
    d = Decohere()
    d.on_session_start("test_session_001", hermes_home=str(temp_profile), platform="cli")
    yield d
    if d._io:
        d._io.close()


class TestSessionIOPersistence:
    """Verify data survives across connections — the core bug we fixed."""

    def test_raw_messages_persist(self, temp_profile):
        """Messages written via SessionIO must be visible to a new connection."""
        io = SessionIO(temp_profile, "test_raw_persist")

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello world"},
        ]
        start, end = io.compute_range(messages)
        assert end - start == 2

        # Verify via NEW connection
        db_path = str(temp_profile / "sessions" / "test_raw_persist" / "decohere.db")
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
        assert count == 2

        rows = conn.execute("SELECT role, content FROM raw_messages ORDER BY store_id").fetchall()
        assert rows[0] == ("system", "You are helpful.")
        assert rows[1] == ("user", "Hello world")
        conn.close()
        io.close()

    def test_ledger_entries_persist(self, temp_profile):
        """Turns written via SessionIO must be visible to a new connection."""
        io = SessionIO(temp_profile, "test_ledger_persist")

        turn = {
            "n": 1, "message_range": [0, 2], "entry_skipped": False,
            "tools": [{"name": "read_file", "args_summary": "path=/x"}],
            "files_touched": ["~/test.py"],
            "reference_documentation": [],
            "relevant_metadata": {"task": "Read file", "reference_class": "test"},
            "concepts_and_definitions": [{"term": "test", "definition": "A test concept"}],
            "narrative": {"summary": "Test turn", "cross_references": []},
            "user_intent": "Run tests",
            "decisions_and_rationale": [],
            "procedures": [],
            "insights_and_learnings": ["Persistence works"],
            "critical_reflection": {
                "ignored_perspectives": (), "logical_gaps": (), "improvement_directions": (),
            },
        }
        io.save_turn(turn)

        # Verify via NEW connection
        db_path = str(temp_profile / "sessions" / "test_ledger_persist" / "decohere.db")
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
        assert count == 1

        row = conn.execute("SELECT entry_json FROM ledger_entries WHERE turn_n=1").fetchone()
        saved = json.loads(row[0])
        assert saved["n"] == 1
        assert saved["user_intent"] == "Run tests"
        assert len(saved["tools"]) == 1
        assert saved["tools"][0]["name"] == "read_file"

        # FTS5 concept search should work
        results = conn.execute(
            "SELECT rowid, term FROM concepts_fts WHERE concepts_fts MATCH ?", ("test",)
        ).fetchall()
        assert len(results) >= 1
        conn.close()
        io.close()

    def test_multi_turn_accumulation(self, temp_profile):
        """Multiple turns accumulate correctly across separate save calls."""
        io = SessionIO(temp_profile, "test_multi_turn")

        for i in range(1, 4):
            io.save_turn({
                "n": i, "message_range": [i, i + 1], "entry_skipped": False,
                "tools": [], "files_touched": [],
                "reference_documentation": [],
                "relevant_metadata": {"task": f"Task {i}", "reference_class": "test"},
                "concepts_and_definitions": [],
                "narrative": {"summary": f"Turn {i}", "cross_references": []},
                "user_intent": f"Intent {i}",
                "decisions_and_rationale": [],
                "procedures": [],
                "insights_and_learnings": [],
                "critical_reflection": {
                    "ignored_perspectives": (), "logical_gaps": (), "improvement_directions": (),
                },
            })

        turns = io.get_turns()
        assert len(turns) == 3
        assert turns[0]["n"] == 1
        assert turns[2]["n"] == 3

        db_path = str(temp_profile / "sessions" / "test_multi_turn" / "decohere.db")
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
        assert count == 3
        conn.close()
        io.close()

    def test_turn_count_accurate(self, temp_profile):
        """turn_count() must reflect what's actually in the database."""
        io = SessionIO(temp_profile, "test_count")
        assert io.turn_count() == 0

        for i in range(1, 6):
            io.save_turn({
                "n": i, "message_range": [i, i + 1], "entry_skipped": False,
                "tools": [], "files_touched": [],
                "reference_documentation": [],
                "relevant_metadata": {"task": f"T{i}", "reference_class": "test"},
                "concepts_and_definitions": [],
                "narrative": {"summary": f"T{i}", "cross_references": []},
                "user_intent": "", "decisions_and_rationale": [], "procedures": [],
                "insights_and_learnings": [],
                "critical_reflection": {
                    "ignored_perspectives": (), "logical_gaps": (), "improvement_directions": (),
                },
            })
            assert io.turn_count() == i
        io.close()


class TestCompressPipeline:
    """Test the full compress() → persist pipeline locally."""

    def test_compress_writes_placeholder(self, temp_profile):
        """compress() must write a placeholder turn that survives."""
        d = Decohere()
        d.on_session_start("test_cp", hermes_home=str(temp_profile), platform="cli")

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello world"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = d.compress(messages)

        # compress returns L1+L2 context
        assert len(result) >= 0  # first turn may return empty

        # Data must be in the DB
        db_path = str(temp_profile / "sessions" / "test_cp" / "decohere.db")
        conn = sqlite3.connect(db_path)
        raw_count = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
        turn_count = conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
        assert raw_count > 0, f"Expected raw_messages, got {raw_count}"
        assert turn_count > 0, f"Expected ledger_entries, got {turn_count}"

        entry = conn.execute("SELECT entry_json FROM ledger_entries WHERE turn_n=1").fetchone()
        turn = json.loads(entry[0])
        assert turn["n"] == 1
        assert "entry_skipped" in turn
        conn.close()
        d._io.close()

    def test_compress_twice_accumulates(self, temp_profile):
        """Two compress() calls should create two turns."""
        d = Decohere()
        d.on_session_start("test_cp2", hermes_home=str(temp_profile), platform="cli")

        # Turn 1: short text-only (will be skipped)
        msgs1 = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        d.compress(msgs1)

        # Turn 2: with a tool call (will NOT be skipped)
        msgs2 = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "Read file"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path": "/tmp/x"}'}}
            ]},
            {"role": "tool", "content": '{"ok": true}', "tool_call_id": "c1", "tool_name": "read_file"},
            {"role": "assistant", "content": "Done."},
        ]
        d.compress(msgs2)

        db_path = str(temp_profile / "sessions" / "test_cp2" / "decohere.db")
        conn = sqlite3.connect(db_path)
        turns = conn.execute("SELECT entry_json FROM ledger_entries ORDER BY turn_n").fetchall()
        assert len(turns) == 2

        t1 = json.loads(turns[0][0])
        t2 = json.loads(turns[1][0])
        assert t1["entry_skipped"] is True
        assert t2["entry_skipped"] is False
        assert len(t2["tools"]) == 1
        conn.close()
        d._io.close()

    def test_context_building(self, temp_profile):
        """After turns are fully posted, compress() returns L1+L2 context."""
        d = Decohere()
        d.on_session_start("test_cp3", hermes_home=str(temp_profile), platform="cli")

        # Turn 1: text only (will be skipped)
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Task 1"},
            {"role": "assistant", "content": "Done 1"},
        ]
        d.compress(msgs)

        # Turn 2: with a tool call (won't be skipped)
        msgs = msgs + [
            {"role": "user", "content": "Read config"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path": "/x"}'}}
            ]},
            {"role": "tool", "content": '{"ok": true}', "tool_call_id": "c1", "tool_name": "read_file"},
            {"role": "assistant", "content": "Done 2"},
        ]
        d.compress(msgs)

        # Manually fill the placeholder to simulate completed async posting.
        # In production this is done by TaskManager._run → post_entry → validate_entry.
        turns = d._io.get_turns()
        for turn in turns:
            if turn.get("critical_reflection") is None and not turn.get("entry_skipped"):
                turn["reference_documentation"] = ()
                turn["relevant_metadata"] = {"task": "Test", "reference_class": "test"}
                turn["concepts_and_definitions"] = [{"term": "config", "definition": "Test config"}]
                turn["narrative"] = {"summary": f"Turn {turn['n']} summary", "cross_references": ()}
                turn["user_intent"] = "Test"
                turn["decisions_and_rationale"] = ()
                turn["procedures"] = ()
                turn["insights_and_learnings"] = ()
                turn["critical_reflection"] = {
                    "ignored_perspectives": (), "logical_gaps": (), "improvement_directions": (),
                }
                d._io.save_turn(turn)

        # Turn 3: now compress should see fully-spec'd turns and build L1+L2
        msgs = msgs + [
            {"role": "user", "content": "Task 3"},
            {"role": "assistant", "content": "Done 3"},
        ]
        result = d.compress(msgs)

        assert len(result) >= 2  # at least L1+L2 for prior turns
        assert result[0]["role"] == "user"       # L1 Spec
        assert result[1]["role"] == "user"        # L2 Proc
        assert result[1]["name"] == "turn_context"

        content = result[0]["content"] + result[1]["content"]
        assert "Turn 2" in content

        d._io.close()
