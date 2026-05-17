"""hermes decohere knowledge — manage shared knowledge sources and config."""

from __future__ import annotations

import json
import sys

from ..config import (
    DecohereUserConfig,
    load_decohere_config,
    save_decohere_config,
)
from ..knowledge.shared_store import SharedStore
from ._shared import resolve_hermes_home


def register_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "knowledge",
        help="Manage shared knowledge sources and injection config",
        description="View, select, and configure cross-session knowledge sources.",
    )
    sub = parser.add_subparsers(dest="knowledge_command")

    # sources
    src = sub.add_parser("sources", help="List available knowledge sources")
    src.add_argument("--json", action="store_true", help="Output as JSON")
    src.add_argument("--interactive", action="store_true",
                    help="Interactive checkbox selector (prompt_toolkit)")

    # toggle
    tog = sub.add_parser("toggle", help="Enable/disable knowledge injection")
    tog.add_argument("state", nargs="?", choices=["on", "off"],
                     help="on=enable, off=disable (omit to show status)")

    # select
    sel = sub.add_parser("select", help="Select a session for knowledge import")
    sel.add_argument("session", help="Session ID")
    sel.add_argument("--turns", help="Comma-separated turn numbers (default: all)")

    # deselect
    desel = sub.add_parser("deselect", help="Remove a session from knowledge sources")
    desel.add_argument("session", help="Session ID")

    # exclude
    exc = sub.add_parser("exclude", help="Manage concept exclusion rules")
    exc_sub = exc.add_subparsers(dest="exclude_command")
    exc_add = exc_sub.add_parser("add", help="Add an exclusion pattern")
    exc_add.add_argument("pattern", help="Regex pattern to exclude")
    exc_rm = exc_sub.add_parser("remove", help="Remove an exclusion pattern")
    exc_rm.add_argument("pattern", help="Pattern to remove")
    exc_sub.add_parser("list", help="List active exclusion rules")

    # config
    cfg = sub.add_parser("config", help="Show current knowledge configuration")
    cfg.add_argument("--json", action="store_true", help="Output as JSON")


def run(args) -> int:
    try:
        home = resolve_hermes_home()
        cmd = getattr(args, "knowledge_command", None)

        if cmd == "sources":
            return _cmd_sources(home, args)
        elif cmd == "toggle":
            return _cmd_toggle(home, args)
        elif cmd == "select":
            return _cmd_select(home, args)
        elif cmd == "deselect":
            return _cmd_deselect(home, args)
        elif cmd == "exclude":
            return _cmd_exclude(home, args)
        elif cmd == "config":
            return _cmd_config(home, args)
        else:
            print("Usage: hermes decohere knowledge <command>")
            print("Commands: sources, toggle, select, deselect, exclude, config")
            return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _cmd_sources(home, args) -> int:
    """List available knowledge sources from session decohere.db files."""
    from ._shared import list_all_profile_sessions

    sessions = list_all_profile_sessions()
    # Filter to sessions with turns
    sessions = [s for s in sessions if s.get("turns", 0) > 0]

    if not sessions:
        if args.json:
            print("[]")
        else:
            print("No sessions with ledger entries found.")
        return 0

    # Check which are already in shared store
    store = SharedStore(home)
    try:
        shared = store.source_summary()
        shared_map = {s["session"]: s["count"] for s in shared}
    finally:
        store.close()

    if args.json:
        output = []
        for s in sessions:
            output.append({
                "session_id": s["session_id"],
                "turns": s["turns"],
                "profile": s.get("profile", "?"),
                "in_shared_store": s["session_id"] in shared_map,
                "shared_concepts": shared_map.get(s["session_id"], 0),
            })
        print(json.dumps(output, indent=2, ensure_ascii=False))
    elif getattr(args, "interactive", False):
        from .interactive import interactive_knowledge_sources
        interactive_knowledge_sources(home)
    else:
        print(f"  {'SESSION_ID':<34} {'TURNS':>6} {'IN SHARED':>10}  PROFILE")
        for s in sessions:
            in_store = shared_map.get(s["session_id"], 0)
            mark = f"{in_store} concepts" if in_store else "—"
            print(
                f"  {s['session_id']:<34} {s['turns']:>6} {mark:>10}  "
                f"{s.get('profile', '?')}"
            )
        print(f"\n{sessions} sources. Use 'hermes decohere knowledge select <id>' to import.")

    return 0


