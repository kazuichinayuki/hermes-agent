"""hermes decohere edit — modify a field in a ledger entry."""

from __future__ import annotations

import json
import sys

from ._shared import (
    audit_log,
    get_nested_field,
    open_db,
    parse_json_field,
    resolve_hermes_home,
    resolve_session,
    set_nested_field,
)


def register_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "edit",
        help="Modify a field in a ledger entry",
        description="Edit a specific field of a ledger entry with audit trail.",
    )
    parser.add_argument("--profile", help="Use a specific profile")
    parser.add_argument("--home", help="Directly specify hermes home path")
    parser.add_argument("--session", help="Session ID (default: most recently modified)")
    parser.add_argument("--turn", type=int, required=True, help="Turn number to edit")
    parser.add_argument("--field", required=True,
                       help="Field path (e.g. user_intent, narrative.summary, "
                            "concepts_and_definitions[0].term)")
    parser.add_argument("--value", required=True, help="New value (JSON format)")
    parser.add_argument("--confirm", action="store_true",
                       help="Skip interactive confirmation (for scripts)")


def run(args) -> int:
    try:
        home = resolve_hermes_home(profile=args.profile, home=args.home)
        sid, db_path = resolve_session(home, args.session)

        # Parse new value
        try:
            new_value = json.loads(args.value)
        except json.JSONDecodeError:
            # Treat as raw string
            new_value = args.value

        conn = open_db(db_path, readonly=False)

        row = conn.execute(
            "SELECT entry_json FROM ledger_entries WHERE turn_n = ?",
            (args.turn,),
        ).fetchone()

        if not row:
            conn.close()
            print(f"Error: Turn {args.turn} not found in session {sid}")
            return 1

        # row = (entry_json,) — no row_factory
        entry = parse_json_field(row[0], {})
        if not entry:
            conn.close()
            print(f"Error: Turn {args.turn}: corrupted JSON")
            return 1

        # Get old value
        try:
            old_value = get_nested_field(entry, args.field)
        except (KeyError, IndexError, TypeError) as e:
            conn.close()
            print(f"Error: field '{args.field}' not found in Turn {args.turn}")
            return 1

        # Show diff and confirm
        if not args.confirm:
            old_str = _truncate(json.dumps(old_value, ensure_ascii=False, default=str))
            new_str = _truncate(json.dumps(new_value, ensure_ascii=False, default=str))
            print(f"⚠  About to edit Turn {args.turn}, field '{args.field}'")
            print(f"   Old: {old_str}")
            print(f"   New: {new_str}")
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

        # Apply edit
        new_entry = set_nested_field(entry, args.field, new_value)
        new_entry["validated"] = False
        new_entry["n"] = args.turn

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "UPDATE ledger_entries SET entry_json = ?, validated = 0 WHERE turn_n = ?",
                (json.dumps(new_entry, ensure_ascii=False), args.turn),
            )

            # Rebuild FTS5 for this turn if concepts changed
            if args.field.startswith("concepts_and_definitions"):
                conn.execute(
                    "INSERT INTO concepts_fts(concepts_fts, rowid, term, definition) "
                    "VALUES('delete', ?, '', '')",
                    (args.turn,),
                )
                for c in new_entry.get("concepts_and_definitions", []) or []:
                    if isinstance(c, dict):
                        conn.execute(
                            "INSERT INTO concepts_fts (rowid, term, definition) VALUES (?, ?, ?)",
                            (args.turn, c.get("term", ""), c.get("definition", "")),
                        )

            conn.commit()

            audit_log(home, {
                "session": sid,
                "turn": args.turn,
                "field": args.field,
                "old": old_value,
                "new": new_value,
                "action": "edit",
            })

            print(f"✓ Turn {args.turn} updated (validated=0, pending review).")
            print(f"  Audit log: {home}/decohere_audit.log")
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _truncate(s: str, max_len: int = 100) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len - 1] + "…"
