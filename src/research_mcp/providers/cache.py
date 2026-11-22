"""In-memory TTL cache for provider searches.

The MCP server is long-lived, so memoising identical searches for a few minutes
keeps repeat tool calls off OpenAlex/Semantic Scholar/Crossref rate limits. The
cache is best-effort: expired entries are dropped on read, and setting
`RESEARCH_MCP_SEARCH_CACHE_TTL=0` disables it entirely.
"""

import os
import threading
import time
from typing import Any

DEFAULT_TTL_SECONDS = 900.0
DEFAULT_MAX_ENTRIES = 128
TTL_ENV = "RESEARCH_MCP_SEARCH_CACHE_TTL"


def ttl_seconds() -> float:
    raw = os.environ.get(TTL_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_TTL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_TTL_SECONDS


class SearchCache:
    """Insertion-ordered, thread-safe cache with per-entry expiry."""

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._entries: dict[Any, tuple[Any, float]] = {}
        self._max_entries = max_entries
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any | None:
        ttl = ttl_seconds()
        if ttl <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at <= now:
                self._entries.pop(key, None)
                return None
            return value

    def set(self, key: Any, value: Any) -> None:
        ttl = ttl_seconds()
        if ttl <= 0:
            return
        with self._lock:
            if key not in self._entries and len(self._entries) >= self._max_entries:
                oldest = next(iter(self._entries))
                self._entries.pop(oldest, None)
            self._entries[key] = (value, time.monotonic() + ttl)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
