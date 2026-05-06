"""Immutable-first message and turn-spec store for decohere.

Every raw message is persisted durably in SQLite. Turn specs are stored
as JSON blobs with FTS5 indexing on concepts for cross-session search.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from .db import configure_connection, ensure_schema, run_migrations

logger = logging.getLogger(__name__)


class RawMessageStore:
    """Append-only raw message storage per session."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            configure_connection(self._conn)
            ensure_schema(self._conn)
            run_migrations(self._conn)
        return self._conn

    def append(self, messages: list[dict]) -> tuple[int, int]:
        """Append messages. Returns (start_id, end_id) store_id range."""
        conn = self._get_conn()
        start_id = self.count()
        with conn:
            for msg in messages:
                content = msg.get("content", "")
                if isinstance(content, (dict, list)):
                    content = json.dumps(content, ensure_ascii=False)
                conn.execute(
                    """INSERT INTO raw_messages (role, content, tool_name, tool_call_id)
                       VALUES (?, ?, ?, ?)""",
                    (
                        msg.get("role", "unknown"),
                        str(content) if content else None,
                        msg.get("tool_name"),
                        msg.get("tool_call_id"),
                    ),
                )
        end_id = start_id + len(messages)
        return (start_id, end_id)

    def count(self) -> int:
        """Return number of stored messages."""
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()
        return row[0] if row else 0

    def get(self, start: int = 0, end: int | None = None) -> list[dict]:
        """Retrieve messages by store_id range [start, end)."""
        conn = self._get_conn()
        if end is not None:
            rows = conn.execute(
                "SELECT store_id, role, content, tool_name, tool_call_id, timestamp "
                "FROM raw_messages WHERE store_id >= ? AND store_id < ? "
                "ORDER BY store_id",
                (start, end),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT store_id, role, content, tool_name, tool_call_id, timestamp "
                "FROM raw_messages WHERE store_id >= ? "
                "ORDER BY store_id",
                (start,),
            ).fetchall()

        return [
            {
                "store_id": r[0],
                "role": r[1],
                "content": r[2],
                "tool_name": r[3],
                "tool_call_id": r[4],
                "timestamp": r[5],
            }
            for r in rows
        ]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


class TurnSpecStore:
    """Turn specification storage per session.

    Stores complete turn specs as JSON blobs. Indexes concepts_and_definitions
    in FTS5 for cross-turn and cross-session search.
    """

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            configure_connection(self._conn)
            ensure_schema(self._conn)
            run_migrations(self._conn)
        return self._conn

    def save_turn(self, turn: dict) -> None:
        """Insert or replace a turn spec. Indexes concepts in FTS5."""
        conn = self._get_conn()
        turn_n = turn["n"]
        spec_json = json.dumps(turn, ensure_ascii=False)

        with conn:
            conn.execute(
                """INSERT OR REPLACE INTO turn_specs (turn_n, spec_json, validated)
                   VALUES (?, ?, ?)""",
                (turn_n, spec_json, 1 if turn.get("validated") else 0),
            )

            # Index concepts
            conn.execute("DELETE FROM concepts_fts WHERE rowid = ?", (turn_n,))
            for c in turn.get("concepts_and_definitions", []) or []:
                if isinstance(c, dict):
                    conn.execute(
                        "INSERT INTO concepts_fts (rowid, term, definition) VALUES (?, ?, ?)",
                        (turn_n, c.get("term", ""), c.get("definition", "")),
                    )

    def get_turns(self) -> list[dict]:
        """Return all turn specs ordered by turn_n."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT spec_json FROM turn_specs ORDER BY turn_n"
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get_turn(self, turn_n: int) -> dict | None:
        """Return a single turn spec by turn number."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT spec_json FROM turn_specs WHERE turn_n = ?", (turn_n,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def turn_count(self) -> int:
        """Return number of stored turns."""
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) FROM turn_specs").fetchone()
        return row[0] if row else 0

    def search_concepts(self, query: str, limit: int = 10) -> list[dict]:
        """FTS5 search across concepts_and_definitions across all turns."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT rowid, term, definition
                   FROM concepts_fts WHERE concepts_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [
            {"turn_n": r[0], "term": r[1], "definition": r[2]} for r in rows
        ]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
