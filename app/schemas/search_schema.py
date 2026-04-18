"""
app/schemas/search_schema.py

No blueprint violations found in original.
Presented here for completeness alongside corrected search files.

Confirms:
  - radius_km default 5.0, range 1.0–50.0 (Blueprint §4.1).
  - No lga_id field anywhere (Blueprint §4 HARD RULE).
  - SearchResultItem includes distance_km (Blueprint §4.2 — now populated by service).
  - subscription_weight field present for Blueprint §7.2 ranking.
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from uuid import UUID


# ── Search Request ─────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    query: str = Field(..., min_length=1, max_length=500)

    # Category filter — None means search all seven modules
    category: Optional[str] = Field(
        None,
        description="hotels|products|restaurants|food|services|properties|property|doctors|health|events",
    )

    # Blueprint §4.1: location required for radius-based results
    location_lat: Optional[float] = Field(None, description="User GPS latitude")
    location_lng: Optional[float] = Field(None, description="User GPS longitude")

    # Blueprint §4.1: default 5 km, adjustable 1–50 km
    radius_km: float = Field(
        5.0,
        ge=1.0,
        le=50.0,
        description="Discovery radius in kilometres. Default: 5 km. Range: 1–50 km.",
    )

    # Additional filters (price_range, rating, amenities etc.)
    filters: Dict[str, Any] = Field(default_factory=dict)

    # Pagination
    skip: int  = Field(0, ge=0)
    limit: int = Field(20, ge=1, le=100)

    # NOTE: No lga_id field. Blueprint §4 HARD RULE: no LGA anywhere.


# ── Search Result ──────────────────────────────────────────────────────────────

class SearchResultItem(BaseModel):
    """
    Unified search result — adapts to any of the seven Localy entity types.
    Blueprint §4.2: distance_km populated from ST_Distance (metres → km).
    Blueprint §7.2: subscription_weight used for ranking (Enterprise=4 … Free=1).
    """
    model_config = ConfigDict(from_attributes=True)

    entity_type: str         # hotel|product|restaurant|service|property|doctor|event
    entity_id: UUID
    title: str
    subtitle: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    price: Optional[float] = None
    rating: Optional[float] = None
    location: Optional[str] = None

    # Blueprint §4.2: "Every listing card shows '1.2 km away' or 'Within 500 m'"
    # FIX: was hardcoded None in service — now computed via ST_Distance
    distance_km: Optional[float] = None

    is_open: Optional[bool] = None

    # Blueprint §7.2 factor 1: Enterprise(4) > Pro(3) > Starter(2) > Free(1)
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


# ── Autocomplete ───────────────────────────────────────────────────────────────

class AutocompleteRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    query: str = Field(..., min_length=1, max_length=100)
    category: Optional[str] = None
    limit: int = Field(10, ge=1, le=20)


class AutocompleteSuggestion(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text: str
    category: Optional[str] = None
    count: int = Field(..., description="Number of times this was searched")


class AutocompleteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    suggestions: List[AutocompleteSuggestion]


# ── Popular / Trending Searches ────────────────────────────────────────────────

class PopularSearchesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    searches: List[AutocompleteSuggestion]