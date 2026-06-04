"""Immutable-first message and ledger-entry store for decohere.

RawMessageStore and LedgerStore operate over a shared SQLite connection.
The connection is owned by SessionIO — stores never open their own.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


class RawMessageStore:
    """Append-only raw message storage per session."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._cached_count: int | None = None

    def append(self, messages: list[dict[str, Any]]) -> tuple[int, int]:
        """Append messages. Returns (start_id, end_id) store_id range."""
        start_id = self.count()
        
        def _row_generator():
            for msg in messages:
                content = msg.get("content", "")
                if isinstance(content, (dict, list)):
                    content = json.dumps(content, ensure_ascii=False)
                yield (
                    msg.get("role", "unknown"),
                    str(content) if content else None,
                    msg.get("tool_name"),
                    msg.get("tool_call_id"),
                )

        self._conn.executemany(
            """INSERT INTO raw_messages (role, content, tool_name, tool_call_id)
               VALUES (?, ?, ?, ?)""",
            _row_generator(),
        )
        
        end_id = start_id + len(messages)
        self._cached_count = end_id
        return (start_id, end_id)

    def count(self) -> int:
        if self._cached_count is None:
            row = self._conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()
            self._cached_count = row[0] if row else 0
        return self._cached_count

    def get(self, start: int = 0, end: int | None = None) -> list[dict[str, Any]]:
        if end is not None:
            rows = self._conn.execute(
                "SELECT store_id, role, content, tool_name, tool_call_id, timestamp "
                "FROM raw_messages WHERE store_id >= ? AND store_id < ? "
                "ORDER BY store_id",
                (start, end),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT store_id, role, content, tool_name, tool_call_id, timestamp "
                "FROM raw_messages WHERE store_id >= ? "
                "ORDER BY store_id",
                (start,),
            ).fetchall()
        return [
            {"store_id": r[0], "role": r[1], "content": r[2],
             "tool_name": r[3], "tool_call_id": r[4], "timestamp": r[5]}
            for r in rows
        ]


class LedgerStore:
    """Ledger entry storage per session.

    Stores ledger entries as JSON blobs. Indexes concepts_and_definitions
    in FTS5 for cross-turn and cross-session search.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._cached_count: int | None = None

    def save_turn(self, turn: dict[str, object]) -> None:
        """Insert or replace a ledger entry. Indexes concepts in FTS5."""
        turn_n = turn.get("n")
        is_new = False
        if turn_n is None:
            cur = self._conn.execute("SELECT COALESCE(MAX(turn_n), 0) + 1 FROM ledger_entries")
            turn_n = cur.fetchone()[0]
            turn["n"] = turn_n
            is_new = True
            
        entry_json_blob = json.dumps(turn, ensure_ascii=False)

        self._conn.execute(
            """INSERT OR REPLACE INTO ledger_entries (turn_n, entry_json, validated)
               VALUES (?, ?, ?)""",
            (turn_n, entry_json_blob, 1 if turn.get("validated") else 0),
        )
        
        # Batch insert concepts
        self._conn.execute("DELETE FROM concepts_fts WHERE rowid = ?", (turn_n,))
        
        concepts = turn.get("concepts_and_definitions", []) or []
        def _concept_generator():
            for c in concepts:
                if isinstance(c, dict):
                    yield (turn_n, c.get("term", ""), c.get("definition", ""))
                    
        self._conn.executemany(
            "INSERT INTO concepts_fts (rowid, term, definition) VALUES (?, ?, ?)",
            _concept_generator(),
        )
        
        if is_new and self._cached_count is not None:
            self._cached_count += 1
        elif not is_new:
            # If replacing an existing turn, count doesn't change, but if we don't know, invalidate
            self._cached_count = None

    def get_turns(self) -> list[dict[str, object]]:
        rows = self._conn.execute(
            "SELECT entry_json FROM ledger_entries ORDER BY turn_n"
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get_turn(self, turn_n: int) -> dict[str, object] | None:
        row = self._conn.execute(
            "SELECT entry_json FROM ledger_entries WHERE turn_n = ?", (turn_n,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def turn_count(self) -> int:
        if self._cached_count is None:
            row = self._conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()
            self._cached_count = row[0] if row else 0
        return self._cached_count

    def search_concepts(self, query: str, limit: int = 10) -> list[dict[str, object]]:
        try:
            rows = self._conn.execute(
                """SELECT rowid, term, definition
                   FROM concepts_fts WHERE concepts_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [{"turn_n": r[0], "term": r[1], "definition": r[2]} for r in rows]
