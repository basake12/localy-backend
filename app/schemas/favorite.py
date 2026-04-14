from pydantic import BaseModel, field_validator
from typing import Optional, Any, Dict
from datetime import datetime
from uuid import UUID

# Supported favoritable entity types — extend as new domains are added
FAVORITABLE_TYPES = {
    "hotel", "restaurant", "product", "service",
    "property", "event", "reel",
}


class FavoriteCreate(BaseModel):
    favoritable_type: str
    favoritable_id: UUID

    @field_validator("favoritable_type")
    @classmethod
    def valid_type(cls, v: str) -> str:
        v = v.lower()
        if v not in FAVORITABLE_TYPES:
            raise ValueError(
                f"favoritable_type must be one of: {', '.join(sorted(FAVORITABLE_TYPES))}"
            )
        return v


class FavoriteResponse(BaseModel):
    id: UUID
    user_id: UUID
    favoritable_type: str
    favoritable_id: UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class FavoriteToggleResponse(BaseModel):
    """Returned from POST /favorites/toggle — tells caller the new state."""
    is_favorited: bool
    favorite_id: Optional[UUID] = None  # present when is_favorited=True


class FavoriteWithDetails(FavoriteResponse):
    """Extended response with resolved entity metadata (injected by service)."""
    entity_name: Optional[str] = None
    entity_image: Optional[str] = None
    entity_meta: Optional[Dict[str, Any]] = None