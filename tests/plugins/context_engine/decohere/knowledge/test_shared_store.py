"""Tests for SharedStore — CRUD, dedup, search."""

from plugins.context_engine.decohere.knowledge.shared_store import SharedStore


class TestCRUD:
    def test_add_concept(self, shared_store):
        cid = shared_store.add_concept("test term", "A test definition", "sess_1", 1)
        assert cid is not None and cid > 0

    def test_dedup_same_term_session(self, shared_store):
        shared_store.add_concept("dup", "definition A", "sess_1", 1)
        cid2 = shared_store.add_concept("dup", "definition B", "sess_1", 2)
        assert cid2 is None  # Duplicate skipped

    def test_same_term_different_session(self, shared_store):
        shared_store.add_concept("dup", "def A", "sess_1", 1)
        cid2 = shared_store.add_concept("dup", "def B", "sess_2", 1)
        assert cid2 is not None  # Different session, allowed

    def test_remove_by_id(self, shared_store):
        cid = shared_store.add_concept("to_remove", "def", "sess_x", 1)
        assert shared_store.remove_by_id(cid) is True
        assert shared_store.remove_by_id(9999) is False

    def test_remove_by_source(self, shared_store):
        shared_store.add_concept("c1", "d1", "sess_a", 1)
        shared_store.add_concept("c2", "d2", "sess_a", 2)
        shared_store.add_concept("c3", "d3", "sess_b", 1)
        count = shared_store.remove_by_source("sess_a")
        assert count == 2
        assert shared_store.count() == 1

    def test_get_all(self, populated_store):
        all_c = populated_store.get_all()
        assert len(all_c) == 4

    def test_get_by_source(self, populated_store):
        sess_a = populated_store.get_by_source("sess_a")
        assert len(sess_a) == 2

    def test_update_definition(self, populated_store):
        all_c = populated_store.get_all()
        cid = all_c[0]["id"]
        assert populated_store.update_definition(cid, "Updated definition") is True
        updated = populated_store.get_all()
        found = [c for c in updated if c["id"] == cid][0]
        assert found["definition"] == "Updated definition"

    def test_update_nonexistent(self, shared_store):
        assert shared_store.update_definition(9999, "x") is False


class TestSearch:
    def test_search_finds(self, populated_store):
        results = populated_store.search_text("context window")
        assert len(results) >= 1
        assert results[0]["term"] == "context window"

    def test_search_no_match(self, populated_store):
        results = populated_store.search_text("xyznonexistent123")
        assert len(results) == 0


class TestSummary:
    def test_count(self, populated_store):
        assert populated_store.count() == 4

    def test_source_summary(self, populated_store):
        summary = populated_store.source_summary()
        assert len(summary) == 2
        sessions = {s["session"] for s in summary}
        assert sessions == {"sess_a", "sess_b"}
        counts = {s["session"]: s["count"] for s in summary}
        assert counts["sess_a"] == 2
        assert counts["sess_b"] == 2
