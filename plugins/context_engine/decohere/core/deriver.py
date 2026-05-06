"""Spec derivation — pure async computation. No I/O, no logging, no side-effects."""

from __future__ import annotations

from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning

from ..config import DeriverConfig
from .extractor import tool_chain_log
from .prompt import build_derivation_prompt
from .validator import ensure_spec_schema


async def infer_turn_structure(
    messages: list,
    config: DeriverConfig,
) -> dict:
    """Derive structured turn specification from raw messages.

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

    # 2. Build tool chain log
    chain = tool_chain_log(messages)

    # 3. Build prompt
    system_prompt, user_prompt = build_derivation_prompt(
        user_msg, chain, assistant_response
    )

    # 4. Call auxiliary model
    response = await async_call_llm(
        task="compression",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        response_format={"type": "json_object"},
    )

    content = extract_content_or_reasoning(response) or "{}"

    # 5. Parse + validate
    import json
    try:
        raw = json.loads(content)
    except json.JSONDecodeError:
        raw = {}

    return ensure_spec_schema(raw)
