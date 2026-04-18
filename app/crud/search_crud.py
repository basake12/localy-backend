"""
app/crud/search_crud.py

Search query tracking for analytics and autocomplete corpus.
No blueprint violations found in original — presented here for completeness.

Confirms:
  - datetime.now(timezone.utc) used correctly (§16.4 compliant).
  - No LGA columns or parameters anywhere (§4 HARD RULE).
  - Geography WKTElement used for location storage when coordinates present.
"""

from typing import Optional, List, Dict
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func
from uuid import UUID
from geoalchemy2.elements import WKTElement

from app.crud.base_crud import CRUDBase
from app.models.search_model import SearchQuery


class CRUDSearchQuery(CRUDBase[SearchQuery, None, None]):

    def record_search(
        self,
        db: Session,
        *,
        user_id: Optional[UUID],
        query: str,
        category: Optional[str],
        results_count: int,
        location_lat: Optional[float] = None,
        location_lng: Optional[float] = None,
        location_name: Optional[str] = None,
        filters: Optional[Dict] = None,
        clicked: Optional[str] = None,
    ) -> SearchQuery:
        """
        Record a search query for analytics and autocomplete corpus.
        location stored as PostGIS POINT for potential geo-aware trending features.
        """
        location = None
        if location_lat and location_lng:
            location = WKTElement(
                f"POINT({location_lng} {location_lat})", srid=4326
            )

        search = SearchQuery(
            user_id=user_id,
            query=query.lower().strip(),
            category=category,
            results_count=results_count,
            location=location,
            location_name=location_name,
            filters=filters or {},
            clicked=clicked,
        )
        db.add(search)
        db.flush()  # caller commits
        return search

    def get_autocomplete_suggestions(
        self,
        db: Session,
        *,
        query_prefix: str,
        category: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict]:
        """
        Returns popular searches matching the given prefix.
        Groups by query text, counts occurrences, orders by frequency DESC.
        Called only on Redis MISS — results cached by search_service.get_autocomplete().
        """
        q = (
            db.query(
                SearchQuery.query,
                SearchQuery.category,
                func.count(SearchQuery.id).label("count"),
            )
            .filter(SearchQuery.query.like(f"{query_prefix.lower()}%"))
        )

        if category:
            q = q.filter(SearchQuery.category == category)

        results = (
            q.group_by(SearchQuery.query, SearchQuery.category)
            .order_by(func.count(SearchQuery.id).desc())
            .limit(limit)
            .all()
        )

        return [
            {"text": r.query, "category": r.category, "count": r.count}
            for r in results
        ]

    def get_popular_searches(
        self,
        db: Session,
        *,
        category: Optional[str] = None,
        days: int = 7,
        limit: int = 20,
    ) -> List[Dict]:
        """
        Returns most popular searches in the last N days.
        Blueprint §7.1: "trending searches from the last 7 days."
        Called only on Redis MISS — results cached by search_service.get_popular_searches().
        Blueprint §16.4: datetime.now(timezone.utc) — correctly used here.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        q = (
            db.query(
                SearchQuery.query,
                SearchQuery.category,
                func.count(SearchQuery.id).label("count"),
            )
            .filter(SearchQuery.created_at >= cutoff)
        )

        if category:
            q = q.filter(SearchQuery.category == category)

        results = (
            q.group_by(SearchQuery.query, SearchQuery.category)
            .order_by(func.count(SearchQuery.id).desc())
            .limit(limit)
            .all()
        )

        return [
            {"text": r.query, "category": r.category, "count": r.count}
            for r in results
        ]


search_query_crud = CRUDSearchQuery(SearchQuery)