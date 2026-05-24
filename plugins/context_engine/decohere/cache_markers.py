"""Double-window cache markers for decohere-managed sessions.

When decohere replaces the conversation history with system-role ledger
messages, the built-in ``system_and_3`` cache strategy places markers on
ledger entries that change every turn — defeating the cache.

This module provides ``apply_decohere_cache_markers()`` which walks
backward from the message tail, skips decohere-injected messages, and
places ``cache_control`` markers on the last 2 *real* (non-decohere,
non-system) messages.  This runs as a pre-pass before the built-in
``apply_anthropic_cache_control()`` strategy.

Pure functions — no class state.
"""

from __future__ import annotations

import copy
from typing import Any


# Names assigned to decohere-injected messages
_DECOHERE_NAMES = frozenset({
    "ledger_l1", "turn_context", "turn_index",
    "shared_state", "shared_knowledge",
})


def _is_decohere_message(msg: dict[str, Any]) -> bool:
    """Return True if the message was injected by decohere."""
    if msg.get("_decohere_injected"):
        return True
    if msg.get("name") in _DECOHERE_NAMES:
        return True
    return False


def apply_decohere_cache_markers(
    messages: list[dict[str, Any]],
    *,
    cache_ttl: str = "5m",
    max_markers: int = 2,
) -> list[dict[str, Any]]:
    """Place cache markers on the last N non-decohere messages.

    This is designed to run as a pre-pass: it places markers on real
    conversation messages (user/assistant/tool) that were preserved in
    the history alongside decohere's injected context.

    After this, the built-in ``apply_anthropic_cache_control()`` still
    runs — its markers will be additive.  The key effect is that real
    conversation messages get cached instead of volatile ledger entries.

    Args:
        messages: Full message list (system prompt + conversation + ledger).
        cache_ttl: Cache TTL string ('5m' or '1h').
        max_markers: Maximum number of cache markers to place (default 2).

    Returns:
        Deep copy of messages with cache markers applied.
    """
    messages = copy.deepcopy(messages)
    if not messages:
        return messages

    marker = {"type": "ephemeral"}
    if cache_ttl == "1h":
        marker["ttl"] = "1h"

    # Walk backward, skip system and decohere messages, mark real ones
    placed = 0
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg.get("role") == "system":
            continue
        if _is_decohere_message(msg):
            continue

        # Place cache marker on this real message
        _apply_marker(msg, marker)
        placed += 1
        if placed >= max_markers:
            break

    return messages


def _apply_marker(msg: dict[str, Any], marker: dict[str, str]) -> None:
    """Add cache_control to a message, handling string vs list content."""
    content = msg.get("content")

    if msg.get("role") == "tool":
        msg["cache_control"] = marker
        return

    if content is None or content == "":
        msg["cache_control"] = marker
        return

    if isinstance(content, str):
        msg["content"] = [
            {"type": "text", "text": content, "cache_control": marker}
        ]
        return

    if isinstance(content, list) and content:
        last = content[-1]
        if isinstance(last, dict):
            last["cache_control"] = marker
