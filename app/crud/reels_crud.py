"""
app/crud/reels_crud.py

FIXES:
  1. get_feed() now uses PostGIS ST_DWithin for radius-based filtering.
     Blueprint is explicit: "Location model — Radius-based (default 5 km)
     — no LGA dependency." The previous lga_id join on Business.local_government
     violated this fundamental constraint. Feed now accepts lat, lng,
     radius_meters (default 5000 m) and filters by business coordinates.

  2. Feed ordering now ranks by subscription tier (Enterprise > Pro >
     Starter > Free) per Blueprint §5.4: "Feed is ranked: Enterprise >
     Pro > Starter > Free (organic)." A CASE expression over
     Business.subscription_plan produces the tier rank column.

  3. create_for_user() removed — Blueprint §5.1: "Only verified businesses
     may post reels." Customer reel creation is not permitted.
"""
import json
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, case, text
from sqlalchemy.dialects.postgresql import JSONB
from uuid import UUID

from app.crud.base_crud import CRUDBase
from app.models.reels_model import Reel, ReelLike, ReelComment, ReelView
from app.models.business_model import Business
from app.schemas.reels_schema import ReelCreate, ReelUpdate, ReelCommentCreate
from app.core.constants import DEFAULT_RADIUS_METERS


def _subscription_tier_rank():
    """
    SQLAlchemy CASE expression that converts subscription_plan to a numeric rank.
    Enterprise=4, Pro=3, Starter=2, Free/anything else=1.
    Used for feed ordering per Blueprint §5.4.
    """
    return case(
        (Business.subscription_plan == "enterprise", 4),
        (Business.subscription_plan == "pro",        3),
        (Business.subscription_plan == "starter",    2),
        else_=1,
    )


