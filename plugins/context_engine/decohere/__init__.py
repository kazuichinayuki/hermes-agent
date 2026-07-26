"""Decohere context engine plugin. Entry layer — orchestration only.

Matches the ContextEngine ABC signature exactly.
Hermes calls compress() before each LLM turn — that's where both
posting AND context building happen.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Any

from agent.context_engine import ContextEngine

from .config import LedgerConfig
from .config import load_decohere_config as _load_user_config
from .context.builder import (
    build_fallback_context,
    build_raw_context,
    build_ledger_context,
    build_hint_context,
    build_query_focused_context,
)
from .context.classifier import check_readiness, should_skip_entry, classify_context_budget
from .types import BudgetTier
from .context.formatter import format_entry_layer, format_proc_layer
from .context.placeholder import build_placeholder
from .core.extractor import last_turn_messages, mechanical_fields
from .io.session_io import SessionIO
from .knowledge import SharedStore, build_injection_message
from .monitoring.reporter import HealthReporter
from .scheduling.metrics import MetricsCollector
from .scheduling.task_manager import TaskManager

logger = logging.getLogger(__name__)

class Decohere(ContextEngine):
    """Structured ledger entries as context. Thin coordinator.

    Delegates to:
    - monitoring.*        → health checks, logging
    - scheduling.*        → async tasks, metrics
    - context.*           → classification, placeholder, builder, formatter
    - io.SessionIO        → all reads/writes

    This class contains ZERO business logic. Every method delegates.
    """

    name = "decohere"

    # ── Token state (read by run_agent.py) ──
    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    last_total_tokens: int = 0
    threshold_tokens: int = 200_000
    context_length: int = 0
    compression_count: int = 0

    # ── Compaction parameters ──
    # Decohere is NOT a token-pressure compressor.  It injects context
    # from the ledger.  threshold_percent = 1.0 means the built-in
    # preflight check only fires at 100% context (API error limit),
    # not at the compression.threshold from config.yaml (which is
    # for the built-in ContextCompressor, not for Decohere).
    threshold_percent: float = 1.0
    protect_first_n: int = 0
    protect_last_n: int = 0
    format_version: int = 2
    can_reduce_tokens: bool = False

    def __init__(self, context_length: int = 200_000):
        self.context_length = context_length
        self._session_id: str | None = None
        self._io: SessionIO | None = None
        self._tasks: TaskManager | None = None
        self._metrics: MetricsCollector | None = None
        self._health: HealthReporter | None = None
        self._cfg: LedgerConfig | None = None
        self._last_compressed_turns: int = 0
        self._current_turn_user_hash: int | None = None
        self._canary_token: str | None = None
        self._shared_store: SharedStore | None = None
        self._user_config: "DecohereUserConfig | None" = None

        # Register Purifier hook for global tool interception
        try:
            from hermes_cli.plugins import get_plugin_manager
            pm = get_plugin_manager()
            pm.register_hook("transform_tool_result", self.transform_tool_result)
        except Exception as e:
            logger.warning("Decohere failed to register transform_tool_result hook: %s", e)

    # ── Lifecycle ──────────────────────────────────────────────────────

    def on_session_start(self, session_id: str, **kwargs):
        """Initialize all layers from hermes_home — the only external anchor."""
        hermes_home = kwargs.get("hermes_home", "~/.hermes")
        home = Path(hermes_home).expanduser()

        self._session_id = session_id
        aux_cfg, comp_cfg = self._read_config(home / "config.yaml")
        self._cfg = LedgerConfig.from_aux_config(aux_cfg, compression=comp_cfg)
        # NOTE: compression.threshold from config.yaml is intentionally
        # NOT applied.  Decohere is a context injector, not a token-
        # pressure reducer.  Applying compression.threshold (e.g. 0.67)
        # causes premature preflight compression triggers that emit
        # "🗜️ Compacting context" and run unnecessary session machinery.
        self._io = SessionIO(home, session_id)
        self._tasks = TaskManager(self._cfg)
        self._metrics = MetricsCollector()
        self._health = HealthReporter(self._io, self._metrics)
        self._health.snapshot_session_start(session_id, kwargs.get("platform", ""))
        # ── Per-session canary token for regurgitation detection ──
        self._canary_token = secrets.token_hex(4)
        # ── Shared knowledge store ──
        self._user_config = _load_user_config(home)
        if self._user_config.knowledge_injection:
            self._shared_store = SharedStore(home)

    def on_session_end(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        if self._health and self._tasks:
            self._health.snapshot_session_end(
                session_id, self._tasks.pending_count(session_id)
            )
            self._tasks.cleanup(session_id)
        if self._io:
            self._io.close()
            self._io = None  # prevent should_compress() from using closed connection
        if self._shared_store:
            self._shared_store.close()
            self._shared_store = None

    def on_session_reset(self):
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0
        self._last_compressed_turns = 0
        self._current_turn_user_hash = None
        self._initial_compress_done = False
        self._canary_token = secrets.token_hex(4)

    # ── Token tracking ─────────────────────────────────────────────────

    def update_from_response(self, usage: dict[str, int]) -> None:
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)
        self.last_completion_tokens = usage.get("completion_tokens", 0)
        self.last_total_tokens = usage.get("total_tokens", 0)

    # ── Context management ─────────────────────────────────────────────

    def should_compress(self, tokens: int = None) -> bool:
        """Return True only when tokens exceed the threshold."""
        if tokens is None:
            return False
        return tokens >= self.threshold_tokens

    def compress(
        self,
        messages: list[dict[str, Any]],
        current_tokens: int = None,
        focus_topic: str = None,
    ) -> list[dict[str, Any]]:
        # Decohere no longer acts as a token compressor.
        # It relies on conversation_loop.py intercepting the LLM turn.
        return messages

    def extract_knowledge_async(
        self,
        messages: list[dict[str, Any]],
    ) -> None:
        """Phase 1: extract last turn → placeholder → async posting."""
        sid = self._session_id
        if not sid or not self._io:
            return

        messages = [m for m in messages if m is not None]

        _LEDGER_NAMES = frozenset({
            "ledger_l1", "turn_context", "turn_index",
            "shared_state", "shared_knowledge",
        })
        real_msgs = [m for m in messages if m.get("name") not in _LEDGER_NAMES]
        _strip_ledger_sections(real_msgs, canary_token=self._canary_token)

        turn_msgs = last_turn_messages(real_msgs)
        user_content = _extract_user_content(turn_msgs)
        user_hash = hash(user_content) if user_content else None

        if user_hash != self._current_turn_user_hash:
            self._current_turn_user_hash = user_hash

            should_skip = should_skip_entry(turn_msgs)
            if not should_skip:
                if _is_gutted_turn(turn_msgs):
                    should_skip = True
            mechanical = mechanical_fields(turn_msgs)
            msg_range = self._io.compute_range(turn_msgs)
            placeholder = build_placeholder(
                turn_n=self._io.turn_count() + 1,
                message_range=msg_range,
                mechanical=mechanical,
                skipped=should_skip,
            )
            range_ok = self._health.verify_range(msg_range, len(turn_msgs))
            self._io.save_turn(placeholder)
            persist_ok = self._health.verify_persisted(placeholder["n"])
            if not range_ok.ok or not persist_ok.ok:
                self._metrics.record_degraded()
            if not should_skip:
                self._tasks.schedule(sid, turn_msgs, self._io, self._metrics)

    def build_context_payload(
        self,
        messages: list[dict[str, Any]],
        focus_topic: str = None,
    ) -> list[dict[str, Any]]:
        """Phase 2: read existing specs → build L1+L2 → return payload"""
        if not self._session_id or not self._io:
            return messages

        _LEDGER_NAMES = frozenset({
            "ledger_l1", "turn_context", "turn_index",
            "shared_state", "shared_knowledge",
        })

        self._initial_compress_done = True
        
        current_turns = self._io.turn_count()
        self._last_compressed_turns = current_turns
        turns = self._io.get_turns()
        readiness = check_readiness(turns, self._io.turn_count())

        if readiness.state == "legacy":
            return build_raw_context(messages)
        if readiness.state == "empty":
            return messages

        user_intent = focus_topic or _extract_intent_from_messages(messages)
        budget_tier = classify_context_budget(
            user_query=user_intent,
            turn_count=self._io.turn_count(),
        )

        if budget_tier == BudgetTier.HIGH:
            result = build_query_focused_context(
                query=user_intent,
                turns=list(readiness.turns),
                max_turns=self._cfg.max_turns,
                canary_token=self._canary_token,
            )
        elif budget_tier == BudgetTier.MEDIUM:
            result = build_ledger_context(
                turns=list(readiness.turns),
                max_turns=self._cfg.max_turns,
                canary_token=self._canary_token,
            )
        else:  # BudgetTier.LOW
            result = build_hint_context(
                list(readiness.turns),
                self._cfg.max_turns,
                last_turn_msgs=(
                    self._io.get_raw_messages()
                    if readiness.state != "ready"
                    else None
                ),
                canary_token=self._canary_token,
            )

        included_turns = _extract_turn_numbers(result)
        self._health.snapshot_compress(
            readiness, len(messages), turn_numbers=tuple(included_turns),
        )

        if self._shared_store and self._user_config:
            focus = focus_topic or _extract_intent_from_messages(messages)
            injection_msg = build_injection_message(
                store=self._shared_store,
                knowledge_injection=self._user_config.knowledge_injection,
                knowledge_sources=self._user_config.knowledge_sources,
                knowledge_exclude=self._user_config.knowledge_exclude,
                max_concepts=self._user_config.injection_max_concepts,
                max_tokens_pct=self._user_config.injection_max_tokens_pct,
                user_intent=focus,
            )
            if injection_msg:
                result = list(result) + [injection_msg]

        if self._io:
            state_block = self._io._state.format_for_context()
            if state_block:
                result = list(result) + [
                    {"role": "system", "name": "shared_state", "content": state_block}
                ]

        for msg in result:
            if msg.get("name") in _LEDGER_NAMES or msg.get("name") == "shared_knowledge":
                msg["_decohere_injected"] = True

        self.compression_count += 1
        
        # Prepend the injected context to the original messages
        return result + messages

    # ── Tool-mediated retrieval (Phase 3) ──────────────────────────────

    _RECALL_SCHEMA: dict[str, Any] = {
        "name": "recall_context",
        "description": (
            "Retrieve structured conversation context from the decohere "
            "ledger. Returns turn-by-turn summaries, decisions, procedures, "
            "and insights from earlier in this session. "
            "CRITICAL: Use this tool IMMEDIATELY if you lose track of the overarching goal, "
            "encounter a cascade of tool failures, or need to recall the rationale for previous decisions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Optional focus query. When provided, returns only "
                        "turns relevant to this query. When omitted, returns "
                        "the most recent turns."
                    ),
                },
                "max_turns": {
                    "type": "integer",
                    "description": "Maximum number of turns to return (default: 10).",
                },
            },
            "required": [],
        },
    }

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Expose recall_context tool to the agent."""
        if self._io and self._io.is_v2():
            return [self._RECALL_SCHEMA]
        return []

    def handle_tool_call(
        self,
        name: str,
        args: dict[str, Any],
        **kwargs,
    ) -> str:
        """Handle recall_context tool calls."""
        import json

        if name != "recall_context":
            return json.dumps({"error": f"Unknown tool: {name}"})

        if not self._io:
            return json.dumps({"error": "Context engine not initialized"})

        max_turns = args.get("max_turns", 10)
        query = args.get("query", "")

        turns = self._io.get_turns()
        if not turns:
            return json.dumps({"turns": [], "message": "No conversation history yet."})

        # Select recent turns (query-based filtering is future work)
        selected = turns[-max_turns:] if len(turns) > max_turns else turns

        # Build formatted output using existing formatters
        output_parts = []
        for turn in selected:
            if turn.get("entry_skipped"):
                continue
            n = turn.get("n", "?")
            entry = format_entry_layer(turn)
            proc = format_proc_layer(turn)
            output_parts.append(f"=== Turn {n} ===\n{entry}\n{proc}")

        return json.dumps(
            {
                "turns": len(selected),
                "total_available": len(turns),
                "context": "\n\n".join(output_parts) if output_parts else "(all turns were skipped)",
            },
            ensure_ascii=False,
        )

    def transform_tool_result(self, tool_name: str, args: dict, result: str, **kwargs) -> str | None:
        """Purify the tool result before it enters context."""
        try:
            if not isinstance(result, str):
                return None
            from .core.purifier import purify_text
            purified = purify_text(result)
            if purified != result:
                logger.debug("Decohere purifier compressed output for tool %s (len %d -> %d)", tool_name, len(result), len(purified))
            return purified
        except Exception as e:
            logger.warning("Decohere purifier failed for tool %s: %s", tool_name, e)
            return None

    # ── Status ─────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, object]:
        return {
            "last_prompt_tokens": self.last_prompt_tokens,
            "threshold_tokens": self.threshold_tokens,
            "context_length": self.context_length,
            "usage_percent": (
                min(100, self.last_prompt_tokens / self.context_length * 100)
                if self.context_length else 0
            ),
            "compression_count": self.compression_count,
            "ledger_entries": self._io.turn_count() if self._io else 0,
        }

    def update_model(self, model: str, context_length: int,
                     base_url: str = "", api_key: str = "", provider: str = "",
                     api_mode: str = "", **kwargs):
        self.context_length = context_length
        # Always 1.0 — Decohere doesn't do token-pressure compression.
        # See class-level comment on threshold_percent.
        self.threshold_tokens = context_length



    @staticmethod
    def _read_config(config_path: Path) -> tuple[dict | None, dict | None]:
        import yaml
        try:
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("auxiliary", {}), cfg.get("compression", {})
        except Exception:
            return None, None


