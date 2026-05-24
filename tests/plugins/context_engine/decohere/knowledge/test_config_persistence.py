"""Tests for DecohereUserConfig save/load roundtrip."""

import tempfile
from pathlib import Path

from plugins.context_engine.decohere.config import (
    DecohereUserConfig,
    load_decohere_config,
    save_decohere_config,
)


class TestConfigPersistence:
    def test_defaults(self):
        cfg = DecohereUserConfig()
        assert cfg.knowledge_injection is False
        assert cfg.retrieval_mode == "text"
        assert cfg.injection_max_concepts == 20

    def test_from_dict(self):
        d = {
            "knowledge_injection": True,
            "retrieval": {"mode": "semantic", "top_k": 5},
            "knowledge_sources": [{"session": "abc", "turns": [1, 2]}],
            "knowledge_exclude": ["test.*"],
            "injection": {"max_tokens_pct": 0.15, "max_concepts": 10},
        }
        cfg = DecohereUserConfig.from_dict(d)
        assert cfg.knowledge_injection is True
        assert cfg.retrieval_mode == "semantic"
        assert cfg.retrieval_top_k == 5
        assert len(cfg.knowledge_sources) == 1
        assert len(cfg.knowledge_exclude) == 1
        assert cfg.injection_max_tokens_pct == 0.15
        assert cfg.injection_max_concepts == 10

    def test_to_dict_roundtrip(self):
        cfg = DecohereUserConfig(
            knowledge_injection=True,
            knowledge_sources=[{"session": "x", "turns": [3]}],
            knowledge_exclude=["foo"],
        )
        d = cfg.to_dict()
        cfg2 = DecohereUserConfig.from_dict(d)
        assert cfg2.knowledge_injection is True
        assert cfg2.knowledge_sources == [{"session": "x", "turns": [3]}]
        assert cfg2.knowledge_exclude == ["foo"]

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / "config.yaml").write_text("existing_key: value\n")
            cfg = DecohereUserConfig(knowledge_injection=True, injection_max_concepts=15)
            save_decohere_config(home, cfg)
            loaded = load_decohere_config(home)
            assert loaded.knowledge_injection is True
            assert loaded.injection_max_concepts == 15
            # Should preserve existing keys
            content = (home / "config.yaml").read_text()
            assert "existing_key" in content
