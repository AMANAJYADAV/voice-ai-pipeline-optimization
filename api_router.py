"""FastAPI router for low-latency knowledge retrieval and management."""

import asyncio
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from cache_service import clear_knowledge_cache, search_knowledge

router = APIRouter(prefix="/api/v1/knowledge", tags=["Knowledge Engine"])


class KnowledgeCreateRequest(BaseModel):
    """Payload for creating a knowledge item."""

    title: str = Field(..., min_length=2, max_length=200)
    category: str = Field(..., min_length=2, max_length=100)
    content: str = Field(..., min_length=5)
    keywords: Optional[str] = None
    access_level: str = Field(default="PUBLIC", pattern="^(PUBLIC|INTERNAL|RESTRICTED)$")
    priority: int = Field(default=100, ge=0, le=1000)


class KnowledgeResponse(BaseModel):
    """Public representation of a knowledge item."""

    id: UUID
    title: str
    category: str
    content: str
    access_level: str


def get_db() -> Any:
    """Return a database client.

    Placeholder returning an in-memory stub; replace with a real client
    (configured via environment variables) in production.
    """

    class _StubClient:
        def rpc(self, func_name: str, params: dict[str, Any]) -> "_StubClient":
            return self

        def execute(self) -> Any:
            return type("Response", (), {"data": []})()

        def table(self, table_name: str) -> "_StubClient":
            return self

        def insert(self, data: dict[str, Any]) -> "_StubClient":
            return self

    return _StubClient()


@router.get("/search", response_model=list[dict[str, Any]])
async def search_endpoint(
    q: str = Query(..., min_length=1, description="Search terms"),
    category: Optional[str] = Query(None, description="Optional category filter"),
    limit: int = Query(3, ge=1, le=20, description="Maximum results returned"),
) -> list[dict[str, Any]]:
    """Search the knowledge base without blocking the event loop.

    The synchronous database call is offloaded to a worker thread so
    concurrent sessions are not starved while a query is in flight.
    """
    try:
        return await asyncio.to_thread(
            search_knowledge,
            db_client=get_db(),
            query=q,
            category=category,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Knowledge retrieval failure",
        ) from exc


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_record_endpoint(payload: KnowledgeCreateRequest) -> dict[str, str]:
    """Create a knowledge item and invalidate the cache."""
    try:
        db = get_db()
        await asyncio.to_thread(
            lambda: db.table("knowledge_items").insert(payload.model_dump()).execute()
        )
        # Invalidate so live sessions never read stale data.
        clear_knowledge_cache()
        return {"status": "success", "message": "Record created and cache invalidated"}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create record",
        ) from exc
