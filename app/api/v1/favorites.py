from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from uuid import UUID

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.favorite import (
    FavoriteCreate,
    FavoriteResponse,
    FavoriteToggleResponse,
)
from app.services import favorite_service

router = APIRouter(tags=["Favorites"])


@router.post("/toggle", response_model=FavoriteToggleResponse)
async def toggle_favorite(
    data: FavoriteCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add to favorites if not already saved, remove if already saved.
    Returns the new is_favorited state.
    """
    return await favorite_service.toggle_favorite(db, current_user.id, data)


@router.get("", response_model=List[FavoriteResponse])
async def list_my_favorites(
    type: Optional[str] = Query(None, description="Filter by type: hotel, product, restaurant, â€¦"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the current user's saved favorites, optionally filtered by type."""
    return await favorite_service.list_favorites(
        db, current_user.id, favoritable_type=type, skip=skip, limit=limit
    )


@router.get("/check", response_model=FavoriteToggleResponse)
async def check_is_favorited(
    type: str = Query(..., description="Entity type: hotel, product, etc."),
    id: UUID = Query(..., description="Entity UUID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Check whether a specific entity is in the current user's favorites.
    Lightweight â€” used on detail screens to set the heart icon state.
    """
    is_fav = await favorite_service.check_is_favorited(
        db, current_user.id, favoritable_type=type, favoritable_id=id
    )
    return FavoriteToggleResponse(is_favorited=is_fav)


@router.delete("/{favorite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(
    favorite_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a specific favorite by its ID."""
    await favorite_service.remove_favorite(db, current_user.id, favorite_id)