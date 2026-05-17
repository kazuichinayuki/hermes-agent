"""All defaults live here once. Nowhere else.

Single source of truth for the decohere plugin.
Read at init time, never mutated. Frozen dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LedgerConfig:
    """Immutable posting configuration.

    All defaults defined here. No inline defaults anywhere else.
    """

    model: str = "openai/gpt-5.4-mini"
    provider: str = "openrouter"
    temperature: float = 0.1
    max_tokens: int | None = None
    timeout: float = 5.0
    max_turns: int = 20

    @classmethod
    def from_aux_config(cls, aux: dict | None, compression: dict | None = None) -> "LedgerConfig":
        """Build config from auxiliary + compression config blocks.

        Reads from ``auxiliary.compression`` in config.yaml for model/provider.
        Reads from top-level ``compression`` block for decohere-native mappings:
          - ``protect_last_n`` → ``max_turns`` (how many recent ledger entries
            to keep in context; default 20)

        ``auxiliary.compression.decohere.*`` overrides take precedence for
        ``max_turns``, ``temperature``, ``timeout``.

        ``max_tokens`` defaults to None (no cap) — the compression model
        generates as much detail as needed per ledger entry.  Set explicitly
        in ``auxiliary.compression.decohere.max_tokens`` to cap output.
        """
        if aux is None:
            return cls()

        comp = aux.get("compression", {}) or {}
        ts = comp.get("decohere", {}) or {}

        # Top-level compression block (for decohere-native mappings)
        top = compression or {}
        protect_last_n = top.get("protect_last_n")

        # max_turns: decohere-specific > protect_last_n > default (20)
        max_turns = ts.get("max_turns")
        if max_turns is None and protect_last_n is not None:
            max_turns = int(protect_last_n)

        return cls(
            model=comp.get("model", cls.model),
            provider=comp.get("provider", cls.provider),
            temperature=ts.get("temperature", cls.temperature),
            max_tokens=ts.get("max_tokens"),
            timeout=ts.get("timeout", cls.timeout),
            max_turns=max_turns if max_turns is not None else cls.max_turns,
        )

    @staticmethod
    def _read_config_static(config_path: Path) -> tuple[dict | None, dict | None]:
        """Read auxiliary + compression blocks from config.yaml. No instance needed."""
        import yaml
        try:
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("auxiliary", {}), cfg.get("compression", {})
        except Exception:
            return None, None


# ── User-facing knowledge config ──────────────────────────────────────

@dataclass
class DecohereUserConfig:
    """User-facing configuration for decohere knowledge management.

    Mutable — loaded from config.yaml at session start, modified by CLI
    commands, and saved back.
    """

    knowledge_injection: bool = False

    retrieval_mode: str = "text"             # "text" | "semantic"
    retrieval_semantic_model: str | None = None  # e.g. "gemini-embedding"
    retrieval_semantic_threshold: float = 0.75
    retrieval_top_k: int = 10

    knowledge_sources: list[dict] = field(default_factory=list)
    # [{"session": "abc123", "turns": [3, 5]}, ...]

    knowledge_exclude: list[str] = field(default_factory=list)
    # ["context window", "compression.*"]

    injection_max_tokens_pct: float = 0.10
    injection_max_concepts: int = 20

    @classmethod
    def from_dict(cls, d: dict | None) -> "DecohereUserConfig":
        """Build from config.yaml's 'decohere' block."""
        if not d or not isinstance(d, dict):
            return cls()

        retrieval = d.get("retrieval", {}) or {}
        injection = d.get("injection", {}) or {}

        return cls(
            knowledge_injection=bool(d.get("knowledge_injection", False)),
            retrieval_mode=retrieval.get("mode", "text"),
            retrieval_semantic_model=retrieval.get("semantic_model"),
            retrieval_semantic_threshold=float(retrieval.get("semantic_threshold", 0.75)),
            retrieval_top_k=int(retrieval.get("top_k", 10)),
            knowledge_sources=list(d.get("knowledge_sources", [])),
            knowledge_exclude=list(d.get("knowledge_exclude", [])),
            injection_max_tokens_pct=float(injection.get("max_tokens_pct", 0.10)),
            injection_max_concepts=int(injection.get("max_concepts", 20)),
        )

    def to_dict(self) -> dict:
        """Serialize for writing to config.yaml."""
        return {
            "knowledge_injection": self.knowledge_injection,
            "retrieval": {
                "mode": self.retrieval_mode,
                "semantic_model": self.retrieval_semantic_model,
                "semantic_threshold": self.retrieval_semantic_threshold,
                "top_k": self.retrieval_top_k,
            },
            "knowledge_sources": self.knowledge_sources,
            "knowledge_exclude": self.knowledge_exclude,
            "injection": {
                "max_tokens_pct": self.injection_max_tokens_pct,
                "max_concepts": self.injection_max_concepts,
            },
        }


def load_decohere_config(hermes_home: Path) -> DecohereUserConfig:
    """Read decohere user config from config.yaml."""
    import yaml

    config_path = hermes_home / "config.yaml"
    if not config_path.exists():
        return DecohereUserConfig()

    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        return DecohereUserConfig()

    return DecohereUserConfig.from_dict(cfg.get("decohere", {}))


def save_decohere_config(hermes_home: Path, config: DecohereUserConfig) -> None:
    """Write decohere user config back to config.yaml, preserving other keys."""
    import yaml

    config_path = hermes_home / "config.yaml"

    # Read existing config
    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
        except Exception:
            pass

    # Merge — only overwrite the 'decohere' block
    existing["decohere"] = config.to_dict()

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(existing, f, allow_unicode=True, default_flow_style=False)
