"""Shared utilities for the `hermes decohere` CLI command group.

All commands import from here. No circular dependencies, no side-effects.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------

class DecohereCLIError(Exception):
    """Base exception for all decohere CLI errors."""


class ProfileNotFoundError(DecohereCLIError):
    """The specified profile does not exist."""


class NoSessionsError(DecohereCLIError):
    """No sessions with decohere data found."""


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def resolve_hermes_home(
    profile: str | None = None,
    home: str | None = None,
) -> Path:
    """Resolve hermes_home path.

    Priority: profile > home > HERMES_HOME env > ~/.hermes
    """
    if profile and home:
        raise DecohereCLIError("--profile and --home are mutually exclusive")

    if home:
        p = Path(home).expanduser()
        if not p.is_dir():
            raise DecohereCLIError(f"Error: {p} does not exist")
        return p

    if profile:
        try:
            # Use subprocess to avoid import-time side-effects
            import subprocess
            import sys

            result = subprocess.run(
                [sys.executable, "-m", "hermes", "profile", "show", profile, "--json"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                msg = result.stderr.strip() or result.stdout.strip()
                raise ProfileNotFoundError(
                    f"Profile '{profile}' not found. "
                    f"Available: use 'hermes profile list' to see available profiles."
                )
            data = json.loads(result.stdout)
            return Path(data["home"])
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            raise DecohereCLIError(f"Error resolving profile '{profile}': {e}") from e

    # 3. HERMES_HOME env
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        p = Path(env_home).expanduser()
        if p.is_dir():
            return p

    # 4. Default: ~/.hermes
    default = Path.home() / ".hermes"
    if not default.is_dir():
        raise DecohereCLIError(
            "Error: no hermes home found. Run 'hermes setup' first."
        )
    return default


def resolve_session(
    hermes_home: Path,
    session_id: str | None,
) -> tuple[str, Path]:
    """Resolve session_id → (session_id, db_path).

    If session_id is None, find the most recently modified decohere.db.
    """
    sessions_dir = hermes_home / "sessions"

    if session_id:
        session_dir = sessions_dir / session_id
        db_path = session_dir / "decohere.db"
        if not db_path.exists():
            raise DecohereCLIError(
                f"Error: session '{session_id}' not found"
            )
        return (session_id, db_path)

    # Find most recent
    if not sessions_dir.is_dir():
        raise NoSessionsError("(no sessions found)")

    dbs: list[tuple[Path, Path]] = []
    for session_dir in sorted(sessions_dir.iterdir(), reverse=True):
        if not session_dir.is_dir():
            continue
        db = session_dir / "decohere.db"
        if db.exists():
            dbs.append((session_dir, db))

    if not dbs:
        raise NoSessionsError("(no sessions found)")

    # Sort by modification time, most recent first
    dbs.sort(key=lambda x: x[1].stat().st_mtime, reverse=True)
    return (dbs[0][0].name, dbs[0][1])


def list_all_profile_sessions() -> list[dict[str, Any]]:
    """Scan all known profiles for their sessions. Returns list of dicts.

    Each dict: {profile, session_id, turns, raw_msgs, size, last_updated}
    """
    import subprocess
    import sys

    results: list[dict[str, Any]] = []

    # Collect profile homes
    profile_homes: list[tuple[str, Path]] = []

    # Default profile
    default = Path.home() / ".hermes"
    if default.is_dir():
        profile_homes.append(("default", default))

    # Named profiles
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "hermes", "profile", "list", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            for info in json.loads(proc.stdout):
                name = info.get("name", "unknown")
                home = Path(info.get("path", ""))
                if name != "default" and home.is_dir():
                    profile_homes.append((name, home))
    except Exception:
        pass  # best-effort

    for profile_name, home in profile_homes:
        sessions_dir = home / "sessions"
        if not sessions_dir.is_dir():
            continue
        for sd in sorted(sessions_dir.iterdir(), reverse=True):
            if not sd.is_dir():
                continue
            db = sd / "decohere.db"
            if not db.exists():
                continue
            info = _get_session_summary(db, sd.name)
            info["profile"] = profile_name
            results.append(info)

    return results


def _get_session_summary(db_path: Path, session_id: str) -> dict[str, Any]:
    """Get quick summary stats from a decohere.db without opening it fully."""
    try:
        conn = open_db(db_path, readonly=True)
        turns = conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()
        raw_msgs = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()
        last = conn.execute(
            "SELECT MAX(posted_at) FROM ledger_entries"
        ).fetchone()
        size = db_path.stat().st_size
        conn.close()
        return {
            "session_id": session_id,
            "turns": turns[0] if turns else 0,
            "raw_msgs": raw_msgs[0] if raw_msgs else 0,
            "size": size,
            "last_updated": last[0] if last and last[0] else 0,
        }
    except Exception:
        return {
            "session_id": session_id,
            "turns": 0,
            "raw_msgs": 0,
            "size": 0,
            "last_updated": 0,
        }


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def open_db(db_path: Path, readonly: bool = True) -> sqlite3.Connection:
    """Open decohere.db.

    Read-only connections use URI mode=ro to avoid holding write locks.
    """
    if readonly:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    return conn


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_timestamp(ts: float | None) -> str:
    """Convert unixepoch to '2026-05-09 20:15:03'."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return "—"


