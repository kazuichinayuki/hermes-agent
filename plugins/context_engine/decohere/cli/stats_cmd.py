"""hermes decohere stats — show statistical summary of a session."""

from __future__ import annotations

import json
import sys

from ._shared import (
    format_size,
    format_timestamp,
    get_db_stats,
    open_db,
    resolve_hermes_home,
    resolve_session,
)


def register_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "stats",
        help="Show statistical summary of a session",
        description="Display turn counts, tool usage, concept frequency, and storage info.",
    )
    parser.add_argument("--profile", help="Use a specific profile")
    parser.add_argument("--home", help="Directly specify hermes home path")
    parser.add_argument("--session", help="Session ID (default: most recently modified)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")


def run(args) -> int:
    try:
        home = resolve_hermes_home(profile=args.profile, home=args.home)
        sid, db_path = resolve_session(home, args.session)
        conn = open_db(db_path, readonly=True)

        stats = get_db_stats(conn)
        db_size = db_path.stat().st_size
        conn.close()

        if stats["entry_count"] == 0:
            print(f"Session {sid}: no ledger entries")
            return 0

        if args.json:
            output = {
                "session_id": sid,
                **stats,
                "db_size": db_size,
                "db_size_formatted": format_size(db_size),
            }
            print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
            return 0

        _print_stats(sid, stats, db_size)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _print_stats(session_id: str, stats: dict, db_size: int) -> None:
    """Print formatted stats block."""
    w = 24  # label width

    print(f"Session: {session_id}")
    print("─" * 56)
    print(f"  {'Entries':<{w}} {stats['entry_count']}")
    print(f"  {'Raw messages':<{w}} {stats['raw_count']}")
    print(f"  {'Avg msgs/turn':<{w}} {stats['avg_messages_per_turn']:.1f}")

    # Validated / pending with status dots
    validated = stats["validated"]
    pending = stats["pending"]
    status = "●" if pending == 0 else "◐"
    print(f"  {'Validated':<{w}} {validated} {status}")
    if pending:
        print(f"  {'Pending':<{w}} {pending}  ◌")

    if stats["orphans"]:
        print(f"  {'Orphans':<{w}} {stats['orphans']}  ⚠")

    # Tools
    if stats["top_tools"]:
        print(f"\n  {'Top tools':—^40}")
        max_tool = max(c for _, c in stats["top_tools"]) if stats["top_tools"] else 0
        for tool, count in stats["top_tools"]:
            bar = _bar(count, max_tool, 16)
            print(f"    {tool:<18} {bar} {count}")

    # Concepts
    if stats["top_concepts"]:
        print(f"\n  {'Top concepts':—^40}")
        max_conc = max(c for _, c in stats["top_concepts"]) if stats["top_concepts"] else 0
        for term, count in stats["top_concepts"]:
            safe_term = term or "(unknown)"
            term_display = safe_term if len(safe_term) <= 22 else safe_term[:21] + "…"
            bar = _bar(count, max_conc, 16)
            print(f"    {term_display:<22} {bar} {count}")

    # Timing & Storage
    print(f"\n  {'Timeline':—^40}")
    print(f"    First turn:  {format_timestamp(stats['first_turn'])}")
    print(f"    Last turn:   {format_timestamp(stats['last_turn'])}")
    print(f"    DB size:     {format_size(db_size)}")


def _bar(value: int, maximum: int, width: int) -> str:
    """Draw a proportional bar chart."""
    if maximum == 0:
        return ""
    filled = int(value / maximum * width)
    return "█" * filled + "░" * (width - filled)
