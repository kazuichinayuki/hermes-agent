"""Concept retrieval for shared knowledge store.

Two modes:
  retrieve_text()    — FTS5, zero-cost baseline, works today
  retrieve_semantic() — embedding-based, model-agnostic (future)
"""

from __future__ import annotations

from typing import Any, Callable

from .shared_store import SharedStore


def retrieve_text(
    store: SharedStore,
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """FTS5-based concept retrieval.  Zero external dependencies.

    Returns concepts ranked by FTS5 relevance to the query string.
    """
    return store.search_text(query, limit=limit)


def retrieve_semantic(
    store: SharedStore,
    query: str,
    embed_fn: Callable[[str], list[float]],
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Embedding-based concept retrieval.  Model-agnostic.

    embed_fn maps a string to a float vector.  Different models
    (Gemini embedding, OpenAI embedding, CLIP for cross-modal)
    are all supported by swapping embed_fn — no changes to this
    function or to SharedStore.

    NOT YET IMPLEMENTED — raises NotImplementedError.
    """
    raise NotImplementedError(
        "Semantic retrieval requires an embedding model. "
        "Configure retrieval.mode to 'text' for the FTS5 baseline, "
        "or provide an embed_fn implementation."
    )
