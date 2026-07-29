"""hermes decohere show — display full detail of a single ledger entry."""

from __future__ import annotations

import json
import sys

from ._shared import (
    DecohereCLIError,
    open_db,
    parse_json_field,
    resolve_hermes_home,
    resolve_session,
)


def register_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "show",
        help="Show full detail of a single ledger entry",
        description="Display all fields of a ledger entry, organized by layer.",
    )
    parser.add_argument("--profile", help="Use a specific profile")
    parser.add_argument("--home", help="Directly specify hermes home path")
    parser.add_argument("--session", help="Session ID (default: most recently modified)")
    parser.add_argument("--turn", type=int, required=True, help="Turn number to show")
    parser.add_argument("--layer", choices=["l1", "l2", "full"], default="full",
                       help="l1=reference+metadata, l2=concepts/narrative only, full=all")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--interactive", action="store_true",
                       help="Full-screen detail view (prompt_toolkit)")


def run(args) -> int:
    try:
        home = resolve_hermes_home(profile=args.profile, home=args.home)
        sid, db_path = resolve_session(home, args.session)
        conn = open_db(db_path, readonly=True)

        row = conn.execute(
            "SELECT turn_n, entry_json, posted_at, validated "
            "FROM ledger_entries WHERE turn_n = ?",
            (args.turn,),
        ).fetchone()

        if not row:
            print(f"Turn {args.turn} not found in session {sid}")
            conn.close()
            return 1

        # row = (turn_n, entry_json, posted_at, validated) — no row_factory
        entry = parse_json_field(row[1], {})
        conn.close()

        if args.json:
            output = {
                "turn_n": row[0],
                "posted_at": row[2],
                "validated": bool(row[3]),
                "entry": entry,
            }
            print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
            return 0

        if getattr(args, "interactive", False):
            from .interactive import interactive_show_entry
            interactive_show_entry(entry, row[0], bool(row[3]))
            return 0

        _print_entry(entry, row, args.layer)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _print_entry(entry: dict, row: tuple, layer: str) -> None:
    """Pretty-print a ledger entry."""
    # row = (turn_n, entry_json, posted_at, validated)
    msg_range = entry.get("message_range", [])
    tools = entry.get("tools", []) or []
    files = entry.get("files_touched", []) or []
    task = (entry.get("relevant_metadata") or {}).get("task", "")
    ref_class = (entry.get("relevant_metadata") or {}).get("reference_class", "")

    # Header
    print(f"── Turn {row[0]} ─────────────────────────────────────────")
    print(f"  Message range:  {msg_range[0]} – {msg_range[-1]}" if msg_range else "  Message range:  —")
    print(f"  Tools:          {', '.join(t.get('name', str(t)) if isinstance(t, dict) else str(t) for t in tools) if tools else '(none)'}")
    print(f"  Files:          {', '.join(files) if files else '(none)'}")
    print(f"  Validated:      {'✓' if row[3] else '✗ (pending)'}")
    if task:
        print(f"\n  Task:           {task}")
    if ref_class:
        print(f"  Ref class:      {ref_class}")

    if layer in ("l1", "full"):
        _print_l1(entry)

    if layer in ("l2", "full"):
        _print_l2(entry)

    if layer == "full":
        _print_reflection(entry)

    if not row[3] and not (entry.get("narrative") or {}).get("summary"):
        print("\n  ℹ Status: Pending background extraction (placeholder created).")
        print("  Raw messages are saved in session. Use `hermes decohere messages` or `--json` to view.")


def _print_l1(entry: dict) -> None:
    """Print L1 reference layer."""
    refs = entry.get("reference_documentation", []) or []
    if refs:
        print(f"\n  ── Reference Documentation ──")
        for ref in refs:
            if isinstance(ref, dict):
                print(f"  • {ref.get('title', ref.get('url', str(ref)))}")
            elif isinstance(ref, str):
                print(f"  • {ref}")


def _print_l2(entry: dict) -> None:
    """Print L2 processing layer."""
    # Concepts
    concepts = entry.get("concepts_and_definitions", []) or []
    if concepts:
        print(f"\n  ── Concepts ──")
        for c in concepts:
            if isinstance(c, dict):
                term = c.get("term", "?")
                defn = c.get("definition", "")
                print(f"  • {term}")
                if defn:
                    print(f"    {defn}")

    # Narrative
    narrative = entry.get("narrative", {}) or {}
    if narrative.get("summary"):
        print(f"\n  ── Narrative ──")
        print(f"  {narrative['summary']}")
        cross = narrative.get("cross_references", []) or []
        if cross:
            print(f"  Cross-refs: {', '.join(str(x) for x in cross)}")

    # Decisions
    decisions = entry.get("decisions_and_rationale", []) or []
    if decisions:
        print(f"\n  ── Decisions ──")
        for d in decisions:
            if isinstance(d, dict):
                dec = d.get("decision", "")
                rat = d.get("rationale", "")
                print(f"  • {dec}")
                if rat:
                    print(f"    → {rat}")

    # Procedures
    procs = entry.get("procedures", []) or []
    if procs:
        print(f"\n  ── Procedures ──")
        for p in procs:
            if isinstance(p, dict):
                print(f"  • {p.get('action', p.get('step', str(p)))}")
            elif isinstance(p, str):
                print(f"  • {p}")

    # Insights
    insights = entry.get("insights_and_learnings", []) or []
    if insights:
        print(f"\n  ── Insights ──")
        for i in insights:
            if isinstance(i, str):
                print(f"  • {i}")

    # User Intent
    intent = entry.get("user_intent", "")
    if intent:
        print(f"\n  ── User Intent ──")
        print(f"  {intent}")


def _print_reflection(entry: dict) -> None:
    """Print critical reflection."""
    cr = entry.get("critical_reflection", {}) or {}
    has_content = any(cr.values())
    if not has_content:
        return

    print(f"\n  ── Critical Reflection ──")
    ignored = cr.get("ignored_perspectives", []) or []
    if ignored:
        print(f"  ↳ ignored_perspectives:")
        for p in ignored:
            print(f"    • {p}")
    gaps = cr.get("logical_gaps", []) or []
    if gaps:
        print(f"  ↳ logical_gaps:")
        for g in gaps:
            print(f"    • {g}")
    improvements = cr.get("improvement_directions", []) or []
    if improvements:
        print(f"  ↳ improvements:")
        for imp in improvements:
            print(f"    • {imp}")
