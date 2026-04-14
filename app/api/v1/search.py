"""
app/api/v1/search.py — mounted at /search by router.py

FIXES APPLIED:

1. DOUBLE PATH PREFIX REMOVED.
   The original declared endpoint paths as "/search", "/search/autocomplete",
   "/search/popular". Because router.py mounts this at prefix="/search",
   the full resolved paths became:
     /api/v1/search/search             (Flutter calls /api/v1/search)
     /api/v1/search/search/autocomplete (Flutter calls /api/v1/search/suggestions)
     /api/v1/search/search/popular      (Flutter calls /api/v1/search/trending)
   Every search call from Flutter was a permanent 404.

   Fixed by removing the /search prefix from each endpoint path —
   the prefix lives only in router.py.

2. ENDPOINT PATHS ALIGNED WITH FLUTTER api_endpoints.dart.
   Flutter constants:
     universalSearch   = '/search'              → POST ""        on this router
     searchSuggestions = '/search/suggestions'  → GET  "/suggestions"
     trendingSearches  = '/search/trending'     → GET  "/trending"

3. async def → def (sync SQLAlchemy Session — same fix as all other routers).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.dependencies import get_current_user_optional
from app.models.user_model import User
from app.schemas.search_schema import (
    SearchRequest, SearchResponse,
    AutocompleteResponse,
    PopularSearchesResponse,
)
from app.services.search_service import search_service


router = APIRouter()


# ===========================================================================
# UNIFIED SEARCH  →  POST /api/v1/search
# Flutter: ApiEndpoints.universalSearch = '/search'
# ===========================================================================

@router.post(
    "",                                       # [FIX] was "/search" → double prefix
    response_model=SearchResponse,
    summary="Unified search across all entities",
)
def search(                                   # [FIX] async def → def
    body: SearchRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Search across hotels, products, restaurants, services, properties,
    doctors, events. Pass `lga_id` to enforce location-strict results.
    Returns unified SearchResultItem objects ranked by subscription tier,
    then rating.
    """
    return search_service.search(db, request=body, user_id=user.id if user else None)


# ===========================================================================
# SUGGESTIONS  →  GET /api/v1/search/suggestions
# Flutter: ApiEndpoints.searchSuggestions = '/search/suggestions'
# ===========================================================================

@router.get(
    "/suggestions",                           # [FIX] was "/search/autocomplete"
    response_model=AutocompleteResponse,
    summary="Autocomplete suggestions",
)
def get_suggestions(                          # [FIX] async def → def
    q: str = Query(..., min_length=1, max_length=100, description="Search query prefix"),
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """Returns autocomplete suggestions based on past searches."""
    return search_service.get_autocomplete(db, query=q, category=category, limit=limit)


# ===========================================================================
# TRENDING  →  GET /api/v1/search/trending
# Flutter: ApiEndpoints.trendingSearches = '/search/trending'
# ===========================================================================

@router.get(
    "/trending",                              # [FIX] was "/search/popular"
    response_model=PopularSearchesResponse,
    summary="Trending searches",
)
def get_trending_searches(                    # [FIX] async def → def
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Returns most popular searches from the last 7 days."""
    return search_service.get_popular_searches(db, category=category, limit=limit)