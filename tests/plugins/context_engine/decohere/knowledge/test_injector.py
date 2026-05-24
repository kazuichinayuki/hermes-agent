"""Tests for injector — message building and filtering."""

from plugins.context_engine.decohere.knowledge.injector import build_injection_message


class TestInjectionDisabled:
    def test_disabled_returns_none(self, populated_store):
        msg = build_injection_message(
            populated_store, knowledge_injection=False,
            knowledge_sources=[], knowledge_exclude=[],
        )
        assert msg is None

    def test_empty_store_returns_none(self, shared_store):
        msg = build_injection_message(
            shared_store, knowledge_injection=True,
            knowledge_sources=[], knowledge_exclude=[],
        )
        assert msg is None


class TestInjectionEnabled:
    def test_builds_message(self, populated_store):
        msg = build_injection_message(
            populated_store, knowledge_injection=True,
            knowledge_sources=[], knowledge_exclude=[],
        )
        assert msg is not None
        assert msg["role"] == "system"
        assert msg["name"] == "shared_knowledge"
        assert "Shared Knowledge" in msg["content"]
        assert "context window" in msg["content"]

    def test_source_filter(self, populated_store):
        msg = build_injection_message(
            populated_store, knowledge_injection=True,
            knowledge_sources=[{"session": "sess_a", "turns": []}],
            knowledge_exclude=[],
        )
        assert "context window" in msg["content"]
        assert "compression threshold" in msg["content"]
        assert "Codex /goal" not in msg["content"]

    def test_turn_filter(self, populated_store):
        msg = build_injection_message(
            populated_store, knowledge_injection=True,
            knowledge_sources=[{"session": "sess_b", "turns": [3]}],
            knowledge_exclude=[],
        )
        assert "Codex /goal" in msg["content"]
        assert "context window" not in msg["content"]

    def test_exclude_filter(self, populated_store):
        msg = build_injection_message(
            populated_store, knowledge_injection=True,
            knowledge_sources=[],
            knowledge_exclude=["context window", "Codex.*"],
        )
        assert "context window" not in msg["content"]
        assert "Codex /goal" not in msg["content"]
        # These should still be present
        assert "compression" in msg["content"] or "Ralph loop" in msg["content"]

    def test_max_concepts_cap(self, populated_store):
        msg = build_injection_message(
            populated_store, knowledge_injection=True,
            knowledge_sources=[], knowledge_exclude=[],
            max_concepts=2,
        )
        count = msg["content"].count("• **")
        assert count <= 2