class CRUDReel(CRUDBase[Reel, ReelCreate, ReelUpdate]):

    def create_for_business(
        self, db: Session, *, business_id: UUID, obj_in: ReelCreate
    ) -> Reel:
        reel = Reel(
            business_id=business_id,
            video_url=obj_in.video_url,
            thumbnail_url=obj_in.thumbnail_url,
            duration_seconds=obj_in.duration_seconds,
            caption=obj_in.caption,
            tags=obj_in.tags,
            linked_entity_type=obj_in.linked_entity_type,
            linked_entity_id=obj_in.linked_entity_id,
        )
        db.add(reel)
        db.flush()
        return reel

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
    ) -> tuple:
        """
        Radius-filtered, subscription-ranked reels feed.
        Requires lat/lng for PostGIS radius filter (Blueprint: no LGA).
        Falls back to unfiltered (all active reels) when no coordinates
        are provided — e.g. anonymous users without GPS permission.
        """
        # FIX: Always join Business so we can rank by subscription tier.
        q = (
            db.query(Reel)
            .join(Business, Reel.business_id == Business.id)
            .filter(Reel.is_active == True, Business.is_active == True)
        )

        # FIX: radius-based filter via PostGIS ST_DWithin (Blueprint §3).
        # Business.location is a PostGIS Geography point (set at registration).
        if lat is not None and lng is not None:
            point = f"ST_SetSRID(ST_MakePoint({lng}, {lat}), 4326)::geography"
            q = q.filter(
                text(
                    f"ST_DWithin(businesses.location, {point}, :radius)"
                ).bindparams(radius=radius_meters)
            )

        if tags:
            q = q.filter(
                Reel.tags.op("&&")(cast(json.dumps(tags), JSONB))
            )

        if linked_entity_type:
            q = q.filter(Reel.linked_entity_type == linked_entity_type)

        total = q.with_entities(func.count(Reel.id)).scalar() or 0

        # FIX: rank by is_featured (admin-boosted) → subscription tier →
        # created_at, giving Enterprise businesses organic priority per §5.4.
        tier_rank = _subscription_tier_rank()
        reels = (
            q.order_by(
                Reel.is_featured.desc(),
                tier_rank.desc(),
                Reel.created_at.desc(),
            )
            .offset(skip)
            .limit(limit)
            .all()
        )
        return reels, total

    def get_by_business(
        self, db: Session, *, business_id: UUID, skip: int = 0, limit: int = 20
    ) -> tuple:
        q = db.query(Reel).filter(
            Reel.business_id == business_id,
            Reel.is_active == True,
        )
        total = q.with_entities(func.count(Reel.id)).scalar() or 0
        reels = (
            q.order_by(Reel.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return reels, total

    def increment_view_count(self, db: Session, *, reel_id: UUID) -> None:
        db.query(Reel).filter(Reel.id == reel_id).update(
            {"view_count": Reel.view_count + 1}
        )
        db.flush()

    def increment_like_count(
        self, db: Session, *, reel_id: UUID, delta: int
    ) -> None:
        db.query(Reel).filter(Reel.id == reel_id).update(
            {"like_count": Reel.like_count + delta}
        )
        db.flush()

    def increment_comment_count(
        self, db: Session, *, reel_id: UUID, delta: int
    ) -> None:
        db.query(Reel).filter(Reel.id == reel_id).update(
            {"comment_count": Reel.comment_count + delta}
        )
        db.flush()

    def increment_share_count(self, db: Session, *, reel_id: UUID) -> None:
        db.query(Reel).filter(Reel.id == reel_id).update(
            {"share_count": Reel.share_count + 1}
        )
        db.flush()


class CRUDReelLike(CRUDBase[ReelLike, None, None]):

    def toggle(
        self, db: Session, *, reel_id: UUID, user_id: UUID
    ) -> bool:
        existing = (
            db.query(ReelLike)
            .filter(
                ReelLike.reel_id == reel_id,
                ReelLike.user_id == user_id,
            )
            .first()
        )
        if existing:
            db.delete(existing)
            db.flush()
            return False
        db.add(ReelLike(reel_id=reel_id, user_id=user_id))
        db.flush()
        return True

    def is_liked(
        self, db: Session, *, reel_id: UUID, user_id: UUID
    ) -> bool:
        return (
            db.query(ReelLike)
            .filter(
                ReelLike.reel_id == reel_id,
                ReelLike.user_id == user_id,
            )
            .first()
        ) is not None


class CRUDReelComment(CRUDBase[ReelComment, ReelCommentCreate, None]):

    def create_comment(
        self,
        db: Session,
        *,
        reel_id: UUID,
        user_id: UUID,
        obj_in: ReelCommentCreate,
    ) -> ReelComment:
        comment = ReelComment(
            reel_id=reel_id,
            user_id=user_id,
            body=obj_in.body,
            parent_id=obj_in.parent_id,
        )
        db.add(comment)
        db.flush()
        return comment

    def get_for_reel(
        self, db: Session, *, reel_id: UUID, skip: int = 0, limit: int = 50
    ) -> tuple:
        q = db.query(ReelComment).filter(
            ReelComment.reel_id == reel_id,
            ReelComment.parent_id.is_(None),  # top-level only
        )
        total    = q.with_entities(func.count(ReelComment.id)).scalar() or 0
        comments = (
            q.order_by(ReelComment.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return comments, total


class CRUDReelView(CRUDBase[ReelView, None, None]):

    def record_view(
        self,
        db: Session,
        *,
        reel_id: UUID,
        viewer_id: Optional[UUID],
        watch_time_seconds: int,
        completed: bool,
    ) -> ReelView:
        view = ReelView(
            reel_id=reel_id,
            viewer_id=viewer_id,
            watch_time_seconds=watch_time_seconds,
            completed=completed,
        )
        db.add(view)
        db.flush()
        return view


reel_crud         = CRUDReel(Reel)
reel_like_crud    = CRUDReelLike(ReelLike)
reel_comment_crud = CRUDReelComment(ReelComment)
reel_view_crud    = CRUDReelView(ReelView)