"""hermes decohere search — full-text search across concepts and narratives."""

from __future__ import annotations

import json
import sys

from ._shared import (
    open_db,
    parse_json_field,
    resolve_hermes_home,
    resolve_session,
)


def register_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "search",
        help="Full-text search across concepts and narratives",
        description="Search concepts_fts (FTS5) and narrative summaries.",
    )
    parser.add_argument("--profile", help="Use a specific profile")
    parser.add_argument("--home", help="Directly specify hermes home path")
    parser.add_argument("--session", help="Session ID (default: most recently modified)")
    parser.add_argument("query", help="FTS5 query string (supports boolean operators)")
    parser.add_argument("--field", choices=["concepts", "narrative", "all"], default="all",
                       help="Field to search (default: all)")
    parser.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")


def run(args) -> int:
    try:
        home = resolve_hermes_home(profile=args.profile, home=args.home)
        sid, db_path = resolve_session(home, args.session)
        conn = open_db(db_path, readonly=True)

        results: list[dict] = []

        # FTS5 concept search
        if args.field in ("concepts", "all"):
            try:
                rows = conn.execute(
                    "SELECT rowid FROM concepts_fts "
                    "WHERE concepts_fts MATCH ? ORDER BY rank LIMIT ?",
                    (args.query, args.limit),
                ).fetchall()
                for r in rows:
                    turn_n = r[0]
                    entry_row = conn.execute(
                        "SELECT entry_json FROM ledger_entries WHERE turn_n = ?",
                        (turn_n,),
                    ).fetchone()
                    match_text = f"(concept match, turn {turn_n})"
                    if entry_row:
                        entry = parse_json_field(entry_row[0], {})
                        concepts = entry.get("concepts_and_definitions", []) or []
                        for c in concepts:
                            if isinstance(c, dict):
                                term = c.get("term", "")
                                if term:
                                    match_text = f"{term}: {c.get('definition', '')}"
                                    break
                    results.append({
                        "turn_n": turn_n,
                        "match_type": "concept",
                        "match": match_text,
                    })
            except Exception:
                pass

        # Narrative LIKE search
        if args.field in ("narrative", "all") and len(results) < args.limit:
            remaining = args.limit - len(results)
            like_pattern = f"%{args.query}%"
            rows = conn.execute(
                "SELECT turn_n, entry_json FROM ledger_entries "
                "WHERE entry_json LIKE ? ORDER BY turn_n DESC LIMIT ?",
                (like_pattern, remaining),
            ).fetchall()
            for r in rows:
                entry = parse_json_field(r[1])
                if not entry:
                    continue
                narrative = entry.get("narrative", {}) or {}
                summary = narrative.get("summary", "")
                if summary:
                    results.append({
                        "turn_n": r[0],
                        "match_type": "narrative",
                        "match": summary[:200],
                    })

        conn.close()

        if not results:
            print(f"No results for '{args.query}'")
            return 0

        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
            return 0

        _print_results(results, sid)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _print_results(results: list[dict], session_id: str) -> None:
    """Print formatted search results."""
    print(f"Session: {session_id}")
    print(f"Query:   '{sys.argv[-1] if sys.argv else ''}'\n")

    sep = "  "
    max_match = 70
    header = (
        f"  {'T#':>3}{sep}"
        f"{'TYPE':<10}{sep}"
        f"{'MATCH'}"
    )
    print(header)
    print("  " + "─" * (len(header)))

    type_markers = {"concept": "◆", "narrative": "¶"}

    for r in results:
        marker = type_markers.get(r["match_type"], "?")
        match_type = f"{marker} {r['match_type']}"
        match_text = r["match"]
        if len(match_text) > max_match:
            match_text = match_text[:max_match - 1] + "…"

        print(
            f"  {r['turn_n']:>3}{sep}"
            f"{match_type:<10}{sep}"
            f"{match_text}"
        )

    concept_count = sum(1 for r in results if r["match_type"] == "concept")
    narrative_count = sum(1 for r in results if r["match_type"] == "narrative")
    print(f"\n  {len(results)} result(s) — "
          f"{concept_count} concept, {narrative_count} narrative")
