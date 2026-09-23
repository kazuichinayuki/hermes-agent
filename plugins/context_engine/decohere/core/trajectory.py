"""Trajectory generation and agent experience compilation for Decohere.

Transforms runtime conversation turns into:
1. Standard ShareGPT-format trajectories (with <think> tags, tool calls, and results).
2. Typed decision points (choice, score, noul) to compile agent experience
   into training datasets for fast System 1 decision substrates (e.g. Laya).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_INTERNAL_SYSTEM_NAMES = frozenset({
    "ledger_l1", "turn_context", "turn_index", "shared_state", "shared_knowledge"
})

# Tool Domain Ontology for Coarse-to-Fine Hierarchical Choice (overcoming 50+ options limit)
_TOOL_DOMAIN_MAP: Dict[str, str] = {
    # Filesystem & Code edit
    "view_file": "filesystem",
    "write_to_file": "filesystem",
    "replace_file_content": "filesystem",
    "list_dir": "filesystem",
    "find_by_name": "filesystem",
    "grep_search": "filesystem",
    "read_file": "filesystem",
    # Execution & System management
    "run_command": "execution",
    "manage_task": "execution",
    "schedule": "execution",
    # Search & Memory
    "search_web": "search",
    "read_url_content": "search",
    "recall_context": "memory",
    # Coordination & Subagents
    "invoke_subagent": "coordination",
    "define_subagent": "coordination",
    "manage_subagents": "coordination",
    "send_message": "coordination",
    "ask_question": "coordination",
    "call_mcp_tool": "mcp",
}

_DOMAIN_CRITERIA: Dict[str, str] = {
    "filesystem": "file inspection, search, creation, and code refactoring",
    "execution": "running bash/terminal commands, task management, or timers",
    "search": "external web search and online documentation reading",
    "memory": "querying ledger context and past conversation decisions",
    "coordination": "agent delegation, messaging, or asking user questions",
    "mcp": "invoking registered Model Context Protocol extensions",
    "reply": "answering user directly without invoking external tools",
}


def clean_messages_for_trajectory(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip internal Decohere-injected ledger and state messages so the trajectory
    reflects genuine user-agent dialogue and actions."""
    cleaned = []
    for msg in messages:
        if not msg or not isinstance(msg, dict):
            continue
        if msg.get("_decohere_injected"):
            continue
        name = msg.get("name")
        if name in _INTERNAL_SYSTEM_NAMES:
            continue
        cleaned.append(dict(msg))
    return cleaned


def _convert_scratchpad_to_think(content: str) -> str:
    """Convert <REASONING_SCRATCHPAD> tags to <think> tags."""
    if not content:
        return ""
    if "<REASONING_SCRATCHPAD>" in content:
        content = content.replace("<REASONING_SCRATCHPAD>", "<think>").replace("</REASONING_SCRATCHPAD>", "</think>")
    return content


