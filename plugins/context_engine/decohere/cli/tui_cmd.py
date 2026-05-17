"""hermes decohere tui — full-featured interactive TUI for view and edit."""

from __future__ import annotations

import sys


def register_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "tui",
        help="Full interactive TUI — browse and edit sessions, concepts, state",
        description=(
            "Launch a terminal UI for viewing and editing decohere data:\n"
            "  - Browse sessions and ledger turns\n"
            "  - Search, view, and edit concepts (FTS5)\n"
            "  - View raw conversation messages\n"
            "  - Edit session state key-value pairs\n"
        ),
    )
    parser.add_argument(
        "--db",
        help="Directly open a specific decohere.db file (path or session dir)",
    )


def run(args) -> int:
    try:
        from ._shared import resolve_hermes_home
        from .tui import run_tui

        home = resolve_hermes_home(profile=getattr(args, 'profile', None),
                                  home=getattr(args, 'home', None))
        db_path = getattr(args, 'db', None)
        initial_db = Path(db_path).expanduser() if db_path else None
        return run_tui(home, initial_db)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
