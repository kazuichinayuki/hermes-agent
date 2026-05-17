"""hermes decohere orphans — list orphan raw_messages with no matching ledger entry."""

from __future__ import annotations

import sys
import json

from ._shared import (
    open_db,
    parse_json_field,
    resolve_hermes_home,
    resolve_session,
)


def register_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "orphans",
        help="List orphan raw_messages with no matching ledger entry",
        description="Show raw_messages that fall outside all ledger entry message_ranges.",
    )
    parser.add_argument("--profile", help="Use a specific profile")
    parser.add_argument("--home", help="Directly specify hermes home path")
    parser.add_argument("--session", help="Session ID (default: most recently modified)")
    parser.add_argument("--limit", type=int, default=20, help="Max messages to show (default: 20)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")


def run(args) -> int:
    try:
        home = resolve_hermes_home(profile=args.profile, home=args.home)
        sid, db_path = resolve_session(home, args.session)
        conn = open_db(db_path, readonly=True)

        # Collect all covered store_ids from ledger entries
        rows = conn.execute("SELECT entry_json FROM ledger_entries").fetchall()
        covered: set[int] = set()
        for (entry_json,) in rows:
            entry = parse_json_field(entry_json)
            if not entry:
                continue
            msg_range = entry.get("message_range", [])
            if msg_range and len(msg_range) >= 2:
                covered.update(range(int(msg_range[0]), int(msg_range[-1]) + 1))

        # Find orphans
        raw_rows = conn.execute(
            "SELECT store_id, role, content FROM raw_messages "
            "ORDER BY store_id"
        ).fetchall()

        orphans = []
        total_raw = 0
        for store_id, role, content in raw_rows:
            total_raw += 1
            if store_id not in covered:
                orphans.append({
                    "store_id": store_id,
                    "role": role,
                    "content": (content or "")[:120],
                })

        conn.close()

        if args.json:
            output = {
                "session_id": sid,
                "total_raw": total_raw,
                "orphan_count": len(orphans),
                "covered_range_count": len(covered),
                "orphans": orphans[:args.limit],
            }
            print(json.dumps(output, indent=2, ensure_ascii=False))
            return 0

        if not orphans:
            print(f"No orphan raw_messages in session {sid}.")
            return 0

        print(f"Session: {sid}")
        print(f"  Raw messages: {total_raw} | Covered by entries: {len(covered)} | Orphans: {len(orphans)}")
        print()
        print(f"  {'ID':>5}  {'ROLE':<10}  CONTENT")
        for o in orphans[:args.limit]:
            content = o["content"][:80].replace("\n", " ")
            print(f"  {o['store_id']:>5}  {o['role']:<10}  {content}")

        if len(orphans) > args.limit:
            print(f"  ... and {len(orphans) - args.limit} more. Use --limit to show more.")

        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
