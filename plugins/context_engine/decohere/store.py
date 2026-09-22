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
        if turn_n is None:
            cur = self._conn.execute("SELECT COALESCE(MAX(turn_n), 0) + 1 FROM ledger_entries")
            turn_n = cur.fetchone()[0]
            turn["n"] = turn_n
        else:
            cur = self._conn.execute("SELECT entry_json FROM ledger_entries WHERE turn_n = ?", (turn_n,))
            row = cur.fetchone()
            if row:
                existing = parse_json_field(row[0], {})
                for key in ("message_range", "tools", "files_touched", "n"):
                    if key in existing and key not in turn:
                        turn[key] = existing[key]
            
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


class TrajectoryStore:
    """Storage for complete agent trajectories and compiled decision points.

    Compiles runtime agent experience into SQLite tables for trajectory replay,
    evaluation, and distillation into fast System 1 models (e.g. Laya).
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def save_trajectory(
        self,
        session_id: str,
        turn_n: int,
        model: str,
        completed: bool,
        trajectory: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Insert a complete trajectory record."""
        cur = self._conn.execute(
            """INSERT INTO trajectories (session_id, turn_n, model, completed, trajectory_json, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                turn_n,
                model or "",
                1 if completed else 0,
                json.dumps(trajectory, ensure_ascii=False),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        return cur.lastrowid or 0

    def save_decision_points(
        self,
        session_id: str,
        turn_n: int,
        decision_points: list[dict[str, Any]],
    ) -> int:
        """Insert extracted decision points (choice, score, noul)."""
        if not decision_points:
            return 0
        rows = [
            (
                session_id,
                turn_n,
                dp.get("decision_type", "choice"),
                json.dumps(dp.get("state", {}), ensure_ascii=False),
                json.dumps(dp.get("decision", {}), ensure_ascii=False),
                str(dp.get("target_label", "")),
            )
            for dp in decision_points
        ]
        self._conn.executemany(
            """INSERT INTO decision_points (session_id, turn_n, decision_type, state_json, decision_json, target_label)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )
        return len(rows)

    def get_trajectories(self, session_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch trajectory records."""
        if session_id:
            cur = self._conn.execute(
                """SELECT id, session_id, turn_n, model, completed, trajectory_json, metadata_json, created_at
                   FROM trajectories WHERE session_id = ? ORDER BY turn_n ASC LIMIT ?""",
                (session_id, limit),
            )
        else:
            cur = self._conn.execute(
                """SELECT id, session_id, turn_n, model, completed, trajectory_json, metadata_json, created_at
                   FROM trajectories ORDER BY id DESC LIMIT ?""",
                (limit,),
            )
        results = []
        for r in cur.fetchall():
            try:
                traj = json.loads(r[5])
            except Exception:
                traj = {}
            try:
                meta = json.loads(r[6])
            except Exception:
                meta = {}
            results.append({
                "id": r[0],
                "session_id": r[1],
                "turn_n": r[2],
                "model": r[3],
                "completed": bool(r[4]),
                "trajectory": traj,
                "metadata": meta,
                "created_at": r[7],
            })
        return results

    def get_decision_points(
        self,
        session_id: str | None = None,
        decision_type: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Fetch extracted decision points."""
        query = "SELECT id, session_id, turn_n, decision_type, state_json, decision_json, target_label, created_at FROM decision_points"
        params: list[Any] = []
        clauses = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if decision_type:
            clauses.append("decision_type = ?")
            params.append(decision_type)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id ASC LIMIT ?"
        params.append(limit)

        cur = self._conn.execute(query, tuple(params))
        results = []
        for r in cur.fetchall():
            try:
                state = json.loads(r[4])
            except Exception:
                state = {}
            try:
                decision = json.loads(r[5])
            except Exception:
                decision = {}
            results.append({
                "id": r[0],
                "session_id": r[1],
                "turn_n": r[2],
                "decision_type": r[3],
                "state": state,
                "decision": decision,
                "target_label": r[6],
                "created_at": r[7],
            })
        return results

    def trajectory_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM trajectories").fetchone()
        return row[0] if row else 0

    def decision_point_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM decision_points").fetchone()
        return row[0] if row else 0


class NogoodClauseStore:
    """Persistence for CDCL Nogood clauses learned during execution."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def save_clause(self, session_id: str, clause: dict[str, Any]) -> None:
        pattern = clause.get("pattern", {})
        pattern_json = json.dumps(pattern, sort_keys=True) if isinstance(pattern, dict) else str(pattern)
        self._conn.execute(
            """INSERT OR REPLACE INTO nogood_clauses
               (clause_id, session_id, predicate, tool_name, pattern_json, reason, scope, hit_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                clause["clause_id"],
                session_id,
                clause["predicate"],
                clause["tool_name"],
                pattern_json,
                clause["reason"],
                clause.get("scope", "session"),
                clause.get("hit_count", 0),
            ),
        )

    def load_clauses(self, session_id: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            """SELECT clause_id, predicate, tool_name, pattern_json, reason, scope, hit_count, created_at
               FROM nogood_clauses WHERE session_id = ? ORDER BY created_at ASC""",
            (session_id,),
        )
        results = []
        for r in cur.fetchall():
            try:
                pattern = json.loads(r[3])
            except Exception:
                pattern = {}
            results.append({
                "clause_id": r[0],
                "predicate": r[1],
                "tool_name": r[2],
                "pattern": pattern,
                "reason": r[4],
                "scope": r[5],
                "hit_count": r[6],
            })
        return results

    def clause_count(self, session_id: str | None = None) -> int:
        if session_id:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM nogood_clauses WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) FROM nogood_clauses").fetchone()
        return row[0] if row else 0


class ArchiveStore:
    """Out-of-band archive for raw tool outputs (e.g. evaporated Redulogs)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def archive(self, session_id: str, tool_name: str, raw_result: str, state: str) -> int:
        cur = self._conn.execute(
            """INSERT INTO tool_results_archive (session_id, tool_name, raw_result, state)
               VALUES (?, ?, ?, ?)""",
            (session_id, tool_name, raw_result, state),
        )
        return cur.lastrowid or 0

    def get_archives(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            """SELECT id, session_id, tool_name, raw_result, state, created_at
               FROM tool_results_archive WHERE session_id = ? ORDER BY id DESC LIMIT ?""",
            (session_id, limit),
        )
        return [
            {
                "id": r[0],
                "session_id": r[1],
                "tool_name": r[2],
                "raw_result": r[3],
                "state": r[4],
                "created_at": r[5],
            }
            for r in cur.fetchall()
        ]


