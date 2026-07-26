from fastapi import APIRouter, Depends, Query

from app.core.security import get_current_user_id
from app.models.schemas import DiscoverResult, SearchResult
from app.services.discover import discover
from app.services.search import semantic_search

router = APIRouter(tags=["search"])


@router.get("/search", response_model=list[SearchResult])
async def search(
    q: str = Query(..., min_length=1),
    _user: str = Depends(get_current_user_id),
):
    return await semantic_search(q)


@router.get("/discover", response_model=list[DiscoverResult])
async def discover_query(
    q: str = Query(..., min_length=1),
    _user: str = Depends(get_current_user_id),
):
    return await discover(q)
