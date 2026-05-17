"""Per-session state store — key-value dict shared across subagents.

Equivalent to ADK's session.state — multiple agents read/write
through state_delta, persisted in SQLite.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


class StateStore:
    """Per-session key-value state, scoped and persisted.

    Scopes:
      'session' — tied to current session, cleared on end
      'user'    — persists across sessions for the same user
      'app'     — application-level, shared across users
    """

    SCOPE_SESSION = "session"
    SCOPE_USER = "user"
    SCOPE_APP = "app"

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # ── Read ──────────────────────────────────────────────────────────

    def get(self, key: str, scope: str = SCOPE_SESSION) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM session_state WHERE key = ? AND scope = ?",
            (key, scope),
        ).fetchone()
        return row[0] if row else None

    def get_all(self, scope: str | None = None) -> dict[str, str]:
        if scope:
            rows = self._conn.execute(
                "SELECT key, value FROM session_state WHERE scope = ? ORDER BY updated_at",
                (scope,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT key, value, scope FROM session_state ORDER BY scope, updated_at"
            ).fetchall()
            return {f"{r[2]}:{r[0]}": r[1] for r in rows}
        return {r[0]: r[1] for r in rows}

    # ── Write ─────────────────────────────────────────────────────────

    def set(self, key: str, value: str, scope: str = SCOPE_SESSION) -> None:
        self._conn.execute(
            """INSERT INTO session_state (key, value, scope, updated_at)
               VALUES (?, ?, ?, unixepoch('subsec'))
               ON CONFLICT(key, scope) DO UPDATE SET
               value = excluded.value,
               updated_at = unixepoch('subsec')""",
            (key, value, scope),
        )

    def set_many(self, delta: dict[str, str], scope: str = SCOPE_SESSION) -> None:
        for key, value in delta.items():
            self.set(key, value, scope)

    def delete(self, key: str, scope: str = SCOPE_SESSION) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM session_state WHERE key = ? AND scope = ?",
            (key, scope),
        )
        return cursor.rowcount > 0

    # ── Scope management ──────────────────────────────────────────────

    def clear_scope(self, scope: str) -> int:
        cursor = self._conn.execute(
            "DELETE FROM session_state WHERE scope = ?", (scope,)
        )
        return cursor.rowcount

    # ── Format for LLM context ────────────────────────────────────────

    def format_for_context(self) -> str | None:
        """Format all state as a context block for LLM injection."""
        all_state = self.get_all()
        if not all_state:
            return None

        # Group by scope
        session_state = {}
        user_state = {}
        app_state = {}
        for key, value in all_state.items():
            if key.startswith("session:"):
                session_state[key[8:]] = value
            elif key.startswith("user:"):
                user_state[key[5:]] = value
            elif key.startswith("app:"):
                app_state[key[4:]] = value

        lines = ["## Shared State (working memory)"]
        if session_state:
            lines.append("\n### Session State")
            for k, v in session_state.items():
                lines.append(f"  • {k}: {v}")
        if user_state:
            lines.append("\n### User State (persistent)")
            for k, v in user_state.items():
                lines.append(f"  • {k}: {v}")
        if app_state:
            lines.append("\n### App State")
            for k, v in app_state.items():
                lines.append(f"  • {k}: {v}")

        return "\n".join(lines) if len(lines) > 1 else None
