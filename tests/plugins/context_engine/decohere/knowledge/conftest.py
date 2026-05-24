"""Fixtures for knowledge module tests."""

import tempfile
from pathlib import Path

import pytest

from plugins.context_engine.decohere.knowledge.shared_store import SharedStore


@pytest.fixture
def shared_store():
    """Create a fresh SharedStore in a temp directory."""
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        store = SharedStore(home)
        yield store
        store.close()


@pytest.fixture
def populated_store(shared_store):
    """SharedStore with 4 pre-populated concepts from 2 sessions."""
    shared_store.add_concept("context window", "The maximum token capacity of an LLM input", "sess_a", 1)
    shared_store.add_concept("compression threshold", "Token budget at which compression triggers", "sess_a", 2)
    shared_store.add_concept("Codex /goal", "OpenAI Codex CLI autonomous task execution loop", "sess_b", 3)
    shared_store.add_concept("Ralph loop", "Verification loop pattern for self-checking output", "sess_b", 3)
    return shared_store
