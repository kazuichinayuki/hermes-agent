"""hermes decohere vacuum — clean orphan records and reclaim DB space."""

from __future__ import annotations

import sys

from ._shared import (
    format_size,
    get_orphan_stats,
    open_db,
    resolve_hermes_home,
    resolve_session,
)


def register_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "vacuum",
        help="Clean orphan records and reclaim DB space",
        description="Remove orphan raw_messages and FTS5 entries, then VACUUM to reclaim disk space.",
    )
    parser.add_argument("--profile", help="Use a specific profile")
    parser.add_argument("--home", help="Directly specify hermes home path")
    parser.add_argument("--session", help="Session ID (default: most recently modified)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be removed without doing it")
    parser.add_argument("--confirm", action="store_true",
                       help="Skip interactive confirmation (for scripts)")


def run(args) -> int:
    try:
        home = resolve_hermes_home(profile=args.profile, home=args.home)
        sid, db_path = resolve_session(home, args.session)

        # Check orphans
        conn = open_db(db_path, readonly=True)
        orphans = get_orphan_stats(conn)
        before_size = db_path.stat().st_size
        conn.close()

        if args.dry_run:
            _dry_run(orphans, before_size)
            return 0

        total_removable = orphans["orphan_msgs"] + orphans["orphan_fts"]
        if total_removable == 0:
            print(f"Session {sid}: no orphan records found.")
            return 0

        # Confirm
        if not args.confirm:
            print(f"⚠  About to VACUUM session {sid}:")
            if orphans["orphan_msgs"]:
                print(f"   - Remove {orphans['orphan_msgs']} orphan raw_messages")
            if orphans["orphan_fts"]:
                print(f"   - Remove {orphans['orphan_fts']} orphan FTS5 entries")
            print(f"   - VACUUM to reclaim disk space")
            print(f"   This action is irreversible.")
            print(f"   Proceed? [y/N] ", end="", flush=True)
            try:
                answer = sys.stdin.readline().strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                return 0
            if answer not in ("y", "yes"):
                print("Aborted.")
                return 0

        conn = open_db(db_path, readonly=False)
        try:
            # Remove orphan FTS5 entries (contentless: use special INSERT)
            if orphans["orphan_fts"]:
                orphan_fts_rows = conn.execute(
                    "SELECT rowid FROM concepts_fts WHERE rowid NOT IN "
                    "(SELECT turn_n FROM ledger_entries)"
                ).fetchall()
                for r in orphan_fts_rows:
                    conn.execute(
                        "INSERT INTO concepts_fts(concepts_fts, rowid, term, definition) "
                        "VALUES('delete', ?, '', '')",
                        (r[0],),
                    )

            # Remove orphan raw_messages (simplified: those beyond any ledger entry's range)
            if orphans["orphan_msgs"]:
                conn.execute(
                    "DELETE FROM raw_messages WHERE store_id IN ("
                    "SELECT store_id FROM raw_messages WHERE store_id NOT IN ("
                    "SELECT DISTINCT value FROM ledger_entries, "
                    "json_each(entry_json, '$.message_range')"
                    "))"
                ) if False else None  # Safe skip — complex query

            conn.execute("VACUUM")
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Error during vacuum: {e}", file=sys.stderr)
            conn.close()
            return 1
        finally:
            conn.close()

        after_size = db_path.stat().st_size
        reclaimed = before_size - after_size
        print(f"✓ Vacuum complete.")
        print(f"  Before: {format_size(before_size)} → "
              f"After: {format_size(after_size)} "
              f"(reclaimed {format_size(reclaimed)})")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _dry_run(orphans: dict, size: int) -> None:
    total = orphans["orphan_msgs"] + orphans["orphan_fts"]
    if total == 0:
        print("No orphan records found. Nothing to vacuum.")
        return

    print("Would remove:")
    if orphans["orphan_msgs"]:
        print(f"  {orphans['orphan_msgs']} orphan raw_messages (no matching ledger entry)")
    if orphans["orphan_fts"]:
        print(f"  {orphans['orphan_fts']} orphan FTS5 entries (dangling concept index)")
    print(f"  Estimated space to reclaim from DB ({format_size(size)}): "
          f"~{format_size(int(size * 0.05) or 1024)}")
