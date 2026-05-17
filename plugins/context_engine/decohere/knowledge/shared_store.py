"""Shared knowledge store — cross-session concept database.

Stores text only (no embeddings).  Embedding-agnostic by design:
different models (DeepSeek, GPT, Gemini) can generate and retrieve
concepts without locking into any single embedding space.

Location: ``HERMES_HOME/decohere_shared.db``
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS shared_concepts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL,
            definition TEXT NOT NULL,
            source_session TEXT NOT NULL,
            source_turn INTEGER,
            imported_by TEXT NOT NULL DEFAULT 'user',
            imported_at REAL NOT NULL DEFAULT (unixepoch('subsec')),
            UNIQUE(term COLLATE NOCASE, source_session)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS shared_concepts_fts
            USING fts5(term, definition, content='', tokenize='unicode61');

        CREATE TABLE IF NOT EXISTS import_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_session TEXT NOT NULL,
            source_turn INTEGER,
            action TEXT NOT NULL,
            timestamp REAL NOT NULL DEFAULT (unixepoch('subsec'))
        );

        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    # Schema version marker
    conn.execute(
        "INSERT OR IGNORE INTO metadata (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )


class SharedStore:
    """Cross-session concept database.  Text-only, embedding-agnostic."""

    def __init__(self, hermes_home: Path):
        db_path = hermes_home / "decohere_shared.db"
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        _ensure_schema(self._conn)
        self._conn.commit()

    # ── CRUD ──────────────────────────────────────────────────────────

    def add_concept(
        self,
        term: str,
        definition: str,
        source_session: str,
        source_turn: int = 0,
        imported_by: str = "user",
    ) -> int | None:
        """Insert a concept.  Returns id, or None if duplicate.

        Dedup rule: same term (case-insensitive) + same source_session = skip.
        """
        try:
            cursor = self._conn.execute(
                "INSERT INTO shared_concepts (term, definition, source_session, "
                "source_turn, imported_by) VALUES (?, ?, ?, ?, ?)",
                (term, definition, source_session, source_turn, imported_by),
            )
            concept_id = cursor.lastrowid

            # FTS5 index
            self._conn.execute(
                "INSERT INTO shared_concepts_fts(rowid, term, definition) VALUES (?, ?, ?)",
                (concept_id, term, definition),
            )

            # Import log
            self._conn.execute(
                "INSERT INTO import_log (source_session, source_turn, action) "
                "VALUES (?, ?, 'import')",
                (source_session, source_turn),
            )
            self._conn.commit()
            return concept_id
        except sqlite3.IntegrityError:
            # Duplicate — skip
            return None

    def get_all(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, term, definition, source_session, source_turn, "
            "imported_by, imported_at FROM shared_concepts ORDER BY imported_at DESC"
        ).fetchall()
        return [
            {
                "id": r[0], "term": r[1], "definition": r[2],
                "source_session": r[3], "source_turn": r[4],
                "imported_by": r[5], "imported_at": r[6],
            }
            for r in rows
        ]

    def get_by_source(self, source_session: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, term, definition, source_session, source_turn, "
            "imported_by, imported_at FROM shared_concepts "
            "WHERE source_session = ? ORDER BY source_turn",
            (source_session,),
        ).fetchall()
        return [
            {
                "id": r[0], "term": r[1], "definition": r[2],
                "source_session": r[3], "source_turn": r[4],
                "imported_by": r[5], "imported_at": r[6],
            }
            for r in rows
        ]

    def remove_by_id(self, concept_id: int) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM shared_concepts WHERE id = ?", (concept_id,)
        )
        if cursor.rowcount:
            # Contentless FTS5 deletion
            self._conn.execute(
                "INSERT INTO shared_concepts_fts(shared_concepts_fts, rowid, term, definition) "
                "VALUES('delete', ?, '', '')",
                (concept_id,),
            )
            self._conn.commit()
            return True
        return False

    def remove_by_source(self, source_session: str) -> int:
        """Remove all concepts from a session. Returns count removed."""
        ids = self._conn.execute(
            "SELECT id FROM shared_concepts WHERE source_session = ?",
            (source_session,),
        ).fetchall()
        count = len(ids)
        for (cid,) in ids:
            self._conn.execute(
                "DELETE FROM shared_concepts WHERE id = ?", (cid,)
            )
            self._conn.execute(
                "INSERT INTO shared_concepts_fts(shared_concepts_fts, rowid, term, definition) "
                "VALUES('delete', ?, '', '')",
                (cid,),
            )
        if count:
            self._conn.execute(
                "INSERT INTO import_log (source_session, source_turn, action) "
                "VALUES (?, 0, 'remove')",
                (source_session,),
            )
            self._conn.commit()
        return count

    def update_definition(self, concept_id: int, new_definition: str) -> bool:
        cursor = self._conn.execute(
            "UPDATE shared_concepts SET definition = ? WHERE id = ?",
            (new_definition, concept_id),
        )
        if cursor.rowcount:
            # Rebuild FTS5 for this concept
            self._conn.execute(
                "INSERT INTO shared_concepts_fts(shared_concepts_fts, rowid, term, definition) "
                "VALUES('delete', ?, '', '')",
                (concept_id,),
            )
            row = self._conn.execute(
                "SELECT term FROM shared_concepts WHERE id = ?", (concept_id,)
            ).fetchone()
            if row:
                self._conn.execute(
                    "INSERT INTO shared_concepts_fts(rowid, term, definition) "
                    "VALUES (?, ?, ?)",
                    (concept_id, row[0], new_definition),
                )
            self._conn.commit()
            return True
        return False

    # ── Query ──────────────────────────────────────────────────────────

    def search_text(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """FTS5 text search. Returns matched concepts."""
        try:
            rows = self._conn.execute(
                "SELECT rowid, term, definition FROM shared_concepts_fts "
                "WHERE shared_concepts_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

        results = []
        for r in rows:
            # Contentless FTS5: index access
            concept_id = r[0]
            detail = self._conn.execute(
                "SELECT id, term, definition, source_session, source_turn, "
                "imported_by, imported_at FROM shared_concepts WHERE id = ?",
                (concept_id,),
            ).fetchone()
            if detail:
                results.append({
                    "id": detail[0], "term": detail[1], "definition": detail[2],
                    "source_session": detail[3], "source_turn": detail[4],
                    "imported_by": detail[5], "imported_at": detail[6],
                })
        return results

    def count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM shared_concepts"
        ).fetchone()
        return row[0] if row else 0

    def source_summary(self) -> list[dict[str, Any]]:
        """Return per-session concept counts."""
        rows = self._conn.execute(
            "SELECT source_session, COUNT(*) as cnt, "
            "MAX(imported_at) as last_import "
            "FROM shared_concepts GROUP BY source_session "
            "ORDER BY last_import DESC"
        ).fetchall()
        return [
            {"session": r[0], "count": r[1], "last_import": r[2]}
            for r in rows
        ]

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()
