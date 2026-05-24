"""Fixtures for health_check tests — pre-constructed corrupted databases."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest


def _make_base_db(session_dir: Path) -> sqlite3.Connection:
    """Create a decohere.db with full schema."""
    db = session_dir / "decohere.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS raw_messages (
            store_id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL, content TEXT, tool_name TEXT,
            tool_call_id TEXT, timestamp REAL NOT NULL DEFAULT (unixepoch('subsec'))
        );
        CREATE TABLE IF NOT EXISTS ledger_entries (
            turn_n INTEGER PRIMARY KEY,
            entry_json TEXT NOT NULL,
            posted_at REAL NOT NULL DEFAULT (unixepoch('subsec')),
            validated INTEGER NOT NULL DEFAULT 0
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS concepts_fts
            USING fts5(term, definition, content='', tokenize='unicode61');
        """
    )
    return conn


def _add_entry(conn: sqlite3.Connection, turn_n: int, entry: dict, validated: bool = True) -> None:
    conn.execute(
        "INSERT INTO ledger_entries (turn_n, entry_json, validated) VALUES (?, ?, ?)",
        (turn_n, json.dumps(entry, ensure_ascii=False), 1 if validated else 0),
    )


def _add_raw(conn: sqlite3.Connection, count: int) -> None:
    for i in range(count):
        conn.execute(
            "INSERT INTO raw_messages (role, content) VALUES (?, ?)",
            ("user" if i % 2 == 0 else "assistant", f"msg {i}"),
        )


def _make_valid_entry(turn_n: int, msg_start: int = 0) -> dict:
    offset = msg_start + (turn_n - 1) * 6
    return {
        "n": turn_n,
        "message_range": [offset + 1, offset + 6],  # SQLite store_id is 1-indexed
        "entry_skipped": False,
        "tools": ["read_file"],
        "files_touched": [],
        "reference_documentation": (),
        "relevant_metadata": {"task": f"Task {turn_n}", "reference_class": "test"},
        "concepts_and_definitions": [
            {"term": f"concept{turn_n}", "definition": f"Definition for concept {turn_n}"}
        ],
        "narrative": {"summary": f"Turn {turn_n} summary", "cross_references": ()},
        "user_intent": f"Intent {turn_n}",
        "decisions_and_rationale": [],
        "procedures": [],
        "insights_and_learnings": [],
        "critical_reflection": {
            "ignored_perspectives": (), "logical_gaps": (), "improvement_directions": (),
        },
    }


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def healthy_db():
    """Clean DB with 3 valid entries — all checks should pass."""
    with tempfile.TemporaryDirectory() as td:
        profile = Path(td) / "healthy"
        sessions = profile / "sessions" / "healthy_session"
        sessions.mkdir(parents=True)
        conn = _make_base_db(sessions)
        for i in range(1, 4):
            entry = _make_valid_entry(i)
            _add_entry(conn, i, entry)
            conn.execute(
                "INSERT INTO concepts_fts (rowid, term, definition) VALUES (?, ?, ?)",
                (i, f"concept{i}", f"Definition for concept {i}"),
            )
        _add_raw(conn, 18)  # Exactly fills Turn1[0-5], Turn2[6-11], Turn3[12-17]
        conn.commit()
        conn.close()
        yield {"hermes_home": str(profile), "session_id": "healthy_session", "db_path": str(sessions / "decohere.db")}


@pytest.fixture
def missing_fields_db():
    """DB with entries missing required fields."""
    with tempfile.TemporaryDirectory() as td:
        profile = Path(td) / "missing_fields"
        sessions = profile / "sessions" / "mf_session"
        sessions.mkdir(parents=True)
        conn = _make_base_db(sessions)
        # Entry missing user_intent and critical_reflection
        broken = {
            "n": 1, "message_range": [0, 5],
            "entry_skipped": False, "tools": [], "files_touched": [],
            "reference_documentation": (),
            "relevant_metadata": {"task": "broken", "reference_class": "test"},
            "concepts_and_definitions": [],
            "narrative": {"summary": "Broken entry", "cross_references": ()},
            # user_intent MISSING
            "decisions_and_rationale": [],
            "procedures": [],
            "insights_and_learnings": [],
            # critical_reflection MISSING
        }
        _add_entry(conn, 1, broken)
        # Valid entry
        _add_entry(conn, 2, _make_valid_entry(2))
        _add_raw(conn, 10)
        conn.commit()
        conn.close()
        yield {"hermes_home": str(profile), "session_id": "mf_session", "db_path": str(sessions / "decohere.db")}


@pytest.fixture
def fts5_desync_db():
    """DB where concepts_fts is out of sync with ledger_entries."""
    with tempfile.TemporaryDirectory() as td:
        profile = Path(td) / "fts5_broken"
        sessions = profile / "sessions" / "fts5_session"
        sessions.mkdir(parents=True)
        conn = _make_base_db(sessions)
        # Add 3 ledger entries with concepts
        for i in range(1, 4):
            entry = _make_valid_entry(i)
            _add_entry(conn, i, entry)
        # But only add FTS5 for turns 1 and 3 (turn 2 missing)
        conn.execute("INSERT INTO concepts_fts (rowid, term, definition) VALUES (1, 'c1', 'd1')")
        conn.execute("INSERT INTO concepts_fts (rowid, term, definition) VALUES (3, 'c3', 'd3')")
        # Add orphan FTS5 row for non-existent turn 99
        conn.execute("INSERT INTO concepts_fts (rowid, term, definition) VALUES (99, 'ghost', 'orphan concept')")
        _add_raw(conn, 15)
        conn.commit()
        conn.close()
        yield {"hermes_home": str(profile), "session_id": "fts5_session", "db_path": str(sessions / "decohere.db")}


@pytest.fixture
def corrupted_json_db():
    """DB with one entry having corrupted JSON."""
    with tempfile.TemporaryDirectory() as td:
        profile = Path(td) / "corrupt_json"
        sessions = profile / "sessions" / "cj_session"
        sessions.mkdir(parents=True)
        conn = _make_base_db(sessions)
        conn.execute(
            "INSERT INTO ledger_entries (turn_n, entry_json, validated) VALUES (1, 'this is not valid json at all', 0)"
        )
        _add_entry(conn, 2, _make_valid_entry(2))
        _add_raw(conn, 10)
        conn.commit()
        conn.close()
        yield {"hermes_home": str(profile), "session_id": "cj_session", "db_path": str(sessions / "decohere.db")}


@pytest.fixture
def orphan_messages_db():
    """DB with raw_messages beyond ledger entry ranges."""
    with tempfile.TemporaryDirectory() as td:
        profile = Path(td) / "orphan_msgs"
        sessions = profile / "sessions" / "orphan_session"
        sessions.mkdir(parents=True)
        conn = _make_base_db(sessions)
        # Ledger entry covers messages 0-5
        entry = _make_valid_entry(1)
        entry["message_range"] = [1, 6]  # Store ID is 1-indexed
        _add_entry(conn, 1, entry)
        # But we added 20 raw messages — 14 are orphans (6-19)
        _add_raw(conn, 20)
        conn.commit()
        conn.close()
        yield {"hermes_home": str(profile), "session_id": "orphan_session", "db_path": str(sessions / "decohere.db")}
