# Voice AI Low-Latency Knowledge Engine

A low-latency knowledge retrieval pipeline for an autonomous voice AI agent, built with FastAPI and PostgreSQL full-text search. It reduced the retrieval bottleneck from **~1.2 s to 5–20 ms (about a 98% reduction)**.

> This repository is a generalized, sanitized reference implementation based on work done during an internship. All proprietary logic, identifiers, and data have been removed or replaced with generic examples. The database client is a stub; no credentials or real endpoints are included.

## Why latency matters

In a live voice conversation, any pause while the agent looks up information is audible to the caller. Retrieval sits on the critical path of every turn, so a ~1.2 s lookup is a noticeable delay. The goal was to make retrieval fast enough to be effectively invisible.

## Key achievements

- **Deeply optimized retrieval pipeline.** Cut retrieval latency from ~1.2 s to 5–20 ms (98% reduction) for an autonomous voice agent.
- **Async bounded in-memory cache (TTL + LRU).** Repeated lookups are served from memory. A fixed entry cap and per-entry TTL keep memory bounded, and lookups run off the event loop (`asyncio.to_thread`) so FastAPI is not starved under high-concurrency sessions.
- **Refactored PL/pgSQL search procedure.** `websearch_to_tsquery` is computed once per call instead of per row, speeding up rank scans over a GIN-indexed generated `tsvector` column.
- **Real-time cache invalidation.** Any write flushes the cache so live sessions never receive stale facts. The caching layer and API paths are covered by an automated test suite (100% line coverage, see below).

## Architecture

```text
Voice agent
    |
    v  GET /api/v1/knowledge/search
FastAPI router --> asyncio.to_thread (keeps the event loop free)
    |
    v
Cache service
    |-- hit  --> return cached rows (in-memory)
    '-- miss --> PostgreSQL RPC search_knowledge_v1
                   |-- tsquery parsed once per call
                   |-- GIN-indexed fts scan
                   '-- ts_rank + priority ordering
                 --> populate cache (TTL, LRU-bounded)

POST /api/v1/knowledge --> write --> clear cache
```

## Design details

**Cache (`cache_service.py`)**
- Keys are normalized (trimmed, lowercased) so equivalent queries share an entry.
- `OrderedDict` provides true LRU eviction; entries also expire after a TTL.
- A lock guards cache state; the database call runs outside the lock so slow queries never block cache hits.
- Rows marked `INTERNAL` are filtered out before being cached or returned.
- Returned rows are copies, so callers cannot corrupt cached data.

**Search procedure (`schema.sql`)**
- `fts` is a stored generated `tsvector` over title, content, and keywords, indexed with GIN.
- The query is parsed once into a `tsquery` variable and reused for both filtering and ranking.
- Results are ordered by `ts_rank`, then a priority weight.

**API (`api_router.py`)**
- Blocking database calls are dispatched to a worker thread.
- Create endpoint invalidates the cache only after a successful write.

## Getting started

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
psql -d <your_database> -f schema.sql   # optional: create the schema
```

Mount `router` from `api_router.py` in a FastAPI app and replace the `get_db()` stub with your own database client, configured through environment variables.

## Tests

```bash
pytest test_knowledge_engine.py -v --cov=cache_service --cov=api_router --cov-report=term-missing
```

The suite covers cache hits, key normalization, TTL expiry, LRU eviction, internal-row filtering, invalidation, error handling, and both API endpoints.

## Project structure

```text
.
├── README.md
├── requirements.txt
├── schema.sql
├── cache_service.py
├── api_router.py
└── test_knowledge_engine.py
```

## Tech stack

Python, FastAPI, Pydantic, PostgreSQL (PL/pgSQL, full-text search, GIN indexes), pytest.
