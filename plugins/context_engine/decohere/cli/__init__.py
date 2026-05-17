"""hermes decohere CLI — manage session data with CRUD commands.

Entry point: decohere_command(args) — dispatched from hermes_cli/main.py.
"""

from __future__ import annotations

import sys

from . import (
    sessions_cmd, list_cmd, show_cmd, search_cmd,
    stats_cmd, export_cmd, edit_cmd, delete_cmd, vacuum_cmd,
    knowledge_cmd, browse_cmd, orphans_cmd, repost_cmd, state_cmd,
    tui_cmd,
)

# Command registry: subcommand name → module
COMMANDS = {
    "sessions": sessions_cmd,
    "list": list_cmd,
    "show": show_cmd,
    "search": search_cmd,
    "stats": stats_cmd,
    "export": export_cmd,
    "edit": edit_cmd,
    "delete": delete_cmd,
    "vacuum": vacuum_cmd,
    "knowledge": knowledge_cmd,
    "browse": browse_cmd,
    "orphans": orphans_cmd,
    "repost": repost_cmd,
    "state": state_cmd,
    "tui": tui_cmd,
}


def register_subparsers(subparsers) -> None:
    """Register all decohere subcommands on the given argparser subparsers."""
    for name, mod in COMMANDS.items():
        mod.register_parser(subparsers)


def decohere_command(args) -> int:
    """Dispatch the decohere command to the correct subcommand handler."""
    sub = getattr(args, "decohere_command", None)

    if sub is None or sub == "":
        print(
            "Usage: hermes decohere [OPTIONS] COMMAND [ARGS...]\n"
            "\n"
            "  Manage decohere session data.\n"
            "\n"
            "Commands:\n"
            "  sessions   List all sessions with decohere data\n"
            "  list       List ledger entries in a session\n"
            "  show       Show full detail of a single ledger entry\n"
            "  search     Full-text search across concepts and narratives\n"
            "  edit       Modify a field in a ledger entry\n"
            "  delete     Delete an entry or an entire session's data\n"
            "  export     Export session data to JSON / Markdown / YAML\n"
            "  stats      Show statistical summary of a session\n"
            "  vacuum     Clean orphan records and reclaim DB space\n"
            "\n"
            "Global options:\n"
            "  --profile NAME   Use a specific profile\n"
            "  --home PATH      Directly specify hermes home path\n"
            "  --help           Show this help message\n"
            "\n"
            "Run 'hermes decohere <command> --help' for per-command options.",
            file=sys.stderr,
        )
        return 1

    mod = COMMANDS.get(sub)
    if mod is None:
        print(f"Unknown decohere subcommand: {sub}", file=sys.stderr)
        print("Run 'hermes decohere' for available commands.", file=sys.stderr)
        return 1

    return mod.run(args)
