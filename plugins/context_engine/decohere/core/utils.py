"""Pure utility functions. Zero dependencies, zero side-effects."""

import time


def elapsed_ms(t0: float) -> float:
    """Return elapsed milliseconds since t0."""
    return (time.monotonic() - t0) * 1000


def ensure_entry(mapping: dict, key: str, factory):
    """Get or create a dict entry. Returns (value, created_new)."""
    if key in mapping:
        return mapping[key], False
    value = factory()
    mapping[key] = value
    return value, True