def _extract_intent_from_messages(messages: list[dict[str, Any]]) -> str:
    """Extract a brief intent string from the last user message."""
    for msg in reversed(messages):
        if msg and msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                return content.strip()[:200]
    return ""


def _extract_user_content(messages: list[dict[str, Any]]) -> str | None:
    """Extract the user message content from turn messages for dedup hashing.

    Used by compress() to detect when it is being re-called for the same
    user turn (e.g. during tool-call loop iterations).
    """
    for msg in messages:
        if msg and msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                return content
    return None


_GUTTED_MIN_ASSISTANT_CHARS = 50


def _is_gutted_turn(turn_msgs: list[dict[str, Any]]) -> bool:
    """True if the turn's assistant messages were gutted by ledger stripping.

    When the LLM regurgitates injected ledger context as its response,
    ``_strip_ledger_sections`` removes the regurgitated blocks, leaving
    near-empty assistant messages.  These turns have no original content
    to extract — scheduling extraction would feed the extraction model
    empty or context-only input and create phantom duplicate entries.

    Returns True when:
    - The turn has NO tool calls (assistant didn't actually DO anything)
    - Every assistant message in the turn has ≤ GUTTED_MIN_ASSISTANT_CHARS
      of content (after stripping)

    Turns with tool calls are NOT gutted — the tool chain is real work.
    """
    has_tool_calls = False
    has_assistant = False
    for msg in turn_msgs:
        if msg.get("role") == "assistant":
            if msg.get("tool_calls"):
                has_tool_calls = True
            content = msg.get("content", "") or ""
            if isinstance(content, str) and len(content.strip()) > _GUTTED_MIN_ASSISTANT_CHARS:
                return False  # at least one substantive message
            has_assistant = True
    # Gutted: had assistant messages, but they're all empty/near-empty
    # and there were no tool calls (no actual work done).
    return has_assistant and not has_tool_calls


