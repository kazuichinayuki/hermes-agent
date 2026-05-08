"""Mechanical extraction from raw messages. Pure functions only."""

from __future__ import annotations

from typing import Any

from ..types import MechanicalFields

_KEY_ARGS: dict[str, tuple[str, ...]] = {
    "read_file": ("path",),
    "write_file": ("path",),
    "web_search": ("query",),
    "web_extract": ("urls",),
    "browser_navigate": ("url",),
    "terminal": ("command",),
    "execute_code": (),
    "patch": ("path",),
    "memory": ("action", "target"),
}


def last_turn_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reverse-scan for last user message boundary. Returns NEW list slice."""
    if not messages:
        return []
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            return list(messages[i:])
    return list(messages)


def tool_calls_from_messages(messages: list[dict[str, Any]]) -> tuple[dict[str, str], ...]:
    """Parse tool_calls from assistant messages. Returns tuple of {name, args_summary}."""
    import json as _json

    results: list = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            name = fn.get("name", "unknown")
            args_raw = fn.get("arguments", "{}")
            args_summary = summarise_args(name, args_raw)
            results.append({"name": name, "args_summary": args_summary})
    return tuple(results)


def files_from_messages(messages: list[dict[str, Any]]) -> tuple[str, ...]:
    """Extract file paths from tool call args. Home dir → ~."""
    import json as _json
    from os.path import expanduser

    home = expanduser("~")
    path_tools = {"read_file", "write_file", "patch", "search_files"}
    results: list = []
    seen: set = set()

    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            if name not in path_tools:
                continue
            try:
                args = _json.loads(fn.get("arguments", "{}"))
            except (_json.JSONDecodeError, TypeError):
                continue
            path = args.get("path", "") or args.get("file_path", "")
            if path and path not in seen:
                seen.add(path)
                sanitized = path
                if home != "/" and path.startswith(home):
                    sanitized = "~" + path[len(home):]
                results.append(sanitized)
    return tuple(results)


def mechanical_fields(messages: list[dict[str, Any]]) -> MechanicalFields:
    """One-shot extraction: tools + files_touched."""
    return MechanicalFields(
        tools=tool_calls_from_messages(messages),
        files_touched=files_from_messages(messages),
    )


def tool_chain_log(messages: list[dict[str, Any]]) -> str:
    """Build structured tool chain log. ① fn(key_args) → result_summary per call."""
    import json as _json

    lines: list = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                name = fn.get("name", "unknown")
                args_raw = fn.get("arguments", "{}")
                key_args = summarise_args(name, args_raw)
                lines.append(f"① {name}({key_args})")
        elif role == "tool":
            content = msg.get("content", "") or ""
            summary = summarise_tool_result(content)
            if lines:
                lines[-1] += f" → {summary}"
        elif role == "assistant" and msg.get("content"):
            content = msg["content"]
            if content.strip():
                lines.append(f"\nAssistant response:\n{content}")
    return "\n".join(lines)


def summarise_args(fn: str, args_raw: str | dict[str, Any]) -> str:
    """Keep key arguments, drop boilerplate."""
    import json as _json

    key_args = _KEY_ARGS.get(fn, ())
    if not key_args:
        return ""
    try:
        args = (
            _json.loads(args_raw)
            if isinstance(args_raw, str)
            else (args_raw or {})
        )
    except (_json.JSONDecodeError, TypeError):
        return ""
    parts = []
    for k in key_args:
        if k in args:
            val = args[k]
            parts.append(f"{k}={_json.dumps(val, ensure_ascii=False)}")
    return ", ".join(parts)


def summarise_tool_result(content: str) -> str:
    """Describe result type + size. Never truncate — describe it."""
    import json as _json

    n_chars = len(content)
    try:
        data = _json.loads(content)
        if isinstance(data, dict):
            keys = list(data.keys())[:5]
            return f"JSON dict {{{', '.join(keys)}}}, {n_chars} chars"
        if isinstance(data, list):
            return f"JSON array[{len(data)}], {n_chars} chars"
        return f"JSON value, {n_chars} chars"
    except (_json.JSONDecodeError, TypeError):
        return f"text ({n_chars} chars)"
