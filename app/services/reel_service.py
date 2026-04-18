"""
app/services/reel_service.py

FIXES:
  [AUDIT BUG-9] create_reel() now enforces business.is_verified at the
  SERVICE LAYER — not just at the router dependency level.

  Root cause of original bug:
    The router (reels.py) correctly uses require_verified_business.
    However the SERVICE LAYER only checked ownership (business.user_id != user.id).
    Blueprint §8.4 HARD RULE must be enforced at every call site for
    defense-in-depth. Same reasoning as story_service.py BUG-8.

  Blueprint §8.4 HARD RULE:
    "Only VERIFIED businesses may post reels. Unverified businesses see these
     features locked with a clear verification prompt — not a silent blank state."

  get_feed() uses lat/lng/radius_meters (radius-only). No LGA.
  Blueprint §4 / §2 HARD RULE: no LGA filtering anywhere.

  Feed ranking: Enterprise > Pro > Starter > Free (subscription_tier_rank DESC).
  Blueprint §7.3 / §7.2.
"""
from typing import Optional, List
from uuid import UUID

from sqlalchemy.orm import Session

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
        """
        Business owner creates a reel.

        Blueprint §8.4 HARD RULE (enforced at SERVICE LAYER):
          "Only VERIFIED businesses may post reels."

        Tags JSONB structure (Blueprint §8.4 / §P08):
          [{ timestamp_ms: INT, listing_id: UUID,
             x_position: FLOAT, y_position: FLOAT }]
        Stored in reels.tags JSONB array.
        """
        business = business_crud.get(db, id=business_id)
        if not business or business.user_id != user.id:
            raise PermissionDeniedException("You don't own this business.")

        # [BUG-9 FIX] — Blueprint §8.4 HARD RULE: verified businesses only.
        # Enforced at service layer for defense-in-depth (see module docstring).
        if not business.is_verified:
            raise PermissionDeniedException(
                "Your business must be verified by an admin before you can post "
                "reels. Complete your profile and await admin review."
            )

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

        if not reel.is_active:
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
        category: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> dict:
        """
        Returns paginated reel feed — radius-filtered, subscription-ranked.

        Blueprint §4 HARD RULE: lat/lng GPS coordinates only. No LGA parameter.
        Blueprint §7.3: Enterprise first → Pro → Starter → Free (organic).
        Blueprint §7.3: Favourited businesses appear before all others in each tier.
        Blueprint §7.3: "From businesses near you" label on all feed content.
        """
        reels, total = reel_crud.get_feed(
            db,
            viewer_id=viewer_id,
            lat=lat,
            lng=lng,
            radius_meters=radius_meters,
            tags=tags,
            linked_entity_type=linked_entity_type,
            category=category,
            skip=skip,
            limit=limit,
        )

        # Attach liked_by_me flag per reel in the result set (batch query)
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
            raise PermissionDeniedException("You don't own this reel.")

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
            raise PermissionDeniedException("You don't own this reel.")

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
        """
        Record a reel view.
        Blueprint §8.4 analytics: views, total play time, likes, shares,
        taps on tagged items, conversion rate from reel to purchase.
        """
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