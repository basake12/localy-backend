from sqlalchemy import Column, String, Integer, ForeignKey, Index
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geography
import uuid

from app.models.base_model import BaseModel


# ============================================
# SEARCH QUERY (analytics & autocomplete)
# ============================================

class SearchQuery(BaseModel):
    """
    Tracks all search queries for:
    - Analytics (what users search for)
    - Autocomplete / suggestions
    - Popular searches
    """

    __tablename__ = "search_queries"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    query = Column(String(500), nullable=False, index=True)
    category = Column(String(50), nullable=True)  # hotels | products | restaurants | services | etc.

    # Results count
    results_count = Column(Integer, default=0)

    # Location context
    location = Column(Geography(geometry_type='POINT', srid=4326), nullable=True)
    location_name = Column(String(200), nullable=True)  # "Abuja", "Lagos Island", etc.

    # Filters applied
    filters = Column(JSONB, default=dict)  # price_range, rating, etc.

    # Engagement
    clicked = Column(String(100), nullable=True)  # entity_type:entity_id if user clicked a result

    # Relationships
    user = relationship("User")

    __table_args__ = (
        Index('idx_search_queries_query_category', 'query', 'category'),
        Index('idx_search_queries_created', 'created_at'),
    )