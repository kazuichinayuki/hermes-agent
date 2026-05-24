"""Tests for matcher — text and semantic retrieval."""

import pytest
from plugins.context_engine.decohere.knowledge.matcher import retrieve_text, retrieve_semantic


class TestTextRetrieval:
    def test_retrieve_finds_concepts(self, populated_store):
        results = retrieve_text(populated_store, "context window", limit=5)
        assert len(results) >= 1
        assert results[0]["term"] == "context window"

    def test_retrieve_no_match(self, populated_store):
        results = retrieve_text(populated_store, "xyznonexistent")
        assert len(results) == 0

    def test_retrieve_respects_limit(self, populated_store):
        results = retrieve_text(populated_store, "loop", limit=1)
        assert len(results) <= 1


class TestSemanticRetrieval:
    def test_not_implemented(self, populated_store):
        with pytest.raises(NotImplementedError):
            retrieve_semantic(populated_store, "test", embed_fn=lambda x: [0.1, 0.2])
