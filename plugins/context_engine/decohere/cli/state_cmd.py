"""hermes decohere state — per-session shared state (L2 working memory)."""

from __future__ import annotations

import json
import sys

from ._shared import (
    open_db,
    resolve_hermes_home,
    resolve_session,
)


def register_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "state",
        help="Manage per-session shared state (working memory)",
        description="Get, set, list, and delete key-value state entries.",
    )
    sub = parser.add_subparsers(dest="state_command")
    sub.add_parser("list", help="List all state entries")
    get_p = sub.add_parser("get", help="Get a state value")
    get_p.add_argument("key", help="State key")
    get_p.add_argument("--scope", default="session", choices=["session", "user", "app"])
    set_p = sub.add_parser("set", help="Set a state value")
    set_p.add_argument("key", help="State key")
    set_p.add_argument("value", help="State value")
    set_p.add_argument("--scope", default="session", choices=["session", "user", "app"])
    del_p = sub.add_parser("delete", help="Delete a state entry", aliases=["rm", "del"])
    del_p.add_argument("key", help="State key")
    del_p.add_argument("--scope", default="session", choices=["session", "user", "app"])
    sub.add_parser("clear", help="Clear all session-scoped state")


def run(args) -> int:
    try:
        home = resolve_hermes_home()
        sid, db_path = resolve_session(home, None)

        # Use read-write connection for write operations
        cmd = getattr(args, "state_command", "list")
        readonly = cmd in ("list", "get", None)

        conn = open_db(db_path, readonly=readonly)
        try:
            from plugins.context_engine.decohere.io.state_store import StateStore
            store = StateStore(conn._conn if hasattr(conn, '_conn') else conn)

            if cmd == "list" or cmd is None:
                return _cmd_list(store)
            elif cmd == "get":
                return _cmd_get(store, args)
            elif cmd == "set":
                return _cmd_set(store, args, conn)
            elif cmd in ("delete", "rm", "del"):
                return _cmd_delete(store, args, conn)
            elif cmd == "clear":
                return _cmd_clear(store, conn)
            else:
                print(f"Unknown: {cmd}", file=sys.stderr)
                return 1
        finally:
            if not readonly:
                conn.commit()
            conn.close()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _cmd_list(store) -> int:
    all_state = store.get_all()
    if not all_state:
        print("No state entries.")
        return 0

    print(f"  {'KEY':<30} {'SCOPE':<10} VALUE")
    for key, value in all_state.items():
        scope = "session"
        display_key = key
        if ":" in key:
            scope, display_key = key.split(":", 1)
        val = value[:60] if len(value) > 60 else value
        print(f"  {display_key:<30} {scope:<10} {val}")
    return 0


def _cmd_get(store, args) -> int:
    val = store.get(args.key, args.scope)
    if val is None:
        print(f"(not set)")
    else:
        print(val)
    return 0


def _cmd_set(store, args, conn) -> int:
    store.set(args.key, args.value, args.scope)
    conn.commit()
    print(f"✓ {args.scope}:{args.key} = {args.value}")
    return 0


def _cmd_delete(store, args, conn) -> int:
    if store.delete(args.key, args.scope):
        conn.commit()
        print(f"✓ {args.scope}:{args.key} deleted")
    else:
        print(f"Key '{args.key}' not found in scope '{args.scope}'")
    return 0


def _cmd_clear(store, conn) -> int:
    count = store.clear_scope("session")
    conn.commit()
    print(f"✓ Cleared {count} session-scoped state entries")
    return 0
