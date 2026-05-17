"""hermes decohere list — list ledger entries in a session."""

from __future__ import annotations

import json
import sys

from ._shared import (
    format_timestamp,
    open_db,
    parse_json_field,
    resolve_hermes_home,
    resolve_session,
)


def register_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "list",
        help="List ledger entries in a session",
        description="List ledger entries with task, tools, and timestamps.",
    )
    parser.add_argument("--profile", help="Use a specific profile")
    parser.add_argument("--home", help="Directly specify hermes home path")
    parser.add_argument("--session", help="Session ID (default: most recently modified)")
    parser.add_argument("--limit", type=int, default=20, help="Max entries to show (default: 20)")
    parser.add_argument("--offset", type=int, default=0, help="Skip first N entries")
    parser.add_argument("--layer", choices=["l1", "l2", "full"], default="full",
                       help="Detail level (default: full)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--interactive", action="store_true",
                       help="Interactive turn browser (prompt_toolkit)")
    parser.add_argument("--no-placeholders", action="store_true",
                       help="Skip placeholder entries (no L2 content)")


def run(args) -> int:
    try:
        home = resolve_hermes_home(profile=args.profile, home=args.home)
        sid, db_path = resolve_session(home, args.session)
        conn = open_db(db_path, readonly=True)

        rows = conn.execute(
            "SELECT turn_n, entry_json, posted_at, validated "
            "FROM ledger_entries ORDER BY turn_n DESC LIMIT ? OFFSET ?",
            (args.limit, args.offset),
        ).fetchall()

        # Filter placeholders if requested
        if getattr(args, "no_placeholders", False):
            filtered = []
            for r in rows:
                entry = parse_json_field(r[1], {})
                if entry.get("entry_skipped"):
                    continue
                concepts = entry.get("concepts_and_definitions")
                if not concepts:
                    continue
                filtered.append(r)
            rows = filtered

        if not rows:
            print(f"Session {sid}: 0 ledger entries")
            conn.close()
            return 0

        if args.json:
            result = []
            for r in rows:
                entry = parse_json_field(r[1], {})
                result.append({
                    "turn_n": r[0],
                    "task": (entry.get("relevant_metadata") or {}).get("task", ""),
                    "tools": entry.get("tools", []) or [],
                    "files": entry.get("files_touched", []) or [],
                    "posted_at": r[2],
                    "validated": bool(r[3]),
                })
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif getattr(args, "interactive", False):
            from .interactive import interactive_list
            interactive_list(conn, sid, limit=args.limit)
        else:
            _print_table(rows, sid)

        conn.close()
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _print_table(rows: list, session_id: str) -> None:
    """Print a formatted table of ledger entries."""
    # Calculate column widths from data
    max_task = 28
    max_tools = 24

    # First pass: measure
    entries = []
    for r in rows:
        entry = parse_json_field(r[1], {})
        task = (entry.get("relevant_metadata") or {}).get("task", "") or ""
        if isinstance(task, str):
            max_task = min(max(max_task, len(task)), 40)
        tools_raw = entry.get("tools", []) or []
        tool_names = [t.get("name", t) if isinstance(t, dict) else str(t)
                       for t in tools_raw]
        tools_str = ", ".join(tool_names[:3])
        max_tools = min(max(max_tools, len(tools_str)), 32)
        concepts = entry.get("concepts_and_definitions")
        n_concepts = len(concepts) if concepts and isinstance(concepts, list) else 0
        entries.append((r, entry, task, tools_str, n_concepts))

    # Header
    print(f"Session: {session_id}\n")
    status_w = 4  # ✓/✗/◌
    sep = "  "
    header = (
        f"  {'#':>3}{sep}"
        f"{'S':^{status_w}}{sep}"
        f"{'C':>2}{sep}"
        f"{'TASK':<{max_task}}{sep}"
        f"{'TOOLS':<{max_tools}}{sep}"
        f"{'POSTED'}"
    )
    print(header)
    print("  " + "─" * (len(header) - 2))

    for r, entry, task, tools_str, n_concepts in entries:
        turn_n = r[0]
        validated = bool(r[3])
        concepts_count = n_concepts

        # Status indicator
        if validated:
            status = "✓"
        elif entry.get("entry_skipped"):
            status = "◌"  # skipped
        elif entry.get("concepts_and_definitions") is not None:
            status = "◐"  # posted but not validated
        else:
            status = "✗"  # placeholder, not posted

        # Truncate task
        task_display = task
        if isinstance(task_display, str) and len(task_display) > max_task:
            task_display = task_display[:max_task - 1] + "…"

        # Truncate tools
        tools_display = tools_str
        if len(tools_display) > max_tools:
            tools_display = tools_display[:max_tools - 1] + "…"

        concepts_str = str(concepts_count) if concepts_count > 0 else "·"

        print(
            f"  {turn_n:>3}{sep}"
            f"{status:^{status_w}}{sep}"
            f"{concepts_str:>2}{sep}"
            f"{task_display:<{max_task}}{sep}"
            f"{tools_display:<{max_tools}}{sep}"
            f"{format_timestamp(r[2])}"
        )

    # Footer
    total = len(rows)
    posted = sum(1 for _, e, _, _, _ in entries
                 if e.get("concepts_and_definitions") is not None)
    print(f"\n  {total} turn(s), {posted} posted, "
          f"{total - posted} pending  (✓=validated ◐=posted ◌=skipped ✗=pending)")
