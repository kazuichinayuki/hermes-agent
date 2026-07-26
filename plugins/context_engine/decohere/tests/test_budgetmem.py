"""Tests for BudgetMem context-aware budget-tier routing."""

import pytest
from plugins.context_engine.decohere.types import BudgetTier
from plugins.context_engine.decohere.context.classifier import classify_context_budget
from plugins.context_engine.decohere.context.query_focused import build_query_focused_context


def test_budget_classification():
    # Low turn count or empty query -> LOW
    assert classify_context_budget("", turn_count=5) == BudgetTier.LOW
    assert classify_context_budget("ok continue", turn_count=1) == BudgetTier.LOW

    # Short simple query -> LOW
    assert classify_context_budget("Sounds good", turn_count=5) == BudgetTier.LOW

    # Medium query (procedure/status/progress keywords or long content) -> MEDIUM
    assert classify_context_budget("What is the current status of the file changes?", turn_count=5) == BudgetTier.MEDIUM

    # High query (backward references: why, decision, earlier, history) -> HIGH
    assert classify_context_budget("Why did we make that architectural decision earlier?", turn_count=5) == BudgetTier.HIGH
    assert classify_context_budget("Can you recall the former error rationale?", turn_count=5) == BudgetTier.HIGH


def test_llm_precheck_override():
    def mock_llm_high(query):
        return "Budget requirement: HIGH"

    assert classify_context_budget("some query", turn_count=5, llm_fn=mock_llm_high) == BudgetTier.HIGH


def test_query_focused_context_building():
    mock_turns = [
        {
            "n": 1,
            "entry_skipped": False,
            "user_intent": "Setup SQLite WAL mode",
            "decisions_and_rationale": [
                {"decision": "Use WAL mode with 5000ms busy_timeout", "rationale": "Prevents database lock contention"}
            ],
            "insights_and_learnings": [
                {"category": "Database", "title": "WAL Concurrency", "content": "WAL mode allows concurrent readers"}
            ],
        },
        {
            "n": 2,
            "entry_skipped": False,
            "user_intent": "Update UI layout",
            "decisions_and_rationale": [
                {"decision": "Use Flexbox for main view", "rationale": "Cleaner alignment"}
            ],
        },
    ]

    # Query targeting WAL database decision
    result = build_query_focused_context(
        query="Why did we use WAL mode busy timeout for SQLite?",
        turns=mock_turns,
        max_turns=5,
    )

    assert len(result) == 1
    assert result[0]["role"] == "system"
    assert "Query-Focused Historical Memory" in result[0]["content"]
    assert "WAL mode with 5000ms busy_timeout" in result[0]["content"]
