"""
app/models/search_model.py

Search query analytics model.
No blueprint violations found in original — presented for completeness.

Confirms:
  - No LGA columns or fields (Blueprint §4 HARD RULE).
  - Geography(POINT, 4326) for location context (Blueprint §4.3 pattern).
  - JSONB filters column for search filter metadata.
"""

from sqlalchemy import Column, String, Integer, ForeignKey, Index
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geography

from app.models.base_model import BaseModel


class SearchQuery(BaseModel):
    """
    Tracks all search queries for:
    - Analytics (what users search for, where)
    - Autocomplete suggestions corpus
    - Popular / trending searches

    Blueprint §7.1: autocomplete cache (search_suggest:{hash} TTL=300s) is
    built from this table. search_service.get_autocomplete() queries here on
    Redis MISS.

    NOTE: No LGA column. Blueprint §4 HARD RULE: no LGA anywhere in codebase.
    location stores GPS coordinates only — for potential geo-aware trending.
    """

    __tablename__ = "search_queries"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # The actual search string (lowercased and stripped on write)
    query    = Column(String(500), nullable=False, index=True)

    # Blueprint category filter applied — None means cross-module
    # hotels|products|restaurants|services|properties|doctors|events
    category = Column(String(50), nullable=True)

    # Count of results returned (for quality analytics)
    results_count = Column(Integer, default=0)

    # GPS context at time of search — NOT an LGA field
    location      = Column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=True,
    )
    location_name = Column(String(200), nullable=True)  # e.g. "Lekki, Lagos"

    # Filters applied at time of search (price_range, rating_min, etc.)
    filters = Column(JSONB, default=dict)

    # Which result was tapped: "entity_type:entity_id" or None if no click
    clicked = Column(String(100), nullable=True)

    # Relationships
    user = relationship("User")

    __table_args__ = (
        Index("idx_search_queries_query_category", "query", "category"),
        Index("idx_search_queries_created",        "created_at"),
    )

    def __repr__(self) -> str:
        return f"<SearchQuery '{self.query}' cat={self.category}>"