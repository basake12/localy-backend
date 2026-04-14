from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from typing import Optional, List
from uuid import UUID

from app.models.favorites_model import Favorite


async def get_favorite(
    db: AsyncSession,
    user_id: UUID,
    favoritable_type: str,
    favoritable_id: UUID,
) -> Optional[Favorite]:
    result = await db.execute(
        select(Favorite).where(
            and_(
                Favorite.user_id == user_id,
                Favorite.favoritable_type == favoritable_type,
                Favorite.favoritable_id == favoritable_id,
            )
        )
    )
    return result.scalar_one_or_none()


async def get_favorite_by_id(
    db: AsyncSession, favorite_id: UUID, user_id: UUID
) -> Optional[Favorite]:
    result = await db.execute(
        select(Favorite).where(
            and_(Favorite.id == favorite_id, Favorite.user_id == user_id)
        )
    )
    return result.scalar_one_or_none()


async def list_user_favorites(
    db: AsyncSession,
    user_id: UUID,
    favoritable_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
) -> List[Favorite]:
    q = select(Favorite).where(Favorite.user_id == user_id)
    if favoritable_type:
        q = q.where(Favorite.favoritable_type == favoritable_type)
    q = q.order_by(Favorite.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(q)
    return list(result.scalars().all())


async def count_user_favorites(
    db: AsyncSession,
    user_id: UUID,
    favoritable_type: Optional[str] = None,
) -> int:
    q = select(func.count(Favorite.id)).where(Favorite.user_id == user_id)
    if favoritable_type:
        q = q.where(Favorite.favoritable_type == favoritable_type)
    result = await db.execute(q)
    return result.scalar_one() or 0


async def create_favorite(
    db: AsyncSession,
    user_id: UUID,
    favoritable_type: str,
    favoritable_id: UUID,
) -> Favorite:
    favorite = Favorite(
        user_id=user_id,
        favoritable_type=favoritable_type,
        favoritable_id=favoritable_id,
    )
    db.add(favorite)
    await db.commit()
    await db.refresh(favorite)
    return favorite


async def delete_favorite(db: AsyncSession, favorite: Favorite) -> None:
    await db.delete(favorite)
    await db.commit()


async def is_favorited(
    db: AsyncSession,
    user_id: UUID,
    favoritable_type: str,
    favoritable_id: UUID,
) -> bool:
    result = await get_favorite(db, user_id, favoritable_type, favoritable_id)
    return result is not None
