"""hermes decohere browse — interactive session and knowledge browser."""

from __future__ import annotations

import sys

from ._shared import resolve_hermes_home
from .interactive import interactive_sessions


def register_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "browse",
        help="Interactive browser for sessions and knowledge",
        description="Browse sessions, view turns, and manage shared knowledge interactively.",
    )


def run(args) -> int:
    try:
        home = resolve_hermes_home()
        return interactive_sessions(home)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
