"""Knowledge modules for decohere cross-session concept management.

shared_store  — text-only concept database (decohere_shared.db)
matcher       — text and semantic retrieval
injector      — build shared knowledge context messages
"""

from .shared_store import SharedStore
from .matcher import retrieve_text
from .injector import build_injection_message

__all__ = ["SharedStore", "retrieve_text", "build_injection_message"]
