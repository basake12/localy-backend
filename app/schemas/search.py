from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID


# ============================================
# SEARCH REQUEST
# ============================================

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    category: Optional[str] = None  # hotels | products | restaurants | services | properties | doctors | events
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    radius_km: Optional[float] = Field(10.0, ge=1, le=50)
    filters: Dict[str, Any] = Field(default_factory=dict)
    skip: int = Field(0, ge=0)
    limit: int = Field(20, ge=1, le=100)


# ============================================
# SEARCH RESULT (unified)
# ============================================

class SearchResultItem(BaseModel):
    """Generic search result item that adapts to any entity type."""
    entity_type: str        # hotel | product | restaurant | service | property | doctor | event
    entity_id: UUID
    title: str
    subtitle: Optional[str]
    description: Optional[str]
    image_url: Optional[str]
    price: Optional[float]
    rating: Optional[float]
    location: Optional[str]
    distance_km: Optional[float]
    metadata: Dict[str, Any] = Field(default_factory=dict)  # Entity-specific fields


class SearchResponse(BaseModel):
    query: str
    results: List[SearchResultItem]
    total: int
    skip: int
    limit: int
    category: Optional[str]


# ============================================
# AUTOCOMPLETE
# ============================================

class AutocompleteRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=100)
    category: Optional[str] = None
    limit: int = Field(10, ge=1, le=20)


class AutocompleteSuggestion(BaseModel):
    text: str
    category: Optional[str]
    count: int  # How many times this was searched


class AutocompleteResponse(BaseModel):
    suggestions: List[AutocompleteSuggestion]


# ============================================
# POPULAR SEARCHES
# ============================================

class PopularSearchesResponse(BaseModel):
    searches: List[AutocompleteSuggestion]