def _format_tool_calls_value(tool_calls: list) -> str:
    """Format tool_calls array into standard Hermes/ShareGPT <tool_call> tags."""
    parts = []
    for tc in tool_calls:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        name = fn.get("name", tc.get("name", "unknown"))
        args = fn.get("arguments", tc.get("arguments", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                pass
        payload = json.dumps({"name": name, "arguments": args}, ensure_ascii=False)
        parts.append(f"<tool_call>\n{payload}\n</tool_call>")
    return "\n".join(parts)


def build_trajectory_record(
    session_id: str,
    turn_n: int,
    messages: List[Dict[str, Any]],
    model: str = "",
    completed: bool = True,
    usage: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convert internal messages into a standard ShareGPT / Hermes trajectory record."""
    cleaned = clean_messages_for_trajectory(messages)
    conversations: List[Dict[str, str]] = []

    i = 0
    while i < len(cleaned):
        msg = cleaned[i]
        role = msg.get("role")
        content = msg.get("content") or ""

        if role == "system":
            conversations.append({"from": "system", "value": str(content)})
        elif role == "user":
            conversations.append({"from": "human", "value": str(content)})
        elif role == "assistant":
            reasoning = msg.get("reasoning_content") or ""
            text = str(content)
            if reasoning and "<think>" not in text:
                text = f"<think>\n{reasoning}\n</think>\n{text}"
            else:
                text = _convert_scratchpad_to_think(text)

            tool_calls = msg.get("tool_calls")
            if tool_calls and isinstance(tool_calls, list):
                tool_text = _format_tool_calls_value(tool_calls)
                full_val = f"{text.strip()}\n{tool_text}".strip() if text.strip() else tool_text
                conversations.append({"from": "gpt", "value": full_val})
            else:
                conversations.append({"from": "gpt", "value": text.strip()})
        elif role == "tool":
            tool_name = msg.get("name") or msg.get("tool_name") or "tool"
            conversations.append({"from": "tool", "value": f"[{tool_name}] {str(content)}"})

        i += 1

    return {
        "id": f"traj_{session_id}_{turn_n}",
        "session_id": session_id,
        "turn_n": turn_n,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "completed": completed,
        "conversations": conversations,
        "usage": usage or {},
        "metadata": metadata or {},
    }


def extract_typed_decisions(
    messages: List[Dict[str, Any]],
    session_id: str,
    turn_n: int,
    user_query: str = "",
) -> List[Dict[str, Any]]:
    """Compile agent experiences within a turn into typed decisions (choice, score, noul).

    This produces domain dataset samples suitable for training / fine-tuning fast
    non-autoregressive decision substrates such as Laya or decision trees:
    1. 'choice': Tool selection given current state (e.g. tool name vs 'direct_reply').
    2. 'noul': Goal termination / fulfillment verification (stop vs continue).
    3. 'score': Multi-step complexity / budget estimation.
    """
    cleaned = clean_messages_for_trajectory(messages)
    decision_points: List[Dict[str, Any]] = []

    if not user_query:
        for msg in cleaned:
            if msg.get("role") == "user":
                user_query = str(msg.get("content") or "")[:500]

    # Analyze assistant actions and tool sequences
    assistant_indices = [idx for idx, m in enumerate(cleaned) if m.get("role") == "assistant"]
    total_assistant_turns = len(assistant_indices)

    for step_idx, a_idx in enumerate(assistant_indices):
        assistant_msg = cleaned[a_idx]
        tool_calls = assistant_msg.get("tool_calls") or []
        is_final_step = (step_idx == total_assistant_turns - 1)

        # Context preceding this assistant decision (up to 3 prior messages)
        start_ctx = max(0, a_idx - 3)
        history_snippet = [
            f"{m.get('role')}: {str(m.get('content') or '')[:120]}"
            for m in cleaned[start_ctx:a_idx]
        ]

        state = {
            "query": user_query,
            "step_index": step_idx,
            "preceding_context": history_snippet,
        }

        # Check tool execution feedback for Prediction Error / Surprise
        has_tool_error = False
        tool_feedback_snippet = ""
        for next_idx in range(a_idx + 1, min(len(cleaned), a_idx + 4)):
            next_msg = cleaned[next_idx]
            if next_msg.get("role") == "tool":
                c = str(next_msg.get("content") or "")
                tool_feedback_snippet = c[:200]
                c_lower = c.lower()
                if any(err_kw in c_lower for err_kw in ("error", "failed", "exception", "traceback", "not found", "code 1", "exit code 1")):
                    has_tool_error = True
                    break

        # 1. Tool selection decision ('choice')
        if tool_calls and isinstance(tool_calls, list):
            for tc in tool_calls:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                tool_name = fn.get("name", tc.get("name", "unknown"))
                args = fn.get("arguments", {})
                domain = _TOOL_DOMAIN_MAP.get(tool_name, "general")
                decision_points.append({
                    "session_id": session_id,
                    "turn_n": turn_n,
                    "decision_type": "choice",
                    "state": state,
                    "instructions": "Select the appropriate tool to execute for this step.",
                    "decision": {
                        "tool": tool_name,
                        "domain": domain,
                        "arguments": args,
                    },
                    "target_label": tool_name,
                    "metadata": {
                        "step_index": step_idx,
                        "has_more_tools": len(tool_calls) > 1,
                        "hierarchical": {
                            "domain": domain,
                            "sub_tool": tool_name,
                            "domain_criteria": _DOMAIN_CRITERIA.get(domain, ""),
                        },
                        "prediction_error": {
                            "has_tool_error": has_tool_error,
                            "worth_rethinking": has_tool_error,
                            "feedback": tool_feedback_snippet,
                        },
                    },
                })
        else:
            domain = "reply"
            decision_points.append({
                "session_id": session_id,
                "turn_n": turn_n,
                "decision_type": "choice",
                "state": state,
                "instructions": "Select the appropriate tool to execute for this step.",
                "decision": {"tool": "direct_reply", "domain": "reply"},
                "target_label": "direct_reply",
                "metadata": {
                    "step_index": step_idx,
                    "hierarchical": {
                        "domain": "reply",
                        "sub_tool": "direct_reply",
                        "domain_criteria": _DOMAIN_CRITERIA.get("reply", ""),
                    },
                },
            })

        # 2. Goal completion decision ('noul': True if goal met, False if further tool calls needed)
        decision_points.append({
            "session_id": session_id,
            "turn_n": turn_n,
            "decision_type": "noul",
            "state": state,
            "instructions": "Has the user's intent been completely fulfilled without further actions?",
            "decision": is_final_step and not tool_calls,
            "target_label": "complete" if (is_final_step and not tool_calls) else "needs_action",
            "metadata": {
                "step_index": step_idx,
                "is_final_step": is_final_step,
                "worth_rethinking": has_tool_error,
            },
        })

        # 3. Reflection trigger decision ('reflect_trigger': Fast-Path vs LLM Reflection)
        if tool_calls and isinstance(tool_calls, list):
            for tc in tool_calls:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                tname = fn.get("name", tc.get("name", "unknown"))
                targs = fn.get("arguments", {})
                from .reflect_trigger import ReflectTrigger
                rt = ReflectTrigger()
                decision = rt.evaluate(tname, targs, tool_feedback_snippet)
                decision_points.append({
                    "session_id": session_id,
                    "turn_n": turn_n,
                    "decision_type": "reflect_trigger",
                    "state": state,
                    "instructions": "Evaluate observation: fast_path (no reflection needed) or trigger_reflection (wakes LLM).",
                    "decision": {
                        "should_reflect": decision.should_reflect,
                        "trigger_score": decision.trigger_score,
                        "reasons": decision.reasons,
                        "entropy_estimate": decision.entropy_estimate,
                    },
                    "target_label": "trigger_reflection" if decision.should_reflect else "fast_path",
                    "metadata": {
                        "step_index": step_idx,
                        "tool_name": tname,
                        "diagnostic": decision.diagnostic_summary,
                    },
                })

    # 3. Complexity score ('score': 0=direct answer, 1=single tool, 2=multi-step workflow)
    tool_count = sum(len(cleaned[idx].get("tool_calls") or []) for idx in assistant_indices)
    complexity_tier = 0 if tool_count == 0 else (1 if tool_count == 1 else 2)
    decision_points.append({
        "session_id": session_id,
        "turn_n": turn_n,
        "decision_type": "score",
        "state": {"query": user_query},
        "instructions": "Rate task complexity: 0 (direct answer), 1 (single tool), 2 (multi-step tool chain).",
        "decision": complexity_tier,
        "target_label": str(complexity_tier),
        "metadata": {"total_tool_calls": tool_count, "total_steps": total_assistant_turns},
    })

    return decision_points
