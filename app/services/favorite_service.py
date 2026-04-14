from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException, status

from app.schemas.favorite import (
    FavoriteCreate,
    FavoriteResponse,
    FavoriteToggleResponse,
    FavoriteWithDetails,
)
from app.crud import favorite as favorite_crud


# ============================================
# FAVORITE SERVICE
# ============================================

async def toggle_favorite(
    db: AsyncSession,
    user_id: UUID,
    data: FavoriteCreate,
) -> FavoriteToggleResponse:
    """Add if not favorited, remove if already favorited."""
    existing = await favorite_crud.get_favorite(
        db, user_id, data.favoritable_type, data.favoritable_id
    )
    if existing:
        await favorite_crud.delete_favorite(db, existing)
        return FavoriteToggleResponse(is_favorited=False)

    fav = await favorite_crud.create_favorite(
        db,
        user_id=user_id,
        favoritable_type=data.favoritable_type,
        favoritable_id=data.favoritable_id,
    )
    return FavoriteToggleResponse(is_favorited=True, favorite_id=fav.id)


async def list_favorites(
    db: AsyncSession,
    user_id: UUID,
    favoritable_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
) -> List[FavoriteResponse]:
    favorites = await favorite_crud.list_user_favorites(
        db, user_id, favoritable_type=favoritable_type, skip=skip, limit=limit
    )
    return [FavoriteResponse.model_validate(f) for f in favorites]


async def remove_favorite(
    db: AsyncSession,
    user_id: UUID,
    favorite_id: UUID,
) -> None:
    fav = await favorite_crud.get_favorite_by_id(db, favorite_id, user_id)
    if not fav:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Favorite not found")
    await favorite_crud.delete_favorite(db, fav)


async def check_is_favorited(
    db: AsyncSession,
    user_id: UUID,
    favoritable_type: str,
    favoritable_id: UUID,
) -> bool:
    return await favorite_crud.is_favorited(
        db, user_id, favoritable_type, favoritable_id
    )