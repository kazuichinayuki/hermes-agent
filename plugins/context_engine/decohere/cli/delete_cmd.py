"""hermes decohere delete — delete an entry or an entire session's data."""

from __future__ import annotations

import sys

from ._shared import (
    audit_log,
    open_db,
    resolve_hermes_home,
    resolve_session,
)


def register_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "delete",
        help="Delete an entry or an entire session's data",
        description="Delete single ledger entries or all data for a session.",
    )
    parser.add_argument("--profile", help="Use a specific profile")
    parser.add_argument("--home", help="Directly specify hermes home path")
    parser.add_argument("--session", help="Session ID (default: most recently modified)")
    parser.add_argument("--turn", type=int, help="Turn number to delete (single mode)")
    parser.add_argument("--all", action="store_true",
                       help="Delete ALL entries and raw messages for the session")
    parser.add_argument("--confirm", action="store_true",
                       help="Skip interactive confirmation (for scripts)")


def run(args) -> int:
    try:
        home = resolve_hermes_home(profile=args.profile, home=args.home)
        sid, db_path = resolve_session(home, args.session)

        if args.all:
            return _delete_all(home, sid, db_path, args)
        elif args.turn is not None:
            return _delete_one(home, sid, db_path, args)
        else:
            print("Error: specify --turn N or --all", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _delete_one(home, sid: str, db_path, args) -> int:
    conn = open_db(db_path, readonly=False)

    row = conn.execute(
        "SELECT entry_json FROM ledger_entries WHERE turn_n = ?",
        (args.turn,),
    ).fetchone()
    # row = (entry_json,) — no row_factory

    if not row:
        conn.close()
        print(f"Error: Turn {args.turn} not found in session {sid}")
        return 1

    if not args.confirm:
        print(f"⚠  About to DELETE Turn {args.turn} from session {sid}")
        print(f"   This action is irreversible.")
        print(f"   Proceed? [y/N] ", end="", flush=True)
        try:
            answer = sys.stdin.readline().strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            conn.close()
            return 0
        if answer not in ("y", "yes"):
            print("Aborted.")
            conn.close()
            return 0

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM ledger_entries WHERE turn_n = ?", (args.turn,))
        # Contentless FTS5: use INSERT ... VALUES('delete', ...) instead of DELETE
        conn.execute(
            "INSERT INTO concepts_fts(concepts_fts, rowid, term, definition) "
            "VALUES('delete', ?, '', '')",
            (args.turn,),
        )
        conn.commit()

        audit_log(home, {
            "session": sid,
            "turn": args.turn,
            "action": "delete",
        })
        print(f"✓ Turn {args.turn} deleted.")
        print(f"  Audit log: {home}/decohere_audit.log")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return 0


def _delete_all(home, sid: str, db_path, args) -> int:
    conn = open_db(db_path, readonly=False)

    entry_count = conn.execute(
        "SELECT COUNT(*) FROM ledger_entries"
    ).fetchone()[0]
    raw_count = conn.execute(
        "SELECT COUNT(*) FROM raw_messages"
    ).fetchone()[0]

    if entry_count == 0 and raw_count == 0:
        conn.close()
        print(f"Session {sid} has no data to delete.")
        return 0

    if not args.confirm:
        print(f"⚠  About to DELETE ALL {entry_count} ledger entries and "
              f"{raw_count} raw messages")
        print(f"   from session {sid}.")
        print(f"   This action is irreversible.")
        print(f"   Type the session ID to confirm: ", end="", flush=True)
        try:
            answer = sys.stdin.readline().strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            conn.close()
            return 0
        if answer != sid:
            print("Session ID does not match. Aborted.")
            conn.close()
            return 0

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM ledger_entries")
        # Contentless FTS5: delete all rows via special INSERT
        rows = conn.execute("SELECT rowid FROM concepts_fts").fetchall()
        for r in rows:
            conn.execute(
                "INSERT INTO concepts_fts(concepts_fts, rowid, term, definition) "
                "VALUES('delete', ?, '', '')",
                (r[0],),
            )
        conn.execute("DELETE FROM raw_messages")
        conn.commit()

        audit_log(home, {
            "session": sid,
            "action": "delete_all",
            "entry_count": entry_count,
            "raw_count": raw_count,
        })
        print(f"✓ Session {sid} data deleted ({entry_count} entries, "
              f"{raw_count} raw messages).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return 0