def _strip_ledger_sections(
    messages: list[dict[str, Any]],
    *,
    canary_token: str | None = None,
) -> None:
    """Strip regurgitated ledger content from assistant messages IN PLACE.

    The LLM may echo injected ledger context in its response.  If fed
    back into extraction, this creates a feedback loop of nested/
    duplicated ledger entries.

    Multi-layer detection (ordered most-specific → least-specific):
    0. Per-session canary token [canary:XXXX] — zero false-positives
    1. <!-- DECOHERE:BEGIN --> machine markers (from builder.py)
    2. ## Ledger Entries headers
    3. [INTERNAL CONTEXT — DO NOT ECHO ...] guard text
    4. Standalone [Turn N] blocks followed by ledger field patterns

    For each detected pattern, everything from the first match to the
    end of the message is removed; only preceding real content is kept.
    Only assistant messages are processed — system/user/tool messages
    are left untouched.
    """
    import re

    # Ordered from most specific to broadest — first match wins
    _LEDGER_MARKERS = [
        re.compile(r"<!-- DECOHERE:BEGIN -->.*", re.DOTALL),
        re.compile(r"^## Ledger Entries\b.*", re.MULTILINE | re.DOTALL),
        re.compile(r"\[INTERNAL CONTEXT \S+ DO NOT ECHO[^\]]*\].*", re.DOTALL),
        re.compile(
            r"\[Turn \d+\]\s*\n\s*(?:message_range:|tools:|files:|task:|ref_class:|"
            r"concepts_and_definitions:|narrative:|user_intent:|decisions:|"
            r"procedures:|insights:|\u21b3 improvements:).*",
            re.DOTALL,
        ),
    ]
    # Prepend canary-based detector if a token was provided
    if canary_token:
        _LEDGER_MARKERS.insert(
            0, re.compile(re.escape(f"[canary:{canary_token}]") + r".*", re.DOTALL),
        )

    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue

        # ── Canary detection: fast path with logging ──
        if canary_token and f"[canary:{canary_token}]" in content:
            logger.warning(
                "CANARY_DETECTED: LLM regurgitated ledger context "
                "(canary token found in assistant response). Stripping."
            )

        for pattern in _LEDGER_MARKERS:
            m = pattern.search(content)
            if m:
                content = content[:m.start()].strip()
                break

        if not content:
            msg["content"] = "[SYSTEM_ERROR: Your previous response consisted entirely of regurgitated internal ledger context, which is strictly forbidden. The system has intercepted and removed it to prevent infinite feedback loops. Please formulate a new response that actually executes the requested task or directly answers the user, without repeating the memory context.]"
        else:
            msg["content"] = content


def _extract_turn_numbers(messages: list[dict[str, Any]]) -> list[int]:
    """Extract turn numbers from L1/L2 context messages for audit trail."""
    import re
    numbers = set()
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            for m in re.finditer(r"\[Turn (\d+)\]", content):
                numbers.add(int(m.group(1)))
    return sorted(numbers)
