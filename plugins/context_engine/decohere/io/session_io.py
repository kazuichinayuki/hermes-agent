"""Session I/O. The ONLY layer that touches files/DB.

Wraps RawMessageStore and LedgerStore over a shared SQLite connection.
No business logic, no formatting, no computation.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..db import configure_connection, ensure_schema, run_migrations
from ..store import RawMessageStore, LedgerStore


class SessionIO:
    """Encapsulates all session persistence for a single session.

    Owns the per-session SQLite database at
    ``<hermes_home>/sessions/<session_id>/decohere.db``.
    RawMessageStore and LedgerStore share a single connection.
    """

    def __init__(self, hermes_home: Path, session_id: str):
        session_dir = hermes_home / "sessions" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(session_dir / "decohere.db")

        conn = sqlite3.connect(db_path)
        configure_connection(conn)
        ensure_schema(conn)
        run_migrations(conn)

        self._conn = conn
        self._raw = RawMessageStore(conn)
        self._ledger = LedgerStore(conn)
        self._session_id = session_id

    # ── Raw messages ──────────────────────────────────────────────────

    def compute_range(self, messages: list[dict]) -> tuple[int, int]:
        return self._raw.append(messages)

    def get_raw_messages(self, start: int = 0, end: int | None = None) -> list[dict]:
        return self._raw.get(start, end)

    def raw_count(self) -> int:
        return self._raw.count()

    # ── Ledger ─────────────────────────────────────────────────────────

    def save_turn(self, turn: dict) -> None:
        self._ledger.save_turn(turn)

    def get_turns(self) -> list[dict]:
        return self._ledger.get_turns()

    def get_turn(self, turn_n: int) -> dict | None:
        return self._ledger.get_turn(turn_n)

    def turn_count(self) -> int:
        return self._ledger.turn_count()

    # ── Session metadata ──────────────────────────────────────────────

    def is_v2(self) -> bool:
        return True

    def close(self) -> None:
        self._conn.close()
