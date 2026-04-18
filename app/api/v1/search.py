"""
app/api/v1/search.py — mounted at /search by router.py

Blueprint §7.1: unified search bar, autocomplete, trending.
Blueprint §4  : NO lga_id parameter or reference anywhere (HARD RULE).
Blueprint §4.1: radius_km default 5 km, adjustable 1–50 km.
Blueprint §15 : POST /search, GET /search/suggestions, GET /search/trending.

FIXES vs previous version:
  1. DOUBLE PATH PREFIX REMOVED.
     Original declared endpoints as "/search", "/search/autocomplete",
     "/search/popular". Since router.py mounts this at prefix="/search",
     the resolved paths became /api/v1/search/search, /api/v1/search/search/autocomplete
     etc. — every call was a 404. Fixed by removing /search prefix from each path.

  2. ENDPOINT PATHS ALIGNED WITH FLUTTER api_endpoints.dart:
     universalSearch   = '/search'              → POST  ""             (this router)
     searchSuggestions = '/search/suggestions'  → GET   "/suggestions"
     trendingSearches  = '/search/trending'     → GET   "/trending"

  3. lga_id REMOVED from all docstrings.
     Blueprint §4 HARD RULE: no LGA column or parameter anywhere in the codebase.
     Previous router docstring said "Pass lga_id to enforce location-strict results."

  4. Suggestions endpoint now accepts lat/lng for nearby trending context.
     Blueprint §7.1: "nearby trending results" as part of autocomplete.

  5. sync def used (sync SQLAlchemy Session).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.dependencies import get_current_user_optional
from app.models.user_model import User
from app.schemas.search_schema import (
    SearchRequest,
    SearchResponse,
    AutocompleteResponse,
    PopularSearchesResponse,
)
from app.services.search_service import search_service

router = APIRouter()


# ── Unified Search  →  POST /api/v1/search ────────────────────────────────────

@router.post(
    "",                                       # FIX: was "/search" → double prefix
    response_model=SearchResponse,
    summary="Unified search across all seven Localy modules",
)
def search(
    body: SearchRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Search across hotels, products, food, services, properties, health, events.

    Supply location_lat + location_lng for radius-filtered results.
    Default radius: 5 km (adjustable 1–50 km via radius_km). Blueprint §4.1.

    Results ranked by: subscription tier → profile completeness →
    weighted rating → distance. Blueprint §7.2.

    Radius-only discovery — no LGA filtering exists on this platform.
    Blueprint §4 HARD RULE.
    """
    return search_service.search(
        db,
        request=body,
        user_id=user.id if user else None,
    )


# ── Suggestions  →  GET /api/v1/search/suggestions ───────────────────────────

@router.get(
    "/suggestions",                           # FIX: was "/search/autocomplete"
    response_model=AutocompleteResponse,
    summary="Autocomplete suggestions (Redis-cached, TTL=300s)",
)
def get_suggestions(
    q: str = Query(..., min_length=1, max_length=100, description="Search query prefix"),
    category: Optional[str] = Query(None, description="Filter by category"),
    lat: Optional[float] = Query(None, description="User latitude — for nearby trending"),
    lng: Optional[float] = Query(None, description="User longitude — for nearby trending"),
    limit: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """
    Returns autocomplete suggestions based on past searches.
    Blueprint §7.1: "Auto-suggest — partial keywords, recent searches, nearby trending."
    Blueprint §16.3: served from Redis (key: search_suggest:{hash}, TTL=300s).
    Radius-only — no LGA parameter. Blueprint §4 HARD RULE.
    """
    return search_service.get_autocomplete(
        db,
        query=q,
        category=category,
        limit=limit,
        lat=lat,
        lng=lng,
    )


# ── Trending  →  GET /api/v1/search/trending ─────────────────────────────────

@router.get(
    "/trending",                              # FIX: was "/search/popular"
    response_model=PopularSearchesResponse,
    summary="Trending searches (last 7 days, Redis-cached)",
)
def get_trending_searches(
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """
    Returns most popular searches from the last 7 days.
    Blueprint §7.1. Redis-cached with TTL=300s. Blueprint §16.3.
    """
    return search_service.get_popular_searches(
        db,
        category=category,
        limit=limit,
    )