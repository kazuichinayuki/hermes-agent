"""Tests for memoir-style decohere command-loop TUI."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))


@pytest.fixture
def hermes_home(tmp_path):
    home = tmp_path / "hermes"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    for name in ("s1", "s2"):
        sd = sessions / name
        sd.mkdir()
        db_path = sd / "decohere.db"
        _create_sample_db(db_path, turns=2 if name == "s1" else 1)
    return home


def _create_sample_db(db_path: Path, turns: int = 2) -> None:
    from plugins.context_engine.decohere.db import ensure_schema, run_migrations

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_schema(conn)
    run_migrations(conn)
    for turn_n in range(1, turns + 1):
        entry = {
            "n": turn_n,
            "message_range": [turn_n * 10, turn_n * 10 + 2],
            "tools": [{"name": f"tool_{turn_n}"}],
            "files_touched": [f"file_{turn_n}.py"],
            "relevant_metadata": {"task": f"Task {turn_n}"},
            "user_intent": f"Intent {turn_n}",
            "concepts_and_definitions": [
                {"term": f"concept_{turn_n}_a", "definition": f"Def A {turn_n}"},
                {"term": f"concept_{turn_n}_b", "definition": f"Def B {turn_n}"},
            ],
            "narrative": {"summary": f"Summary {turn_n}"},
        }
        conn.execute(
            "INSERT INTO ledger_entries(turn_n, entry_json) VALUES (?, ?)",
            (turn_n, json.dumps(entry)),
        )
        for idx, c in enumerate(entry["concepts_and_definitions"]):
            conn.execute(
                "INSERT INTO concepts_fts(rowid, term, definition) VALUES (?, ?, ?)",
                (turn_n * 1000 + idx, c["term"], c["definition"]),
            )
        for i in range(2):
            conn.execute(
                "INSERT INTO raw_messages(role, content, tool_name) VALUES (?, ?, ?)",
                ("user" if i == 0 else "assistant", f"Message {turn_n}-{i}", None),
            )
    conn.execute("INSERT INTO session_state(key, value, scope) VALUES ('k', 'v', 'session')")
    conn.commit()
    conn.close()


@pytest.fixture
def app(hermes_home):
    from plugins.context_engine.decohere.cli.tui import DecohereCLI

    return DecohereCLI(hermes_home)


class TestSessionCommands:
    def test_scan_sessions(self, app):
        sessions = app._scan_sessions()
        assert len(sessions) == 2
        assert sessions[0].index == 1
        assert sessions[0].session_id in {"s1", "s2"}

    def test_sessions_command_populates_cache(self, app, capsys):
        app.execute("sessions")
        assert len(app._sessions_cache) == 2

    def test_open_by_index(self, app):
        app.execute("sessions")
        app.execute("open 1")
        assert app.current is not None

    def test_open_by_prefix(self, app):
        app.execute("open s1")
        assert app.current.session_id == "s1"

    def test_close(self, app):
        app.execute("open s1")
        app.execute("close")
        assert app.current is None


class TestTurnCommands:
    def test_turns_requires_session(self, app):
        with pytest.raises(RuntimeError):
            app.execute("turns")

    def test_turns_lists(self, app, capsys):
        app.execute("open s1")
        app.execute("turns")
        out = capsys.readouterr().out
        assert "T1" in out
        assert "T2" in out

    def test_turns_query(self, app, capsys):
        app.execute("open s1")
        app.execute("turns Task 2")
        out = capsys.readouterr().out
        assert "Task 2" in out

    def test_show(self, app, capsys):
        app.execute("open s1")
        app.execute("show 1")
        out = capsys.readouterr().out
        assert "Turn 1" in out
        assert "concept_1_a" in out

    def test_edit_turn_field(self, app):
        app.execute("open s1")
        app.execute('edit 1 relevant_metadata.task "Changed Task"')
        conn = sqlite3.connect(app.current.db_path)
        entry = json.loads(conn.execute("SELECT entry_json FROM ledger_entries WHERE turn_n=1").fetchone()[0])
        conn.close()
        assert entry["relevant_metadata"]["task"] == "Changed Task"


class TestConceptCommands:
    def test_concepts_lists(self, app, capsys):
        app.execute("open s1")
        app.execute("concepts")
        out = capsys.readouterr().out
        assert "concept_1_a" in out
        assert "concept_2_b" in out

    def test_concepts_query(self, app, capsys):
        app.execute("open s1")
        app.execute("concepts concept_2")
        out = capsys.readouterr().out
        assert "concept_2_a" in out
        assert "concept_1_a" not in out

    def test_concept_show(self, app, capsys):
        app.execute("open s1")
        app.execute("concept 1 0")
        out = capsys.readouterr().out
        assert "concept_1_a" in out
        assert "Def A 1" in out

    def test_concept_set_term_rebuilds_fts(self, app):
        app.execute("open s1")
        app.execute("concept-set 1 0 term NewTerm")
        conn = sqlite3.connect(app.current.db_path)
        entry = json.loads(conn.execute("SELECT entry_json FROM ledger_entries WHERE turn_n=1").fetchone()[0])
        assert entry["concepts_and_definitions"][0]["term"] == "NewTerm"
        assert conn.execute("SELECT COUNT(*) FROM concepts_fts WHERE concepts_fts MATCH 'NewTerm'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM concepts_fts WHERE concepts_fts MATCH 'concept_1_a'").fetchone()[0] == 0
        conn.close()

    def test_concept_set_definition(self, app):
        app.execute("open s1")
        app.execute('concept-set 1 0 definition "New Definition"')
        conn = sqlite3.connect(app.current.db_path)
        entry = json.loads(conn.execute("SELECT entry_json FROM ledger_entries WHERE turn_n=1").fetchone()[0])
        conn.close()
        assert entry["concepts_and_definitions"][0]["definition"] == "New Definition"

    def test_concept_add(self, app):
        app.execute("open s1")
        app.execute('concept-add 1 AddedTerm :: Added definition')
        conn = sqlite3.connect(app.current.db_path)
        entry = json.loads(conn.execute("SELECT entry_json FROM ledger_entries WHERE turn_n=1").fetchone()[0])
        assert entry["concepts_and_definitions"][-1]["term"] == "AddedTerm"
        assert conn.execute("SELECT COUNT(*) FROM concepts_fts WHERE concepts_fts MATCH 'AddedTerm'").fetchone()[0] == 1
        conn.close()

    def test_concept_del(self, app):
        app.execute("open s1")
        app.execute("concept-del 1 0")
        conn = sqlite3.connect(app.current.db_path)
        entry = json.loads(conn.execute("SELECT entry_json FROM ledger_entries WHERE turn_n=1").fetchone()[0])
        terms = [c["term"] for c in entry["concepts_and_definitions"]]
        assert "concept_1_a" not in terms
        assert conn.execute("SELECT COUNT(*) FROM concepts_fts WHERE concepts_fts MATCH 'concept_1_a'").fetchone()[0] == 0
        conn.close()


class TestMessagesAndState:
    def test_messages(self, app, capsys):
        app.execute("open s1")
        app.execute("messages")
        out = capsys.readouterr().out
        assert "Message 1-0" in out

    def test_messages_query(self, app, capsys):
        app.execute("open s1")
        app.execute("messages 2-0")
        out = capsys.readouterr().out
        assert "Message 2-0" in out
        assert "Message 1-0" not in out

    def test_message(self, app, capsys):
        app.execute("open s1")
        app.execute("message 1")
        out = capsys.readouterr().out
        assert "Message #1" in out

    def test_state(self, app, capsys):
        app.execute("open s1")
        app.execute("state")
        out = capsys.readouterr().out
        assert "test_key" not in out  # sample uses key k
        assert "k" in out

    def test_state_set(self, app):
        app.execute("open s1")
        app.execute("state-set session key2 value2")
        conn = sqlite3.connect(app.current.db_path)
        assert conn.execute("SELECT value FROM session_state WHERE key='key2'").fetchone()[0] == "value2"
        conn.close()

    def test_state_del(self, app):
        app.execute("open s1")
        app.execute("state-del session k")
        conn = sqlite3.connect(app.current.db_path)
        assert conn.execute("SELECT COUNT(*) FROM session_state WHERE key='k'").fetchone()[0] == 0
        conn.close()


class TestDBHelpers:
    def test_schema_v2_regular_fts(self, hermes_home):
        from plugins.context_engine.decohere.cli.tui import _open_db_readwrite
        db = hermes_home / "sessions" / "s1" / "decohere.db"
        conn = _open_db_readwrite(db)
        # Regular FTS5 supports content SELECT and plain DELETE
        row = conn.execute("SELECT term FROM concepts_fts WHERE rowid=1000").fetchone()
        assert row[0] == "concept_1_a"
        conn.execute("DELETE FROM concepts_fts WHERE rowid=1000")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM concepts_fts WHERE rowid=1000").fetchone()[0] == 0
        conn.close()

    def test_corrupted_entry_json(self, hermes_home):
        from plugins.context_engine.decohere.cli.tui import _load_entry_json, _open_db_readonly
        db = hermes_home / "sessions" / "s1" / "decohere.db"
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO ledger_entries(turn_n, entry_json) VALUES (99, 'bad json')")
        conn.commit(); conn.close()
        conn2 = _open_db_readonly(db)
        assert _load_entry_json(conn2, 99) is None
        conn2.close()

    def test_imports(self):
        from plugins.context_engine.decohere.cli.tui import DecohereCLI, run_tui, _save_entry_json
        assert callable(run_tui)
        assert DecohereCLI is not None
        assert callable(_save_entry_json)
