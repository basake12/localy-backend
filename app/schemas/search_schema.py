from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from uuid import UUID


# ============================================
# SEARCH REQUEST
# ============================================

class SearchRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    query: str = Field(..., min_length=1, max_length=500)
    category: Optional[str] = None  # hotels|products|restaurants|services|properties|doctors|events
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    radius_km: float = Field(5.0, ge=1.0, le=50.0)  # Blueprint default: 5 km
    filters: Dict[str, Any] = Field(default_factory=dict)
    skip: int = Field(0, ge=0)
    limit: int = Field(20, ge=1, le=100)


# ============================================
# SEARCH RESULT (unified)
# ============================================

class SearchResultItem(BaseModel):
    """Generic search result item that adapts to any entity type."""
    model_config = ConfigDict(from_attributes=True)

    entity_type: str        # hotel|product|restaurant|service|property|doctor|event
    entity_id: UUID
    title: str
    subtitle: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    price: Optional[float] = None
    rating: Optional[float] = None
    location: Optional[str] = None
    distance_km: Optional[float] = None
    is_open: Optional[bool] = None
    # Subscription tier weight — higher = promoted in results
    subscription_weight: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    query: str
    results: List[SearchResultItem]
    total: int
    skip: int
    limit: int
    category: Optional[str] = None


# ============================================
# AUTOCOMPLETE
# ============================================

class AutocompleteRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    query: str = Field(..., min_length=1, max_length=100)
    category: Optional[str] = None
    limit: int = Field(10, ge=1, le=20)


class AutocompleteSuggestion(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text: str
    category: Optional[str] = None
    count: int  # How many times this was searched


class AutocompleteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    suggestions: List[AutocompleteSuggestion]


# ============================================
# POPULAR SEARCHES
# ============================================

class PopularSearchesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    searches: List[AutocompleteSuggestion]