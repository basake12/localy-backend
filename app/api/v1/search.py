"""
search_crud.py — /search/*

Unified search across all platform entities.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.dependencies import get_current_user_optional
from app.models.user_model import User
from app.schemas.search_schema import (
    SearchRequest, SearchResponse,
    AutocompleteRequest, AutocompleteResponse,
    PopularSearchesResponse,
)
from app.services.search_service import search_service


router = APIRouter()


# ===========================================================================
# UNIFIED SEARCH
# ===========================================================================

@router.post(
    "/search",
    response_model=SearchResponse,
    summary="Unified search across all entities",
)
async def search(
    body: SearchRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Search across hotels, products, restaurants, services, properties, doctors, events.
    Returns unified SearchResultItem objects.
    """
    result = search_service.search(db, request=body, user_id=user.id if user else None)
    return result


# ===========================================================================
# AUTOCOMPLETE
# ===========================================================================

@router.get(
    "/search/autocomplete",
    response_model=AutocompleteResponse,
    summary="Autocomplete suggestions",
)
async def autocomplete(
    q: str = Query(..., min_length=1, max_length=100, description="Search query prefix"),
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """
    Returns autocomplete suggestions based on past searches.
    """
    result = search_service.get_autocomplete(db, query=q, category=category, limit=limit)
    return result


# ===========================================================================
# POPULAR SEARCHES
# ===========================================================================

@router.get(
    "/search/popular",
    response_model=PopularSearchesResponse,
    summary="Popular searches",
)
async def popular_searches(
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """
    Returns most popular searches from the last 7 days.
    """
    result = search_service.get_popular_searches(db, category=category, limit=limit)
    return result