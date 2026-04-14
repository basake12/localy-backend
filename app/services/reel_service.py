"""
app/services/reel_service.py

FIX:
  get_feed() signature updated from lga_id to lat/lng/radius_meters to
  align with the Blueprint radius-only location model and the corrected
  reels.py route and reels_crud.py.
"""
from typing import Optional, List
from sqlalchemy.orm import Session
from uuid import UUID

from app.crud.reels_crud import (
    reel_crud, reel_like_crud, reel_comment_crud, reel_view_crud,
)
from app.models.user_model import User
from app.schemas.reels_schema import (
    ReelCreate, ReelUpdate, ReelCommentCreate, ReelViewCreate,
)
from app.core.exceptions import NotFoundException, PermissionDeniedException
from app.crud.business_crud import business_crud
from app.core.constants import DEFAULT_RADIUS_METERS


class ReelService:

    def create_reel(
        self, db: Session, *, business_id: UUID, obj_in: ReelCreate, user: User
    ) -> dict:
        """Business owner creates a reel."""
        business = business_crud.get(db, id=business_id)
        if not business or business.user_id != user.id:
            raise PermissionDeniedException("You don't own this business")

        reel = reel_crud.create_for_business(
            db, business_id=business_id, obj_in=obj_in
        )
        db.commit()
        db.refresh(reel)
        return reel

    def get_reel(
        self, db: Session, *, reel_id: UUID, viewer_id: Optional[UUID] = None
    ) -> dict:
        reel = reel_crud.get(db, id=reel_id)
        if not reel:
            raise NotFoundException("Reel not found")

        liked_by_me = False
        if viewer_id:
            liked_by_me = reel_like_crud.is_liked(
                db, reel_id=reel_id, user_id=viewer_id
            )

        return {"reel": reel, "liked_by_me": liked_by_me}

    def get_feed(
        self,
        db: Session,
        *,
        viewer_id: Optional[UUID] = None,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
        radius_meters: int = DEFAULT_RADIUS_METERS,
        tags: Optional[List[str]] = None,
        linked_entity_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> dict:
        """
        Returns paginated reel feed — radius-filtered, subscription-ranked.
        lat/lng are device GPS coordinates for PostGIS ST_DWithin.
        Blueprint §3: radius-only, no LGA.
        Blueprint §5.4: Enterprise > Pro > Starter > Free.
        """
        reels, total = reel_crud.get_feed(
            db,
            viewer_id=viewer_id,
            lat=lat,
            lng=lng,
            radius_meters=radius_meters,
            tags=tags,
            linked_entity_type=linked_entity_type,
            skip=skip,
            limit=limit,
        )

        # Attach liked_by_me flag per reel in the result set
        if viewer_id and reels:
            reel_ids = [r.id for r in reels]
            liked = (
                db.query(reel_like_crud.model.reel_id)
                .filter(
                    reel_like_crud.model.user_id == viewer_id,
                    reel_like_crud.model.reel_id.in_(reel_ids),
                )
                .all()
            )
            liked_ids = {r[0] for r in liked}
            for reel in reels:
                reel.liked_by_me = reel.id in liked_ids

        return {"reels": reels, "total": total, "skip": skip, "limit": limit}

    def update_reel(
        self, db: Session, *, reel_id: UUID, obj_in: ReelUpdate, user: User
    ) -> dict:
        reel = reel_crud.get(db, id=reel_id)
        if not reel:
            raise NotFoundException("Reel not found")

        business = business_crud.get(db, id=reel.business_id)
        if not business or business.user_id != user.id:
            raise PermissionDeniedException("You don't own this reel")

        updated = reel_crud.update(db, db_obj=reel, obj_in=obj_in)
        db.commit()
        db.refresh(updated)
        return updated

    def delete_reel(self, db: Session, *, reel_id: UUID, user: User) -> None:
        reel = reel_crud.get(db, id=reel_id)
        if not reel:
            raise NotFoundException("Reel not found")

        business = business_crud.get(db, id=reel.business_id)
        if not business or business.user_id != user.id:
            raise PermissionDeniedException("You don't own this reel")

        reel_crud.remove(db, id=reel_id)
        db.commit()

    # ── ENGAGEMENT ────────────────────────────────────────────────────────

    def toggle_like(
        self, db: Session, *, reel_id: UUID, user_id: UUID
    ) -> dict:
        reel = reel_crud.get(db, id=reel_id)
        if not reel:
            raise NotFoundException("Reel not found")

        liked = reel_like_crud.toggle(db, reel_id=reel_id, user_id=user_id)
        delta = 1 if liked else -1
        reel_crud.increment_like_count(db, reel_id=reel_id, delta=delta)
        db.commit()
        return {"liked": liked}

    def create_comment(
        self,
        db: Session,
        *,
        reel_id: UUID,
        user_id: UUID,
        obj_in: ReelCommentCreate,
    ) -> dict:
        reel = reel_crud.get(db, id=reel_id)
        if not reel:
            raise NotFoundException("Reel not found")

        comment = reel_comment_crud.create_comment(
            db, reel_id=reel_id, user_id=user_id, obj_in=obj_in
        )
        reel_crud.increment_comment_count(db, reel_id=reel_id, delta=1)
        db.commit()
        db.refresh(comment)
        return comment

    def get_comments(
        self, db: Session, *, reel_id: UUID, skip: int = 0, limit: int = 50
    ) -> dict:
        comments, total = reel_comment_crud.get_for_reel(
            db, reel_id=reel_id, skip=skip, limit=limit
        )
        return {"comments": comments, "total": total}

    def record_view(
        self,
        db: Session,
        *,
        reel_id: UUID,
        obj_in: ReelViewCreate,
        viewer_id: Optional[UUID] = None,
    ) -> dict:
        reel = reel_crud.get(db, id=reel_id)
        if not reel:
            raise NotFoundException("Reel not found")

        reel_view_crud.record_view(
            db,
            reel_id=reel_id,
            viewer_id=viewer_id,
            watch_time_seconds=obj_in.watch_time_seconds,
            completed=obj_in.completed,
        )
        reel_crud.increment_view_count(db, reel_id=reel_id)
        db.commit()
        return {"viewed": True}


reel_service = ReelService()