def format_relative_time(ts: float | None) -> str:
    """Convert unixepoch to relative time string."""
    if not ts:
        return "—"
    import time
    delta = time.time() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    if delta < 172800:
        return "yesterday"
    if delta < 604800:
        return f"{int(delta / 86400)}d ago"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def format_size(size: int) -> str:
    """Format byte size in human-readable form."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def parse_json_field(raw: str | None, default: Any = None) -> Any:
    """Safely parse entry_json string. Corrupted JSON → default."""
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def audit_log(hermes_home: Path, entry: dict[str, Any]) -> None:
    """Write a JSON line to {hermes_home}/decohere_audit.log."""
    log_path = hermes_home / "decohere_audit.log"
    entry["ts"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # best-effort — don't crash on audit log failure


# ---------------------------------------------------------------------------
# Nested field access helpers (for edit command)
# ---------------------------------------------------------------------------

def get_nested_field(data: dict[str, Any], path: str) -> Any:
    """Get a nested value by dotted/index path.

    Examples:
        user_intent → data["user_intent"]
        narrative.summary → data["narrative"]["summary"]
        concepts_and_definitions[0].term → data["concepts_and_definitions"][0]["term"]
    """
    import re

    parts = re.split(r"\.|(?=\[\d+\])", path)
    parts = [p for p in parts if p]  # filter empty
    current: Any = data
    for part in parts:
        m = re.match(r"\[(\d+)\]", part)
        if m:
            idx = int(m.group(1))
            if isinstance(current, (list, tuple)) and 0 <= idx < len(current):
                current = current[idx]
            else:
                raise KeyError(f"Index {idx} out of range at '{part}' in '{path}'")
        else:
            if isinstance(current, dict):
                current = current[part]
            else:
                raise KeyError(f"Cannot access key '{part}' on non-dict in '{path}'")
    return current


def set_nested_field(
    data: dict[str, Any],
    path: str,
    value: Any,
) -> dict[str, Any]:
    """Set a nested value by dotted/index path. Returns modified copy.

    Examples:
        user_intent → data["user_intent"] = value
        narrative.summary → data["narrative"]["summary"] = value
        concepts_and_definitions[0].term → data["concepts_and_definitions"][0]["term"] = value
    """
    import re
    import copy

    data = copy.deepcopy(data)
    parts = re.split(r"\.|(?=\[\d+\])", path)
    parts = [p for p in parts if p]
    if not parts:
        raise ValueError(f"Invalid field path: '{path}'")

    # Navigate to parent
    parent: Any = data
    for part in parts[:-1]:
        m = re.match(r"\[(\d+)\]", part)
        if m:
            idx = int(m.group(1))
            if isinstance(parent, (list, tuple)) and 0 <= idx < len(parent):
                parent = parent[idx]
            else:
                raise KeyError(f"Index {idx} out of range in '{path}'")
        else:
            if isinstance(parent, dict):
                parent = parent[part]
            else:
                raise KeyError(f"Cannot access '{part}' in '{path}'")

    # Set on parent
    last = parts[-1]
    m = re.match(r"\[(\d+)\]", last)
    if m:
        idx = int(m.group(1))
        if isinstance(parent, list) and 0 <= idx < len(parent):
            parent[idx] = value
        else:
            raise KeyError(f"Index {idx} out of range in '{path}'")
    else:
        if isinstance(parent, dict):
            parent[last] = value
        else:
            raise KeyError(f"Cannot set '{last}' on non-dict in '{path}'")

    return data


# ---------------------------------------------------------------------------
# DB stats helpers
# ---------------------------------------------------------------------------

def get_db_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return comprehensive stats from a decohere.db connection."""
    # All returns are tuples — no row_factory on these connections
    entry_count = conn.execute(
        "SELECT COUNT(*) FROM ledger_entries"
    ).fetchone()[0]
    raw_count = conn.execute(
        "SELECT COUNT(*) FROM raw_messages"
    ).fetchone()[0]
    validated = conn.execute(
        "SELECT COUNT(*) FROM ledger_entries WHERE validated = 1"
    ).fetchone()[0]
    pending = entry_count - validated

    # Top tools — row = (entry_json,)
    tools: dict[str, int] = {}
    for row in conn.execute("SELECT entry_json FROM ledger_entries"):
        entry = parse_json_field(row[0], {})
        for t in entry.get("tools", []) or []:
            name = t.get("name", t) if isinstance(t, dict) else t
            tools[name] = tools.get(name, 0) + 1
    top_tools = sorted(tools.items(), key=lambda x: x[1], reverse=True)[:5]

    # Top concepts from FTS5
    try:
        fts_rows = conn.execute(
            "SELECT term, COUNT(*) as cnt FROM concepts_fts GROUP BY term ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
        top_concepts = [(r[0], r[1]) for r in fts_rows]
    except sqlite3.OperationalError:
        top_concepts = []

    # First / last turn timestamps
    first = conn.execute(
        "SELECT MIN(posted_at) FROM ledger_entries"
    ).fetchone()[0]
    last = conn.execute(
        "SELECT MAX(posted_at) FROM ledger_entries"
    ).fetchone()[0]

    # Orphan counts
    orphans = conn.execute(
        "SELECT COUNT(*) FROM raw_messages WHERE store_id NOT IN "
        "(SELECT CAST(value AS INTEGER) FROM ledger_entries, json_each(entry_json, '$.message_range'))"
    ).fetchone()[0] if entry_count > 0 else 0

    return {
        "entry_count": entry_count,
        "raw_count": raw_count,
        "validated": validated,
        "pending": pending,
        "top_tools": top_tools,
        "top_concepts": top_concepts,
        "first_turn": first,
        "last_turn": last,
        "avg_messages_per_turn": raw_count / entry_count if entry_count else 0,
        "orphans": orphans,
    }


def get_orphan_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Count orphan records by checking message_range coverage."""
    import json

    orphan_msgs = 0
    orphan_fts = 0

    # Collect covered store_ids from ledger entries
    rows = conn.execute("SELECT entry_json FROM ledger_entries").fetchall()
    covered: set[int] = set()
    for (entry_json,) in rows:
        try:
            entry = json.loads(entry_json)
        except (json.JSONDecodeError, TypeError):
            continue
        msg_range = entry.get("message_range", [])
        if msg_range and len(msg_range) >= 2:
            covered.update(range(int(msg_range[0]), int(msg_range[-1]) + 1))

    # Count raw_messages outside covered ranges
    raw_rows = conn.execute("SELECT store_id FROM raw_messages").fetchall()
    orphan_msgs = sum(1 for (sid,) in raw_rows if sid not in covered)

    # Orphan FTS5
    try:
        orphan_fts = conn.execute(
            "SELECT COUNT(*) FROM concepts_fts WHERE rowid NOT IN "
            "(SELECT turn_n FROM ledger_entries)"
        ).fetchone()[0]
    except Exception:
        pass

    return {"orphan_msgs": orphan_msgs, "orphan_fts": orphan_fts}
