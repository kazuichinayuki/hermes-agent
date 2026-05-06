"""All defaults live here once. Nowhere else.

Single source of truth for the decohere plugin.
Read at init time, never mutated. Frozen dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeriverConfig:
    """Immutable derivation configuration.

    All defaults defined here. No inline defaults anywhere else.
    """

    model: str = "openai/gpt-5.4-mini"
    provider: str = "openrouter"
    temperature: float = 0.1
    max_tokens: int = 1000
    timeout: float = 5.0
    max_turns: int = 20

    @classmethod
    def from_aux_config(cls, aux: dict | None) -> "DeriverConfig":
        """Build config from auxiliary config block.

        Reads from ``auxiliary.compression`` in config.yaml.
        Falls back to DeriverConfig defaults for every missing key.
        """
        if aux is None:
            return cls()

        comp = aux.get("compression", {}) or {}
        ts = comp.get("decohere", {}) or {}

        return cls(
            model=comp.get("model", cls.model),
            provider=comp.get("provider", cls.provider),
            temperature=ts.get("temperature", cls.temperature),
            max_tokens=ts.get("max_tokens", cls.max_tokens),
            timeout=ts.get("timeout", cls.timeout),
            max_turns=ts.get("max_turns", cls.max_turns),
        )
