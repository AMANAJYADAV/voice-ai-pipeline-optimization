"""Unit tests for the caching layer and API endpoints."""

import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import cache_service
from api_router import router
from cache_service import cache_size, clear_knowledge_cache, search_knowledge


@pytest.fixture(autouse=True)
def reset_cache():
    clear_knowledge_cache()
    yield
    clear_knowledge_cache()


def make_db(rows):
    db = MagicMock()
    db.rpc.return_value.execute.return_value = MagicMock(data=rows)
    return db


ROW = {
    "id": "11111111-1111-1111-1111-111111111111",
    "title": "System Architecture Overview",
    "category": "engineering",
    "content": "Core components.",
    "access_level": "PUBLIC",
}


def test_cache_hit_bypasses_database():
    db = make_db([ROW])
    first = search_knowledge(db, "architecture", "engineering")
    second = search_knowledge(db, "architecture", "engineering")
    assert first == second and len(first) == 1
    assert db.rpc.return_value.execute.call_count == 1


def test_query_normalization():
    db = make_db([ROW])
    search_knowledge(db, "Pricing Model")
    search_knowledge(db, "   pricing model   ")
    assert db.rpc.return_value.execute.call_count == 1


def test_empty_query_and_missing_data():
    db = make_db(None)
    assert search_knowledge(db, "") == []


def test_internal_rows_filtered():
    db = make_db([ROW, {**ROW, "id": "2", "access_level": "INTERNAL"}])
    assert len(search_knowledge(db, "x")) == 1


def test_returned_rows_are_copies():
    db = make_db([ROW])
    search_knowledge(db, "x")[0]["title"] = "mutated"
    assert search_knowledge(db, "x")[0]["title"] == ROW["title"]


def test_ttl_expiry(monkeypatch):
    db = make_db([ROW])
    monkeypatch.setattr(cache_service, "CACHE_TTL_SECONDS", 0.01)
    search_knowledge(db, "x")
    time.sleep(0.03)
    search_knowledge(db, "x")
    assert db.rpc.return_value.execute.call_count == 2


def test_lru_eviction(monkeypatch):
    db = make_db([ROW])
    monkeypatch.setattr(cache_service, "MAX_CACHE_ENTRIES", 2)
    search_knowledge(db, "a")
    search_knowledge(db, "b")
    search_knowledge(db, "a")  # "a" becomes most recently used
    search_knowledge(db, "c")  # evicts "b"
    assert cache_size() == 2
    calls = db.rpc.return_value.execute.call_count
    search_knowledge(db, "a")  # still cached
    assert db.rpc.return_value.execute.call_count == calls
    search_knowledge(db, "b")  # was evicted
    assert db.rpc.return_value.execute.call_count == calls + 1


def test_invalidation_forces_refetch():
    db = make_db([])
    search_knowledge(db, "test")
    clear_knowledge_cache()
    search_knowledge(db, "test")
    assert db.rpc.return_value.execute.call_count == 2


def test_database_error_returns_empty():
    db = MagicMock()
    db.rpc.side_effect = RuntimeError("boom")
    assert search_knowledge(db, "x") == []


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_search_endpoint(client):
    assert client.get("/api/v1/knowledge/search", params={"q": "hello"}).status_code == 200


def test_create_endpoint_invalidates_cache(client):
    search_knowledge(make_db([ROW]), "x")
    assert cache_size() == 1
    payload = {"title": "Title", "category": "cat", "content": "Some content"}
    assert client.post("/api/v1/knowledge/", json=payload).status_code == 201
    assert cache_size() == 0


def test_search_endpoint_error_returns_500(client, monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("api_router.get_db", boom)
    assert client.get("/api/v1/knowledge/search", params={"q": "hello"}).status_code == 500


def test_create_endpoint_error_keeps_cache(client, monkeypatch):
    search_knowledge(make_db([ROW]), "x")

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("api_router.get_db", boom)
    payload = {"title": "Title", "category": "cat", "content": "Some content"}
    assert client.post("/api/v1/knowledge/", json=payload).status_code == 500
    assert cache_size() == 1  # failed write must not flush the cache
