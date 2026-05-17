"""hermes decohere sessions — list all sessions with decohere data."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

from ._shared import (
    NoSessionsError,
    format_relative_time,
    format_size,
    list_all_profile_sessions,
    resolve_hermes_home,
)


def register_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "sessions",
        help="List all sessions with decohere data",
        description="List all sessions that have decohere.db files, "
        "optionally across all profiles.",
    )
    parser.add_argument("--profile", help="Use a specific profile (default: active profile)")
    parser.add_argument("--home", help="Directly specify hermes home path")
    parser.add_argument("--all-profiles", action="store_true",
                       help="Scan all known profiles for sessions")
    parser.add_argument("--json", action="store_true", help="Output as JSON instead of table")
    return parser


def run(args) -> int:
    try:
        if args.all_profiles:
            return _run_all_profiles(args)
        else:
            return _run_single_profile(args)
    except NoSessionsError as e:
        if args.json:
            print("[]")
        else:
            print(str(e))
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _run_single_profile(args) -> int:
    home = resolve_hermes_home(profile=args.profile, home=args.home)
    sessions_dir = home / "sessions"

    if not sessions_dir.is_dir():
        if args.json:
            print("[]")
        else:
            profile_name = args.profile or "default"
            print(f"Active profile: {profile_name} ({home})")
            print("(no sessions found)")
        return 0

    sessions: list[dict[str, Any]] = []
    for sd in sorted(sessions_dir.iterdir(), reverse=True):
        if not sd.is_dir():
            continue
        db = sd / "decohere.db"
        if not db.exists():
            continue
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            turns = conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
            raw_msgs = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
            try:
                concepts = conn.execute("SELECT COUNT(*) FROM concepts_fts").fetchone()[0]
            except sqlite3.OperationalError:
                concepts = 0
            postings = conn.execute(
                "SELECT COUNT(*) FROM ledger_entries WHERE validated = 1"
            ).fetchone()[0]
            last = conn.execute(
                "SELECT MAX(posted_at) FROM ledger_entries"
            ).fetchone()[0]
            size = db.stat().st_size
            conn.close()
            sessions.append({
                "session_id": sd.name,
                "turns": turns,
                "raw_msgs": raw_msgs,
                "concepts": concepts,
                "postings": postings,
                "size": size,
                "last_updated": last,
            })
        except Exception:
            pass

    if not sessions:
        if args.json:
            print("[]")
        else:
            profile_name = args.profile or "default"
            print(f"Active profile: {profile_name} ({home})")
            print("(no sessions found)")
        return 0

    sessions.sort(key=lambda x: x.get("last_updated", 0) or 0, reverse=True)

    if args.json:
        import json
        print(json.dumps(sessions, indent=2, default=str, ensure_ascii=False))
        return 0

    profile_name = args.profile or "default"
    print(f"Active profile: {profile_name} ({home})\n")

    # Table header
    sep = "  "
    header = (
        f"  {'SESSION':<34}{sep}"
        f"{'TURNS':>5}{sep}"
        f"{'CONCEPTS':>8}{sep}"
        f"{'POST':>4}{sep}"
        f"{'SIZE':>8}{sep}"
        f"{'ACTIVE'}"
    )
    print(header)
    print("  " + "─" * (len(header) - 2))

    for s in sessions:
        session_id = s["session_id"]
        if len(session_id) > 32:
            session_id = session_id[:31] + "…"

        # Posting status: fraction of turns that are posted
        post_str = f"{s['postings']}/{s['turns']}" if s['turns'] > 0 else "—"

        print(
            f"  {session_id:<34}{sep}"
            f"{s['turns']:>5}{sep}"
            f"{s['concepts']:>8}{sep}"
            f"{post_str:>4}{sep}"
            f"{format_size(s['size']):>8}{sep}"
            f"{format_relative_time(s.get('last_updated', 0))}"
        )

    total_turns = sum(s["turns"] for s in sessions)
    total_concepts = sum(s["concepts"] for s in sessions)
    total_size = sum(s["size"] for s in sessions)
    print()
    print(
        f"  {len(sessions)} session(s), {total_turns} turns, "
        f"{total_concepts} concepts, {format_size(total_size)}"
    )
    return 0


def _run_all_profiles(args) -> int:
    sessions = list_all_profile_sessions()

    if not sessions:
        if args.json:
            print("[]")
        else:
            print("(no sessions found across any profile)")
        return 0

    if args.json:
        import json
        print(json.dumps(sessions, indent=2, default=str, ensure_ascii=False))
        return 0

    # Table header
    sep = "  "
    header = (
        f"  {'PROFILE':<20}{sep}"
        f"{'SESSION':<34}{sep}"
        f"{'TURNS':>5}{sep}"
        f"{'RAW':>6}{sep}"
        f"{'SIZE':>8}"
    )
    print(header)
    print("  " + "─" * (len(header) - 2))

    for s in sessions:
        session_id = s["session_id"]
        if len(session_id) > 32:
            session_id = session_id[:31] + "…"
        profile = s["profile"]
        if len(profile) > 18:
            profile = profile[:17] + "…"

        print(
            f"  {profile:<20}{sep}"
            f"{session_id:<34}{sep}"
            f"{s['turns']:>5}{sep}"
            f"{s['raw_msgs']:>6}{sep}"
            f"{format_size(s['size']):>8}"
        )

    total_turns = sum(s["turns"] for s in sessions)
    print()
    print(
        f"  {len(sessions)} session(s) across "
        f"{len(set(s['profile'] for s in sessions))} profile(s), "
        f"{total_turns} turns total"
    )
    return 0
