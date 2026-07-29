"""Decohere TUI — prompt_toolkit-based vim navigation + tab completion.

Two classes:
  DecohereCLI    — memoir-style command-loop (kept for backward compat, tested)
  DecohereVimTUI — prompt_toolkit Application with vim keybindings + tab complete

Vim bindings:
  j / ↓         move down           (NORMAL)
  k / ↑         move up             (NORMAL)
  Enter         select / show       (NORMAL)
  /              enter search mode  (NORMAL)
  :              enter command mode (NORMAL)
  q              quit               (NORMAL)
  Esc            back / cancel      (all modes)
  Tab            complete           (COMMAND)
  Ctrl-c         quit               (all modes)

Only ONE Application runs at a time — no nesting. All keyboard input goes
through prompt_toolkit keybindings; no input() calls inside the app.
"""

from __future__ import annotations

import json
import readline  # noqa: F401 - enables input history for CLI mode
import shlex
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ._shared import get_nested_field, set_nested_field

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    RICH_AVAILABLE = True
except ImportError:  # pragma: no cover - rich installed in normal hermes env
    Console = None
    Table = None
    Panel = None
    RICH_AVAILABLE = False


# ── Small output helpers ───────────────────────────────────────────────

def _dim(text: str) -> str:
    return f"\033[2m{text}\033[0m"


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m"


def _green(text: str) -> str:
    return f"\033[32m{text}\033[0m"


def _red(text: str) -> str:
    return f"\033[31m{text}\033[0m"


def _yellow(text: str) -> str:
    return f"\033[33m{text}\033[0m"


def _truncate(text: str | None, width: int = 80) -> str:
    text = text or ""
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


# ── DB helpers ────────────────────────────────────────────────────────

def _migrate_path(db_path: str | Path) -> None:
    """Ensure a decohere DB has the current schema."""
    from plugins.context_engine.decohere.db import ensure_schema, run_migrations

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        ensure_schema(conn)
        run_migrations(conn)
        conn.commit()
    finally:
        conn.close()