def _cmd_toggle(home, args) -> int:
    config = load_decohere_config(home)

    if args.state is None:
        status = "ON" if config.knowledge_injection else "OFF"
        print(f"Knowledge injection: {status}")
        return 0

    config.knowledge_injection = (args.state == "on")
    save_decohere_config(home, config)
    status = "ON" if config.knowledge_injection else "OFF"
    print(f"Knowledge injection: {status}")
    return 0


def _cmd_select(home, args) -> int:
    config = load_decohere_config(home)

    turns = None
    if args.turns:
        try:
            turns = [int(t.strip()) for t in args.turns.split(",") if t.strip()]
        except ValueError:
            print("Error: --turns must be comma-separated integers", file=sys.stderr)
            return 1

    # Remove existing entry for this session
    config.knowledge_sources = [
        s for s in config.knowledge_sources
        if s.get("session") != args.session
    ]

    config.knowledge_sources.append({
        "session": args.session,
        "turns": turns or [],
    })
    save_decohere_config(home, config)

    turn_str = f"turns {turns}" if turns else "all turns"
    print(f"✓ {args.session}: {turn_str} selected for knowledge import.")
    return 0


def _cmd_deselect(home, args) -> int:
    config = load_decohere_config(home)

    before = len(config.knowledge_sources)
    config.knowledge_sources = [
        s for s in config.knowledge_sources
        if s.get("session") != args.session
    ]
    after = len(config.knowledge_sources)

    save_decohere_config(home, config)

    if before == after:
        print(f"Session '{args.session}' was not selected.")
    else:
        print(f"✓ {args.session}: removed from knowledge sources.")
    return 0


def _cmd_exclude(home, args) -> int:
    config = load_decohere_config(home)
    sub = getattr(args, "exclude_command", None)

    if sub == "list" or sub is None:
        if not config.knowledge_exclude:
            print("No exclusion rules configured.")
        else:
            print("Active exclusion rules:")
            for pat in config.knowledge_exclude:
                print(f"  • {pat}")
        return 0

    if sub == "add":
        if args.pattern in config.knowledge_exclude:
            print(f"Pattern '{args.pattern}' already excluded.")
        else:
            config.knowledge_exclude.append(args.pattern)
            save_decohere_config(home, config)
            print(f"✓ Added exclusion: {args.pattern}")
        return 0

    if sub == "remove":
        if args.pattern in config.knowledge_exclude:
            config.knowledge_exclude.remove(args.pattern)
            save_decohere_config(home, config)
            print(f"✓ Removed exclusion: {args.pattern}")
        else:
            print(f"Pattern '{args.pattern}' not found in exclusions.")
        return 0

    return 0


def _cmd_config(home, args) -> int:
    config = load_decohere_config(home)

    if args.json:
        print(json.dumps(config.to_dict(), indent=2, ensure_ascii=False))
        return 0

    status = "✓ ON" if config.knowledge_injection else "✗ OFF"
    print(f"Knowledge injection:  {status}")
    print(f"Retrieval mode:       {config.retrieval_mode}")
    print(f"Max concepts:          {config.injection_max_concepts}")
    print(f"Token budget:          {config.injection_max_tokens_pct * 100:.0f}%")

    if config.knowledge_sources:
        print(f"\nSelected sources ({len(config.knowledge_sources)}):")
        for s in config.knowledge_sources:
            turns_str = f"turns {s['turns']}" if s.get("turns") else "all turns"
            print(f"  • {s['session']} ({turns_str})")
    else:
        print("\nSelected sources: (none)")

    if config.knowledge_exclude:
        print(f"\nExclusion rules ({len(config.knowledge_exclude)}):")
        for pat in config.knowledge_exclude:
            print(f"  • {pat}")
    else:
        print("\nExclusion rules: (none)")

    return 0
