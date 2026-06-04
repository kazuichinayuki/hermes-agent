"""Ledger entry posting — pure async computation. No I/O, no logging, no side-effects."""

from __future__ import annotations

import re

from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning

from ..config import LedgerConfig
from typing import Any
from .extractor import tool_chain_log
from .prompt import build_entry_prompt
from .validator import validate_entry


async def post_entry(
    messages: list[dict[str, Any]],
    config: LedgerConfig,
) -> dict[str, object]:
    """Post structured ledger entry from raw messages.

    Pure computation — receives everything via arguments, returns new dict.
    Does NOT read config files, write to DB, or log.

    Flow: extractor → prompt → API call → validator.
    """
    # 1. Extract last turn: user message + tool chain + assistant response
    user_msg = ""
    assistant_response = ""
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                user_msg = content
        elif msg.get("role") == "assistant" and msg.get("content"):
            content = msg["content"]
            if isinstance(content, str) and not msg.get("tool_calls"):
                assistant_response = content

    # ── Defense-in-depth: detect and skip regurgitated turns ──
    # Even though compress() should prevent gutted turns from being
    # scheduled, a belt-and-suspenders check here prevents phantom
    # entries if the guard fails or the messages were stripped
    # incompletely.
    if _is_regurgitated_input(user_msg, assistant_response):
        return validate_entry({})

    # 2. Build tool chain log
    chain = tool_chain_log(messages)

    # 2.5 Purify payloads before LLM extraction to save background tokens
    try:
        from .purifier import purify_text
        user_msg = purify_text(user_msg)
        assistant_response = purify_text(assistant_response)
    except Exception:
        pass  # safe fallback

    # 3. Build prompt
    system_prompt, user_prompt = build_entry_prompt(
        user_msg, chain, assistant_response
    )

    # 4. Call auxiliary model with JSON response_format enforcement.
    # Without this, providers like DeepSeek often wrap JSON in markdown fences,
    # causing json.loads() to fail → empty {} → all fields default to empty.
    response = await async_call_llm(
        task="compression",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        extra_body={"response_format": {"type": "json_object"}},
    )

    content = extract_content_or_reasoning(response) or "{}"

    # 5. Parse + validate with markdown-fence stripping
    import json
    raw = _extract_json(content)

    return validate_entry(raw)


def _extract_json(text: str) -> dict:
    """Extract JSON from text that may be wrapped in markdown fences.

    Handles: ```json ... ```, ``` ... ```, and bare JSON.
    Returns {} if no valid JSON found.
    """
    import json
    import re

    text = text.strip()

    # Try bare JSON first (fast path for json_object response_format)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown fences
    fence_patterns = [
        re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.DOTALL),
        re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL),
    ]
    for pat in fence_patterns:
        m = pat.search(text)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                continue

    # Last resort: find first { ... } block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    return {}


_LEDGER_HEADER_RE = re.compile(r"^## Ledger Entries\b", re.MULTILINE)
_LEDGER_MARKER_RE = re.compile(r"<!-- DECOHERE:BEGIN -->")
_TURN_BLOCK_RE = re.compile(
    r"\[Turn \d+\]\s*\n\s*(?:message_range:|tools:|files:|task:|ref_class:)"
)


def _is_regurgitated_input(user_msg: str, assistant_response: str) -> bool:
    """True if the input looks like regurgitated ledger context.

    When the LLM echoes injected ledger context in its assistant
    response, the extraction model would see it as new content and
    create phantom duplicate entries.  This function checks for
    telltale signs of regurgitation.

    Returns True when:
    - The assistant response contains ``## Ledger Entries`` headers
    - The assistant response contains decohere machine markers
    - The assistant response contains standalone ``[Turn N]`` blocks
      with ledger field names, AND has no substantial original content

    False positives (real assistant content that happens to match)
    are rare because real conversations don't contain ledger markup.
    """
    if not assistant_response or not assistant_response.strip():
        return False

    # Fast checks: ledger markup is highly specific
    if _LEDGER_HEADER_RE.search(assistant_response):
        return True
    if _LEDGER_MARKER_RE.search(assistant_response):
        return True

    # [Turn N] blocks are more ambiguous — only flag when the
    # assistant response is dominated by them (short content,
    # multiple blocks).
    turn_blocks = len(_TURN_BLOCK_RE.findall(assistant_response))
    if turn_blocks >= 3:
        return True
    if turn_blocks >= 1 and len(assistant_response.strip()) < 500:
        return True

    return False