def _open_db_readonly(db_path: str | Path) -> sqlite3.Connection:
    _migrate_path(db_path)
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _open_db_readwrite(db_path: str | Path) -> sqlite3.Connection:
    _migrate_path(db_path)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _load_entry_json(conn: sqlite3.Connection, turn_n: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT entry_json FROM ledger_entries WHERE turn_n = ?", (turn_n,)
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None


def _save_entry_json(
    conn: sqlite3.Connection,
    turn_n: int,
    entry: dict,
    old_entry: dict | None = None,
) -> None:
    """Save entry JSON and refresh FTS rows for this turn.

    ``old_entry`` is accepted for backward compatibility with earlier tests and
    call sites, but regular FTS5 v2 can delete by rowid directly.
    """
    conn.execute(
        "UPDATE ledger_entries SET entry_json = ?, validated = 0 WHERE turn_n = ?",
        (json.dumps(entry, ensure_ascii=False), turn_n),
    )
    _rebuild_fts(conn, turn_n, entry)


def _rebuild_fts(conn: sqlite3.Connection, turn_n: int, entry: dict) -> None:
    """Rebuild FTS rows for one turn.

    Rowid scheme: ``turn_n * 1000 + concept_index``.
    We delete a bounded rowid range, then insert current concepts. Schema v2
    uses regular FTS5, not contentless FTS5, so plain DELETE is legal.
    """
    base = turn_n * 1000
    for idx in range(1000):
        conn.execute("DELETE FROM concepts_fts WHERE rowid = ?", (base + idx,))

    for idx, concept in enumerate(entry.get("concepts_and_definitions", []) or []):
        if isinstance(concept, dict):
            conn.execute(
                "INSERT INTO concepts_fts(rowid, term, definition) VALUES (?, ?, ?)",
                (
                    base + idx,
                    concept.get("term", ""),
                    concept.get("definition", ""),
                ),
            )


# ── Data model ────────────────────────────────────────────────────────

@dataclass
class SessionInfo:
    index: int
    session_id: str
    db_path: Path
    turns: int
    messages: int
    concepts: int
    states: int
    size: int


def _parse_entry(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


# ═══════════════════════════════════════════════════════════════════════
# DecohereCLI — memoir-style command-loop (backward compatible, tested)
# ═══════════════════════════════════════════════════════════════════════

class DecohereCLI:
    """Scrolling command-loop UI for decohere data."""

    def __init__(self, hermes_home: Path, initial_db: Path | None = None):
        self.hermes_home = Path(hermes_home)
        self.current: SessionInfo | None = None
        self.running = True
        self.console = Console() if RICH_AVAILABLE else None
        self._sessions_cache: list[SessionInfo] = []
        if initial_db:
            self._open_db_path(initial_db)

    # ── printing ------------------------------------------------------

    def print(self, text: str = "", style: str | None = None) -> None:
        if self.console and style:
            self.console.print(text, style=style)
        elif self.console:
            self.console.print(text)
        else:
            print(text)

    def success(self, text: str) -> None:
        self.print(f"✓ {text}", "green")

    def error(self, text: str) -> None:
        self.print(f"✗ {text}", "red")

    def info(self, text: str) -> None:
        self.print(text, "dim")

    # ── main loop -----------------------------------------------------

    def run(self) -> int:
        self._welcome()
        while self.running:
            try:
                command = input(self._prompt()).strip()
            except KeyboardInterrupt:
                self.print("\nBye.")
                break
            except EOFError:
                self.print("\nBye.")
                break
            if not command:
                continue
            try:
                self.execute(command)
            except Exception as exc:  # deliberately catch UI command failures
                self.error(str(exc))
        return 0

    def _welcome(self) -> None:
        title = "Decohere TUI"
        subtitle = "browse/edit ledger turns, concepts, raw messages, and state"
        if self.console and Panel:
            self.console.print(Panel(f"{title}\n{subtitle}", border_style="blue"))
        else:
            self.print(_bold(title))
            self.print(_dim(subtitle))
        self.info("Type help for commands, quit to exit.")
        self.print()

    def _prompt(self) -> str:
        if self.current:
            sid = self.current.session_id[:18]
            return f"decohere[{sid}]> "
        return "decohere> "

    # ── dispatch ------------------------------------------------------

    def execute(self, command: str) -> bool:
        """Execute one command. Returns False if it exited the app."""
        try:
            parts = shlex.split(command)
        except ValueError as exc:
            self.error(f"Parse error: {exc}")
            return True
        if not parts:
            return True

        cmd = parts[0].lower()
        args = parts[1:]

        aliases = {
            "?": "help",
            "h": "help",
            "q": "quit",
            "exit": "quit",
            "ls": "sessions",
            "o": "open",
            "t": "turns",
            "s": "show",
            "c": "concepts",
            "m": "messages",
            "msg": "message",
        }
        cmd = aliases.get(cmd, cmd)

        if cmd == "help":
            self.cmd_help(args)
        elif cmd == "quit":
            self.running = False
            self.print("Bye.")
            return False
        elif cmd == "sessions":
            self.cmd_sessions(args)
        elif cmd == "open":
            self.cmd_open(args)
        elif cmd == "close":
            self.current = None
            self.success("session closed")
        elif cmd == "current":
            self.cmd_current(args)
        elif cmd == "turns":
            self._require_session()
            self.cmd_turns(args)
        elif cmd == "show":
            self._require_session()
            self.cmd_show(args)
        elif cmd == "edit":
            self._require_session()
            self.cmd_edit(args)
        elif cmd == "concepts":
            self._require_session()
            self.cmd_concepts(args)
        elif cmd == "concept":
            self._require_session()
            self.cmd_concept(args)
        elif cmd == "concept-set":
            self._require_session()
            self.cmd_concept_set(args)
        elif cmd == "concept-add":
            self._require_session()
            self.cmd_concept_add(args)
        elif cmd == "concept-del":
            self._require_session()
            self.cmd_concept_del(args)
        elif cmd == "messages":
            self._require_session()
            self.cmd_messages(args)
        elif cmd == "message":
            self._require_session()
            self.cmd_message(args)
        elif cmd == "state":
            self._require_session()
            self.cmd_state(args)
        elif cmd == "state-set":
            self._require_session()
            self.cmd_state_set(args)
        elif cmd == "state-del":
            self._require_session()
            self.cmd_state_del(args)
        else:
            self.error(f"unknown command: {cmd}")
            self.info("Type help for commands.")
        return True

    def _require_session(self) -> None:
        if not self.current:
            raise RuntimeError("No session open. Use: sessions; open <index|session-id>")

    # ── commands ------------------------------------------------------

    def cmd_help(self, args: list[str]) -> None:
        self.print("Commands", "bold")
        self.print("  sessions                         list decohere sessions")
        self.print("  open <index|session-id>           open a session")
        self.print("  close/current                     manage selected session")
        self.print("  turns [query]                     list/search ledger turns")
        self.print("  show <turn>                       show turn detail")
        self.print("  edit <turn> <field> <value>       edit turn field")
        self.print("  concepts [query]                  list/search concepts")
        self.print("  concept <turn> <idx>              show concept")
        self.print("  concept-set <turn> <idx> term|definition <value>")
        self.print("  concept-add <turn> <term> :: <definition>")
        self.print("  concept-del <turn> <idx>")
        self.print("  messages [query]                  list/search raw messages")
        self.print("  message <store_id>                show one raw message")
        self.print("  state                             list session_state")
        self.print("  state-set <scope> <key> <value>   write state")
        self.print("  state-del <scope> <key>           delete state")
        self.print("  quit                              exit")

    def cmd_sessions(self, args: list[str]) -> None:
        sessions = self._scan_sessions()
        self._sessions_cache = sessions
        if not sessions:
            self.info("No decohere sessions found.")
            return
        if self.console and Table:
            table = Table(title=f"Decohere sessions — {self.hermes_home}")
            table.add_column("#", justify="right")
            table.add_column("session")
            table.add_column("turns", justify="right")
            table.add_column("msgs", justify="right")
            table.add_column("concepts", justify="right")
            table.add_column("state", justify="right")
            table.add_column("size", justify="right")
            for s in sessions:
                table.add_row(
                    str(s.index), s.session_id, str(s.turns), str(s.messages),
                    str(s.concepts), str(s.states), f"{s.size / 1024:.1f}K"
                )
            self.console.print(table)
        else:
            for s in sessions:
                self.print(
                    f"{s.index:>3} {s.session_id:<36} "
                    f"turns={s.turns:<3} msgs={s.messages:<4} concepts={s.concepts:<4} state={s.states}"
                )

    def cmd_open(self, args: list[str]) -> None:
        if not args:
            raise RuntimeError("Usage: open <index|session-id|path/to/decohere.db>")
        target = args[0]

        # Direct .db file path — open any decohere database
        looks_like_path = "/" in target or "\\" in target or target.endswith(".db")
        if looks_like_path:
            self._open_db_path(Path(target).expanduser())
            return

        # Session lookup by index or ID (existing behaviour)
        sessions = self._sessions_cache or self._scan_sessions()
        chosen = None
        if target.isdigit():
            idx = int(target)
            chosen = next((s for s in sessions if s.index == idx), None)
        else:
            chosen = next((s for s in sessions if s.session_id == target), None)
            if not chosen:
                matches = [s for s in sessions if s.session_id.startswith(target)]
                if len(matches) == 1:
                    chosen = matches[0]
        if not chosen:
            raise RuntimeError(f"Session not found: {target}")
        self.current = chosen
        self.success(f"opened {chosen.session_id}")

    def _open_db_path(self, db_path: Path) -> None:
        """Open a decohere database directly by file path."""
        if db_path.is_dir():
            db_path = db_path / "decohere.db"
        if not db_path.is_file():
            raise RuntimeError(f"Database file not found: {db_path}")
        self.current = SessionInfo(
            index=-1,
            session_id=db_path.stem if db_path.stem != "decohere" else db_path.parent.name,
            db_path=db_path,
            turns=0, messages=0, concepts=0, states=0, size=db_path.stat().st_size,
        )
        try:
            conn = _open_db_readonly(db_path)
            self.current.turns = conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
            self.current.messages = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
            self.current.concepts = conn.execute("SELECT COUNT(*) FROM concepts_fts").fetchone()[0]
            self.current.states = conn.execute("SELECT COUNT(*) FROM session_state").fetchone()[0]
            conn.close()
        except Exception:
            pass
        self.success(f"opened {db_path}")

    def cmd_current(self, args: list[str]) -> None:
        if not self.current:
            self.info("No session open.")
            return
        s = self.current
        self.print(f"{s.session_id}\n  db={s.db_path}\n  turns={s.turns} messages={s.messages} concepts={s.concepts} state={s.states}")

    def cmd_turns(self, args: list[str]) -> None:
        query = " ".join(args).lower() if args else ""
        conn = self._conn_ro()
        rows = conn.execute(
            "SELECT turn_n, entry_json, posted_at, validated FROM ledger_entries ORDER BY turn_n DESC"
        ).fetchall()
        conn.close()
        data = []
        for turn_n, entry_json, posted_at, validated in rows:
            entry = _parse_entry(entry_json)
            task = (entry.get("relevant_metadata") or {}).get("task", "")
            if query and query not in json.dumps(entry, ensure_ascii=False).lower():
                continue
            data.append((turn_n, bool(validated), task, len(entry.get("concepts_and_definitions", []) or [])))
        for turn_n, validated, task, nconcepts in data[:80]:
            mark = "✓" if validated else " "
            self.print(f"[{mark}] T{turn_n:<4} c={nconcepts:<3} {_truncate(task, 90)}")
        self.info(f"{len(data)} turn(s)")

    def cmd_show(self, args: list[str]) -> None:
        if not args:
            raise RuntimeError("Usage: show <turn>")
        turn_n = int(args[0])
        conn = self._conn_ro()
        entry = _load_entry_json(conn, turn_n)
        row = conn.execute("SELECT validated FROM ledger_entries WHERE turn_n=?", (turn_n,)).fetchone()
        conn.close()
        if not entry:
            raise RuntimeError(f"Turn not found: {turn_n}")
        self._print_turn(turn_n, entry, bool(row[0]) if row else False)

    def cmd_edit(self, args: list[str]) -> None:
        if len(args) < 3:
            raise RuntimeError("Usage: edit <turn> <field> <value>")
        turn_n = int(args[0])
        field = args[1]
        value = " ".join(args[2:])
        try:
            parsed_value = json.loads(value)
        except json.JSONDecodeError:
            parsed_value = value
        conn = self._conn_rw()
        old = _load_entry_json(conn, turn_n)
        if not old:
            conn.close()
            raise RuntimeError(f"Turn not found: {turn_n}")
        new = set_nested_field(old, field, parsed_value)
        conn.execute("BEGIN IMMEDIATE")
        _save_entry_json(conn, turn_n, new, old)
        conn.commit()
        conn.close()
        self.success(f"updated T{turn_n}.{field}")

    def cmd_concepts(self, args: list[str]) -> None:
        query = " ".join(args).lower() if args else ""
        concepts = self._load_concepts()
        if query:
            concepts = [c for c in concepts if query in c["term"].lower() or query in c["definition"].lower()]
        for c in concepts[:120]:
            self.print(f"T{c['turn']:<4} #{c['idx']:<2} {_yellow(c['term'])}: {_truncate(c['definition'], 100)}")
        self.info(f"{len(concepts)} concept(s)")

    def cmd_concept(self, args: list[str]) -> None:
        turn, idx = self._parse_turn_idx(args, "Usage: concept <turn> <idx>")
        concept = self._get_concept(turn, idx)
        self.print(f"T{turn} #{idx}\n  term: {concept.get('term','')}\n  definition: {concept.get('definition','')}")

    def cmd_concept_set(self, args: list[str]) -> None:
        if len(args) < 4:
            raise RuntimeError("Usage: concept-set <turn> <idx> term|definition <value>")
        turn = int(args[0]); idx = int(args[1]); field = args[2]
        if field not in {"term", "definition"}:
            raise RuntimeError("field must be term or definition")
        value = " ".join(args[3:])
        conn = self._conn_rw()
        old = _load_entry_json(conn, turn)
        if not old:
            conn.close(); raise RuntimeError(f"Turn not found: {turn}")
        concepts = old.get("concepts_and_definitions", []) or []
        if idx < 0 or idx >= len(concepts):
            conn.close(); raise RuntimeError(f"Concept index out of range: {idx}")
        new = json.loads(json.dumps(old))
        new["concepts_and_definitions"][idx][field] = value
        conn.execute("BEGIN IMMEDIATE")
        _save_entry_json(conn, turn, new, old)
        conn.commit(); conn.close()
        self.success(f"updated concept T{turn} #{idx} {field}")

    def cmd_concept_add(self, args: list[str]) -> None:
        if len(args) < 2:
            raise RuntimeError("Usage: concept-add <turn> <term> :: <definition>")
        turn = int(args[0])
        joined = " ".join(args[1:])
        if "::" in joined:
            term, definition = [x.strip() for x in joined.split("::", 1)]
        else:
            term, definition = joined.strip(), ""
        conn = self._conn_rw()
        old = _load_entry_json(conn, turn)
        if not old:
            conn.close(); raise RuntimeError(f"Turn not found: {turn}")
        new = json.loads(json.dumps(old))
        new.setdefault("concepts_and_definitions", []).append({"term": term, "definition": definition})
        conn.execute("BEGIN IMMEDIATE")
        _save_entry_json(conn, turn, new, old)
        conn.commit(); conn.close()
        self.success(f"added concept to T{turn}: {term}")

    def cmd_concept_del(self, args: list[str]) -> None:
        turn, idx = self._parse_turn_idx(args, "Usage: concept-del <turn> <idx>")
        conn = self._conn_rw()
        old = _load_entry_json(conn, turn)
        if not old:
            conn.close(); raise RuntimeError(f"Turn not found: {turn}")
        concepts = old.get("concepts_and_definitions", []) or []
        if idx < 0 or idx >= len(concepts):
            conn.close(); raise RuntimeError(f"Concept index out of range: {idx}")
        removed = concepts[idx]
        new = json.loads(json.dumps(old))
        del new["concepts_and_definitions"][idx]
        conn.execute("BEGIN IMMEDIATE")
        _save_entry_json(conn, turn, new, old)
        conn.commit(); conn.close()
        self.success(f"deleted concept from T{turn}: {removed.get('term','')}")

    def cmd_messages(self, args: list[str]) -> None:
        query = " ".join(args).lower() if args else ""
        conn = self._conn_ro()
        rows = conn.execute(
            "SELECT store_id, role, content, tool_name FROM raw_messages ORDER BY store_id"
        ).fetchall()
        conn.close()
        count = 0
        for store_id, role, content, tool_name in rows:
            hay = f"{role} {content or ''} {tool_name or ''}".lower()
            if query and query not in hay:
                continue
            count += 1
            tool = f" [{tool_name}]" if tool_name else ""
            self.print(f"#{store_id:<5} {role:<10} {_truncate(content, 90)}{tool}")
        self.info(f"{count} message(s)")

    def cmd_message(self, args: list[str]) -> None:
        if not args:
            raise RuntimeError("Usage: message <store_id>")
        store_id = int(args[0])
        conn = self._conn_ro()
        row = conn.execute(
            "SELECT role, content, tool_name, tool_call_id, timestamp FROM raw_messages WHERE store_id=?",
            (store_id,),
        ).fetchone()
        conn.close()
        if not row:
            raise RuntimeError(f"Message not found: {store_id}")
        role, content, tool_name, tool_call_id, ts = row
        self.print(f"Message #{store_id}\n  role: {role}\n  tool: {tool_name or '—'}\n  call: {tool_call_id or '—'}\n  timestamp: {ts}\n\n{content or ''}")

    def cmd_state(self, args: list[str]) -> None:
        conn = self._conn_ro()
        rows = conn.execute("SELECT scope, key, value FROM session_state ORDER BY scope, key").fetchall()
        conn.close()
        for scope, key, value in rows:
            self.print(f"[{scope:<8}] {key:<30} {_truncate(value, 100)}")
        self.info(f"{len(rows)} state row(s)")

    def cmd_state_set(self, args: list[str]) -> None:
        if len(args) < 3:
            raise RuntimeError("Usage: state-set <scope> <key> <value>")
        scope, key, value = args[0], args[1], " ".join(args[2:])
        conn = self._conn_rw()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR REPLACE INTO session_state(key, value, scope, updated_at) "
            "VALUES (?, ?, ?, unixepoch('subsec'))",
            (key, value, scope),
        )
        conn.commit(); conn.close()
        self.success(f"set state [{scope}] {key}")

    def cmd_state_del(self, args: list[str]) -> None:
        if len(args) < 2:
            raise RuntimeError("Usage: state-del <scope> <key>")
        scope, key = args[0], args[1]
        conn = self._conn_rw()
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("DELETE FROM session_state WHERE scope=? AND key=?", (scope, key))
        conn.commit(); conn.close()
        self.success(f"deleted {cur.rowcount} row(s)")

    # ── internal data -------------------------------------------------

    def _scan_sessions(self) -> list[SessionInfo]:
        sessions_dir = self.hermes_home / "sessions"
        if not sessions_dir.is_dir():
            return []
        results: list[SessionInfo] = []
        for sd in sorted(sessions_dir.iterdir(), reverse=True):
            db = sd / "decohere.db"
            if not sd.is_dir() or not db.exists():
                continue
            try:
                conn = _open_db_readonly(db)
                turns = conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
                msgs = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
                concepts = conn.execute("SELECT COUNT(*) FROM concepts_fts").fetchone()[0]
                states = conn.execute("SELECT COUNT(*) FROM session_state").fetchone()[0]
                conn.close()
            except Exception:
                turns = msgs = concepts = states = 0
            results.append(SessionInfo(
                index=len(results) + 1,
                session_id=sd.name,
                db_path=db,
                turns=turns,
                messages=msgs,
                concepts=concepts,
                states=states,
                size=db.stat().st_size,
            ))
        return results

    def _conn_ro(self) -> sqlite3.Connection:
        assert self.current is not None
        return _open_db_readonly(self.current.db_path)

    def _conn_rw(self) -> sqlite3.Connection:
        assert self.current is not None
        return _open_db_readwrite(self.current.db_path)

    def _load_concepts(self) -> list[dict[str, Any]]:
        conn = self._conn_ro()
        rows = conn.execute("SELECT turn_n, entry_json FROM ledger_entries ORDER BY turn_n").fetchall()
        conn.close()
        concepts = []
        for turn_n, entry_json in rows:
            entry = _parse_entry(entry_json)
            for idx, concept in enumerate(entry.get("concepts_and_definitions", []) or []):
                if isinstance(concept, dict):
                    concepts.append({
                        "turn": turn_n,
                        "idx": idx,
                        "term": concept.get("term", ""),
                        "definition": concept.get("definition", ""),
                    })
        return concepts

    def _get_concept(self, turn: int, idx: int) -> dict:
        conn = self._conn_ro()
        entry = _load_entry_json(conn, turn)
        conn.close()
        if not entry:
            raise RuntimeError(f"Turn not found: {turn}")
        concepts = entry.get("concepts_and_definitions", []) or []
        if idx < 0 or idx >= len(concepts):
            raise RuntimeError(f"Concept index out of range: {idx}")
        return concepts[idx]

    def _parse_turn_idx(self, args: list[str], usage: str) -> tuple[int, int]:
        if len(args) < 2:
            raise RuntimeError(usage)
        return int(args[0]), int(args[1])

    def _print_turn(self, turn_n: int, entry: dict, validated: bool) -> None:
        self.print(f"Turn {turn_n} {'✓' if validated else '✗'}", "bold")
        meta = entry.get("relevant_metadata") or {}
        if meta.get("task"):
            self.print(f"  task: {meta.get('task')}")
        if entry.get("user_intent"):
            self.print(f"  intent: {entry.get('user_intent')}")
        concepts = entry.get("concepts_and_definitions", []) or []
        if concepts:
            self.print("  concepts:")
            for i, c in enumerate(concepts):
                if isinstance(c, dict):
                    self.print(f"    #{i} {c.get('term','')}: {_truncate(c.get('definition',''), 120)}")
        narrative = entry.get("narrative") or {}
        if narrative.get("summary"):
            self.print(f"  narrative: {narrative.get('summary')}")
        if not validated and not narrative.get("summary"):
            self.print("  [Pending background extraction — placeholder entry]", "dim")

    # ── Public data accessors (for vim TUI) ───────────────────────────

    def get_sessions(self) -> list[SessionInfo]:
        """Return cached or freshly scanned sessions list."""
        self._sessions_cache = self._scan_sessions()
        return self._sessions_cache

    def get_turns_data(self, query: str = "") -> list[dict[str, Any]]:
        """Return turn data as list of dicts for vim TUI rendering."""
        if not self.current:
            return []
        conn = self._conn_ro()
        rows = conn.execute(
            "SELECT turn_n, entry_json, posted_at, validated FROM ledger_entries ORDER BY turn_n DESC"
        ).fetchall()
        conn.close()
        result = []
        for turn_n, entry_json, posted_at, validated in rows:
            entry = _parse_entry(entry_json)
            task = (entry.get("relevant_metadata") or {}).get("task", "")
            tools = entry.get("tools", []) or []
            tool_names = [t.get("name", t) if isinstance(t, dict) else str(t) for t in tools]
            if query and query not in json.dumps(entry, ensure_ascii=False).lower():
                continue
            result.append({
                "turn_n": turn_n,
                "task": task,
                "tools": tool_names,
                "concepts_count": len(entry.get("concepts_and_definitions", []) or []),
                "validated": bool(validated),
                "entry": entry,
                "entry_json": entry_json,
            })
        return result

    def get_concepts_data(self, query: str = "") -> list[dict[str, Any]]:
        """Return concept data for vim TUI rendering."""
        concepts = self._load_concepts()
        if query:
            concepts = [c for c in concepts if query in c["term"].lower() or query in c["definition"].lower()]
        return concepts

    def get_messages_data(self, query: str = "") -> list[dict[str, Any]]:
        """Return message data for vim TUI rendering."""
        if not self.current:
            return []
        conn = self._conn_ro()
        rows = conn.execute(
            "SELECT store_id, role, content, tool_name FROM raw_messages ORDER BY store_id"
        ).fetchall()
        conn.close()
        result = []
        for store_id, role, content, tool_name in rows:
            hay = f"{role} {content or ''} {tool_name or ''}".lower()
            if query and query not in hay:
                continue
            result.append({
                "store_id": store_id,
                "role": role,
                "content": content or "",
                "tool_name": tool_name or "",
            })
        return result

    def get_state_data(self) -> list[dict[str, Any]]:
        """Return state data for vim TUI rendering."""
        if not self.current:
            return []
        conn = self._conn_ro()
        rows = conn.execute("SELECT scope, key, value FROM session_state ORDER BY scope, key").fetchall()
        conn.close()
        return [{"scope": scope, "key": key, "value": value} for scope, key, value in rows]


# ═══════════════════════════════════════════════════════════════════════
# DecohereVimTUI — prompt_toolkit Application with vim navigation
# ═══════════════════════════════════════════════════════════════════════

# Command list for tab completion
_ALL_COMMANDS = [
    "sessions", "open", "close", "current",
    "turns", "show", "edit",
    "concepts", "concept", "concept-set", "concept-add", "concept-del",
    "messages", "message",
    "state", "state-set", "state-del",
    "help", "quit",
]


def _prompt_toolkit_available() -> bool:
    try:
        import prompt_toolkit  # noqa: F401
        return True
    except ImportError:
        return False


class DecohereVimTUI:
    """Vim-keybinding TUI wrapping DecohereCLI.

    Modes:
      NORMAL   — browse items with j/k, Enter selects, / search, : command
      SEARCH   — typing creates filter, Enter applies, Esc cancels
      COMMAND  — typing with tab completion, Enter executes, Esc cancels

    Views (set of items displayed):
      sessions, turns, concepts, messages, state, turn_detail
    """

    MODE_NORMAL = "NORMAL"
    MODE_SEARCH = "SEARCH"
    MODE_COMMAND = "COMMAND"

    VIEW_SESSIONS = "sessions"
    VIEW_TURNS = "turns"
    VIEW_CONCEPTS = "concepts"
    VIEW_MESSAGES = "messages"
    VIEW_STATE = "state"
    VIEW_TURN_DETAIL = "turn_detail"
    VIEW_MESSAGE_DETAIL = "message_detail"

    def __init__(self, hermes_home: Path, initial_db: Path | None = None):
        self.cli = DecohereCLI(hermes_home, initial_db)
        self.hermes_home = hermes_home
        self.mode: str = self.MODE_NORMAL
        self.buffer: str = ""
        self.view: str = self.VIEW_SESSIONS
        self.items: list[dict[str, Any]] = []
        self.selected: int = 0
        self.status_msg: str = ""
        self._view_stack: list[str] = []  # for Esc back-navigation
        self.running: bool = True

    # ── run ───────────────────────────────────────────────────────────

    def run(self) -> int:
        if not _prompt_toolkit_available():
            # Fallback to line-oriented CLI
            return self.cli.run()

        from prompt_toolkit import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, FormattedTextControl
        from prompt_toolkit.layout.controls import FormattedTextControl as FTC
        from prompt_toolkit.styles import Style

        self._refresh_view()

        kb = self._create_keybindings()

        layout = Layout(
            HSplit([
                Window(content=FTC(self._render_list), wrap_lines=False),
                Window(height=1, char="─", style="class:dim"),
                Window(height=1, content=FTC(self._render_status), style="class:status"),
            ])
        )

        style = Style.from_dict({
            "selected": "reverse",
            "dim": "italic #666666",
            "status": "bg:#16213e #999999",
            "heading": "bold #e0a040",
            "green": "#44bb44",
            "red": "#cc4444",
            "yellow": "#cccc44",
        })

        app = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=True,
            mouse_support=False,
        )

        try:
            app.run()
        except Exception:
            pass
        finally:
            # Restore terminal state
            print("\033[?25h", end="")  # show cursor
        return 0

    # ── keybindings ───────────────────────────────────────────────────

    def _create_keybindings(self):
        from prompt_toolkit.key_binding import KeyBindings
        kb = KeyBindings()

        # ── Movement (NORMAL) ──
        @kb.add("j")
        @kb.add("down")
        def move_down(event):
            if self.mode == self.MODE_NORMAL:
                self._move_cursor(1)
            elif self.mode in (self.MODE_SEARCH, self.MODE_COMMAND):
                pass  # let text input handle

        @kb.add("k")
        @kb.add("up")
        def move_up(event):
            if self.mode == self.MODE_NORMAL:
                self._move_cursor(-1)

        @kb.add("g", "g")
        def move_top(event):
            if self.mode == self.MODE_NORMAL:
                self.selected = 0

        @kb.add("G")
        def move_bottom(event):
            if self.mode == self.MODE_NORMAL and self.items:
                self.selected = len(self.items) - 1

        @kb.add("c-d")
        def page_down(event):
            if self.mode == self.MODE_NORMAL:
                self._move_cursor(10)

        @kb.add("c-u")
        def page_up(event):
            if self.mode == self.MODE_NORMAL:
                self._move_cursor(-10)

        # ── Mode entry (NORMAL) ──
        @kb.add("/")
        def enter_search(event):
            if self.mode == self.MODE_NORMAL:
                self.mode = self.MODE_SEARCH
                self.buffer = ""

        @kb.add(":")
        def enter_command(event):
            if self.mode == self.MODE_NORMAL:
                self.mode = self.MODE_COMMAND
                self.buffer = ""

        # ── Select (NORMAL) ──
        @kb.add("enter")
        def confirm(event):
            if self.mode == self.MODE_NORMAL:
                self._select_current()
            elif self.mode == self.MODE_SEARCH:
                self._apply_search()
            elif self.mode == self.MODE_COMMAND:
                self._execute_command()

        # ── Escape ──
        @kb.add("escape")
        def escape(event):
            if self.mode == self.MODE_SEARCH:
                self.mode = self.MODE_NORMAL
                self.buffer = ""
                self._refresh_view()
            elif self.mode == self.MODE_COMMAND:
                self.mode = self.MODE_NORMAL
                self.buffer = ""
            elif self.mode == self.MODE_NORMAL:
                self._go_back()

        # ── Quit ──
        @kb.add("q")
        def quit_app(event):
            if self.mode == self.MODE_NORMAL and self.view == self.VIEW_SESSIONS:
                event.app.exit()

        @kb.add("c-c")
        def ctrl_c(event):
            event.app.exit()

        # ── Text input (SEARCH + COMMAND) ──
        @kb.add("<any>")
        def any_key(event):
            if self.mode in (self.MODE_SEARCH, self.MODE_COMMAND):
                if event.data and len(event.data) == 1 and event.data.isprintable():
                    self.buffer += event.data

        @kb.add("c-h")  # backspace
        @kb.add("backspace")
        def backspace(event):
            if self.mode in (self.MODE_SEARCH, self.MODE_COMMAND) and self.buffer:
                self.buffer = self.buffer[:-1]
                if self.mode == self.MODE_SEARCH:
                    self._apply_search_live()

        @kb.add("space")
        def space_key(event):
            if self.mode in (self.MODE_SEARCH, self.MODE_COMMAND):
                self.buffer += " "
                if self.mode == self.MODE_SEARCH:
                    self._apply_search_live()

        # ── Tab completion (COMMAND) ──
        @kb.add("tab")
        def tab_complete(event):
            if self.mode == self.MODE_COMMAND:
                self.buffer = self._complete_command(self.buffer)

        return kb

    # ── cursor ────────────────────────────────────────────────────────

    def _move_cursor(self, delta: int) -> None:
        if not self.items:
            return
        self.selected = max(0, min(len(self.items) - 1, self.selected + delta))

    # ── select ────────────────────────────────────────────────────────

    def _select_current(self) -> None:
        if not self.items or self.selected >= len(self.items):
            return
        item = self.items[self.selected]

        if self.view == self.VIEW_SESSIONS:
            self._view_stack.append(self.VIEW_SESSIONS)
            try:
                self.cli.execute(f"open {item['index']}")
                self.view = self.VIEW_TURNS
                self._refresh_view()
                self.status_msg = f"Opened {item['session_id']}"
            except RuntimeError as e:
                self.status_msg = str(e)

        elif self.view == self.VIEW_TURNS:
            self._view_stack.append(self.VIEW_TURNS)
            self.view = self.VIEW_TURN_DETAIL
            self._refresh_view()
            self.status_msg = f"Turn {item['turn_n']}"

        elif self.view == self.VIEW_MESSAGES:
            self._view_stack.append(self.VIEW_MESSAGES)
            self.view = self.VIEW_MESSAGE_DETAIL
            self._refresh_view()
            self.status_msg = f"Message #{item['store_id']}"

        elif self.view == self.VIEW_CONCEPTS:
            self.status_msg = f"Concept: T{item['turn']} #{item['idx']} {item['term']}"

        elif self.view == self.VIEW_STATE:
            self.status_msg = f"State: [{item['scope']}] {item['key']} = {item['value']}"

        self.selected = 0

    # ── search ────────────────────────────────────────────────────────

    def _apply_search(self) -> None:
        self.mode = self.MODE_NORMAL
        self._refresh_view(query=self.buffer)
        self.status_msg = f"Search: {self.buffer}" if self.buffer else ""

    def _apply_search_live(self) -> None:
        self._refresh_view(query=self.buffer)

    # ── command ────────────────────────────────────────────────────────

    def _execute_command(self) -> None:
        cmd = self.buffer.strip()
        self.mode = self.MODE_NORMAL
        self.buffer = ""

        if not cmd:
            return

        if cmd in ("q", "quit"):
            if self.view == self.VIEW_SESSIONS:
                self.running = False
                # Exit application — signal via raising
                import sys as _sys
                try:
                    from prompt_toolkit.application import get_app
                    get_app().exit()
                except Exception:
                    _sys.exit(0)
                return
            else:
                self._go_back()
                return

        if cmd == "close":
            self.view = self.VIEW_SESSIONS
            self._view_stack.clear()
            self._refresh_view()
            self.status_msg = "Session closed"
            return

        # View-switching commands
        view_map = {
            "sessions": self.VIEW_SESSIONS,
            "turns": self.VIEW_TURNS,
            "concepts": self.VIEW_CONCEPTS,
            "messages": self.VIEW_MESSAGES,
            "state": self.VIEW_STATE,
        }
        if cmd in view_map:
            target_view = view_map[cmd]
            if target_view in (self.VIEW_TURNS, self.VIEW_CONCEPTS, self.VIEW_MESSAGES, self.VIEW_STATE):
                if not self.cli.current:
                    self.status_msg = "No session open. Use :open <id> first."
                    return
            if self.view != target_view:
                self._view_stack.append(self.view)
            self.view = target_view
            self._refresh_view()
            self.selected = 0
            return

        # Delegate to CLI
        try:
            self.cli.execute(cmd)
            self.status_msg = f":{cmd} ✓"
            if self.view in (self.VIEW_SESSIONS, self.VIEW_TURNS,
                             self.VIEW_CONCEPTS, self.VIEW_MESSAGES, self.VIEW_STATE):
                self._refresh_view()
        except RuntimeError as e:
            self.status_msg = str(e)
        except SystemExit:
            pass

    # ── navigation ────────────────────────────────────────────────────

    def _go_back(self) -> None:
        if self._view_stack:
            prev = self._view_stack.pop()
            self.view = prev
            self._refresh_view()
            self.selected = 0
            self.status_msg = ""
        elif self.view == self.VIEW_SESSIONS:
            # At top level, Esc does nothing (q to quit)
            pass

    # ── tab completion ─────────────────────────────────────────────────

    def _complete_command(self, text: str) -> str:
        """Simple completion: complete first word against command list."""
        if not text:
            return text

        # Complete first word
        prefix = text.lower()
        matches = [c for c in _ALL_COMMANDS if c.startswith(prefix)]

        if len(matches) == 1:
            return matches[0] + " "
        elif len(matches) > 1:
            # Find common prefix
            common = matches[0]
            for m in matches[1:]:
                while not m.startswith(common):
                    common = common[:-1]
            if common != prefix:
                return common
        return text

    # ── refresh ───────────────────────────────────────────────────────

    def _refresh_view(self, query: str = "") -> None:
        """Reload items for current view."""
        if self.view == self.VIEW_SESSIONS:
            sessions = self.cli.get_sessions()
            self.items = [
                {"index": s.index, "session_id": s.session_id,
                 "turns": s.turns, "messages": s.messages,
                 "concepts": s.concepts, "states": s.states, "size": s.size}
                for s in sessions
            ]
            if query:
                q = query.lower()
                self.items = [it for it in self.items
                              if q in it["session_id"].lower()]

        elif self.view == self.VIEW_TURNS:
            self.items = self.cli.get_turns_data(query)

        elif self.view == self.VIEW_CONCEPTS:
            self.items = self.cli.get_concepts_data(query)

        elif self.view == self.VIEW_MESSAGES:
            self.items = self.cli.get_messages_data(query)

        elif self.view == self.VIEW_STATE:
            self.items = self.cli.get_state_data()

        elif self.view == self.VIEW_TURN_DETAIL:
            # Turn detail is a single-item "view" — rendered inline
            pass

        elif self.view == self.VIEW_MESSAGE_DETAIL:
            pass

        self.selected = min(self.selected, max(0, len(self.items) - 1))

    # ── render ────────────────────────────────────────────────────────

    def _render_list(self) -> list[tuple[str, str]]:
        """Return formatted text for prompt_toolkit FormattedTextControl."""
        lines: list[tuple[str, str]] = []

        # View heading
        view_heading = {
            self.VIEW_SESSIONS: "Sessions",
            self.VIEW_TURNS: f"Turns — {self.cli.current.session_id if self.cli.current else ''}",
            self.VIEW_CONCEPTS: "Concepts",
            self.VIEW_MESSAGES: "Raw Messages",
            self.VIEW_STATE: "Session State",
        }

        heading = view_heading.get(self.view, "")
        if heading:
            lines.append(("class:heading", f"  {heading}\n"))
            lines.append(("class:dim", "  " + "─" * 60 + "\n"))

        # Items
        view_height = 20
        start = max(0, self.selected - view_height // 2)
        end = min(len(self.items), start + view_height)

        if self.view == self.VIEW_TURN_DETAIL:
            lines.extend(self._render_turn_detail_lines())
        elif self.view == self.VIEW_MESSAGE_DETAIL:
            lines.extend(self._render_message_detail_lines())
        else:
            for i in range(start, end):
                item = self.items[i]
                prefix = "▸" if i == self.selected else " "
                text = self._format_item(item)
                style = "class:selected" if i == self.selected else ""
                lines.append((style, f" {prefix} {text}\n"))

        if not self.items and self.view not in (self.VIEW_TURN_DETAIL, self.VIEW_MESSAGE_DETAIL):
            lines.append(("class:dim", "  (empty)\n"))

        return lines

    def _format_item(self, item: dict) -> str:
        if self.view == self.VIEW_SESSIONS:
            size_str = f"{item['size'] / 1024:.1f}K" if item.get("size") else "—"
            return (f"{item['session_id']:<38} "
                    f"turns:{item.get('turns', 0):>3}  msgs:{item.get('messages', 0):>3}  "
                    f"concepts:{item.get('concepts', 0):>3}  state:{item.get('states', 0)}  "
                    f"{size_str}")

        elif self.view == self.VIEW_TURNS:
            mark = "✓" if item.get("validated") else " "
            tools = ", ".join(item.get("tools", [])[:2])
            return (f"[{mark}] T{item['turn_n']:<4} "
                    f"c={item.get('concepts_count', 0):<3} "
                    f"{_truncate(item.get('task', ''), 70):<72} "
                    f"{tools}")

        elif self.view == self.VIEW_CONCEPTS:
            return (f"T{item['turn']:<4} #{item['idx']:<3} "
                    f"{_yellow(item.get('term', ''))}: "
                    f"{_truncate(item.get('definition', ''), 100)}")

        elif self.view == self.VIEW_MESSAGES:
            tool = f" [{item.get('tool_name')}]" if item.get("tool_name") else ""
            return (f"#{item.get('store_id', ''):<5} "
                    f"{item.get('role', ''):<10} "
                    f"{_truncate(item.get('content', ''), 90)}{tool}")

        elif self.view == self.VIEW_STATE:
            return (f"[{item.get('scope', ''):<8}] "
                    f"{item.get('key', ''):<32} "
                    f"{_truncate(item.get('value', ''), 100)}")

        return ""

    def _render_turn_detail_lines(self) -> list[tuple[str, str]]:
        """Render full turn detail as formatted text lines."""
        lines: list[tuple[str, str]] = []
        if not self.items or self.selected >= len(self.items):
            lines.append(("class:dim", "  (no turn selected)\n"))
            return lines

        item = self.items[self.selected]
        entry = item.get("entry", {})

        turn_n = item.get("turn_n", "?")
        validated = item.get("validated", False)
        lines.append(("class:heading", f"  Turn {turn_n} {'✓' if validated else '✗'}\n"))
        lines.append(("class:dim", "  " + "─" * 60 + "\n"))

        meta = entry.get("relevant_metadata") or {}
        if meta.get("task"):
            lines.append(("", f"  task: {meta.get('task')}\n"))
        if entry.get("user_intent"):
            lines.append(("", f"  intent: {entry.get('user_intent')}\n"))

        concepts = entry.get("concepts_and_definitions", []) or []
        if concepts:
            lines.append(("class:heading", "\n  Concepts:\n"))
            for i, c in enumerate(concepts):
                if isinstance(c, dict):
                    lines.append(("",
                        f"    #{i} {c.get('term', '')}: {_truncate(c.get('definition', ''), 110)}\n"))

        narrative = entry.get("narrative") or {}
        if narrative.get("summary"):
            lines.append(("class:heading", "\n  Narrative:\n"))
            lines.append(("", f"  {narrative.get('summary')}\n"))

        lines.append(("class:dim", "\n  Esc to go back\n"))
        return lines

    def _render_message_detail_lines(self) -> list[tuple[str, str]]:
        """Render full message detail as formatted text lines."""
        lines: list[tuple[str, str]] = []
        if not self.items or self.selected >= len(self.items):
            lines.append(("class:dim", "  (no message selected)\n"))
            return lines

        item = self.items[self.selected]
        lines.append(("class:heading", f"  Message #{item.get('store_id', '?')}\n"))
        lines.append(("class:dim", "  " + "─" * 60 + "\n"))
        lines.append(("", f"  role: {item.get('role', '')}\n"))
        if item.get("tool_name"):
            lines.append(("", f"  tool: {item.get('tool_name')}\n"))
        lines.append(("", "\n"))
        content = item.get("content", "") or ""
        for line in content.split("\n"):
            lines.append(("", f"  {_truncate(line, 120)}\n"))
        lines.append(("class:dim", "\n  Esc to go back\n"))
        return lines

    def _render_status(self) -> list[tuple[str, str]]:
        """Return formatted text for the status bar."""
        mode_map = {
            self.MODE_NORMAL: "NORMAL",
            self.MODE_SEARCH: "SEARCH",
            self.MODE_COMMAND: "COMMAND",
        }
        mode_str = mode_map.get(self.mode, "?")
        session_str = f" [{self.cli.current.session_id[:20]}]" if self.cli.current else ""
        items_str = f" {len(self.items)} items" if self.view not in (self.VIEW_TURN_DETAIL, self.VIEW_MESSAGE_DETAIL) else ""

        if self.mode == self.MODE_NORMAL:
            buf = f"{mode_str}{session_str}{items_str}  j/k nav  / search  : cmd  q quit  Enter select"
        elif self.mode == self.MODE_SEARCH:
            buf = f"{mode_str}  /{self.buffer}"
        else:
            buf = f"{mode_str}  :{self.buffer}"

        if self.status_msg:
            buf = f"  {self.status_msg}  │  {buf}"

        return [("class:status", buf)]


def run_tui(hermes_home: Path, initial_db: Path | None = None) -> int:
    """Entry point for `hermes decohere tui`.

    Uses vim TUI when prompt_toolkit is available, falls back to CLI.
    """
    if _prompt_toolkit_available():
        return DecohereVimTUI(hermes_home, initial_db).run()
    return DecohereCLI(hermes_home, initial_db).run()
