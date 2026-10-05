"""Bounded in-memory cache and knowledge retrieval service.

Provides a thread-safe cache with TTL expiration and true LRU eviction
in front of a PostgreSQL full-text search RPC, so repeated queries are served
from memory instead of a remote database round trip.
"""

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger(__name__)

CacheKey = tuple[str, Optional[str], int]
CacheValue = tuple[float, list[dict[str, Any]]]

CACHE_TTL_SECONDS: float = 300.0  # Time-to-live for each entry
MAX_CACHE_ENTRIES: int = 256      # Upper bound to protect process memory

_cache: "OrderedDict[CacheKey, CacheValue]" = OrderedDict()
_cache_lock = threading.Lock()


def _copy_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return shallow copies so callers cannot mutate cached data."""
    return [dict(row) for row in rows]


def search_knowledge(
    db_client: Any,
    query: str,
    category: Optional[str] = None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Search the knowledge base, serving from cache when possible.

    Args:
        db_client: Database client exposing ``rpc(name, params).execute()``.
        query: Free-text search terms.
        category: Optional category filter.
        limit: Maximum number of rows to return.

    Returns:
        A list of public-safe result rows. Returns an empty list on error.
    """
    try:
        norm_query = query.strip().lower() if query else ""
        norm_category = category.strip().lower() if category else None
        key: CacheKey = (norm_query, norm_category, limit)
        now = time.monotonic()

        with _cache_lock:
            cached = _cache.get(key)
            if cached is not None:
                if now - cached[0] < CACHE_TTL_SECONDS:
                    _cache.move_to_end(key)  # mark as most recently used
                    logger.debug("cache hit")
                    return _copy_rows(cached[1])
                del _cache[key]  # expired

        logger.debug("cache miss; calling database RPC")
        # The database call runs outside the lock so it never blocks other readers.
        response = db_client.rpc(
            "search_knowledge_v1",
            {
                "search_query": query.strip() if query else "",
                "category_filter": norm_category,
                "max_limit": limit,
            },
        ).execute()

        rows: list[dict[str, Any]] = []
        for row in response.data or []:
            if row.get("access_level") == "INTERNAL":
                continue  # never expose internal-only records
            rows.append(
                {
                    "id": str(row.get("id")),
                    "title": row.get("title"),
                    "category": row.get("category"),
                    "content": row.get("content"),
                    "access_level": row.get("access_level", "PUBLIC"),
                }
            )

        with _cache_lock:
            _cache[key] = (time.monotonic(), rows)
            _cache.move_to_end(key)
            while len(_cache) > MAX_CACHE_ENTRIES:
                _cache.popitem(last=False)  # evict least recently used

        return _copy_rows(rows)

    except Exception:
        logger.exception("Knowledge search failed")
        return []


def clear_knowledge_cache() -> None:
    """Flush all cached queries; call after any create/update/delete."""
    with _cache_lock:
        _cache.clear()
    logger.info("Knowledge cache invalidated")


def cache_size() -> int:
    """Return the current number of cached entries."""
    with _cache_lock:
        return len(_cache)
