from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from uuid import UUID

from app.crud.base_crud import CRUDBase
from app.models.reels_model import Reel, ReelLike, ReelComment, ReelView
from app.schemas.reels_schema import ReelCreate, ReelUpdate, ReelCommentCreate


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
        )
        db.add(reel)
        db.flush()
        return reel

    def get_feed(
            self, db: Session, *,
            viewer_id: Optional[UUID] = None,
            tags: Optional[List[str]] = None,
            skip: int = 0,
            limit: int = 20,
    ) -> tuple:
        """
        Returns reels feed sorted by (is_featured DESC, created_at DESC).
        Optionally filter by tags.
        """
        q = db.query(Reel).filter(Reel.is_active == True)

        if tags:
            # Filter reels that have ANY of the provided tags
            # Using JSONB @> operator
            q = q.filter(Reel.tags.op('@>')(tags))

        total = q.with_entities(func.count(Reel.id)).scalar() or 0
        reels = (
            q.order_by(Reel.is_featured.desc(), Reel.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

        return reels, total

    def get_by_business(
            self, db: Session, *, business_id: UUID, skip: int = 0, limit: int = 20
    ) -> tuple:
        q = db.query(Reel).filter(Reel.business_id == business_id, Reel.is_active == True)
        total = q.with_entities(func.count(Reel.id)).scalar() or 0
        reels = q.order_by(Reel.created_at.desc()).offset(skip).limit(limit).all()
        return reels, total

    def increment_view_count(self, db: Session, *, reel_id: UUID) -> None:
        db.query(Reel).filter(Reel.id == reel_id).update({"view_count": Reel.view_count + 1})
        db.flush()

    def increment_like_count(self, db: Session, *, reel_id: UUID, delta: int) -> None:
        db.query(Reel).filter(Reel.id == reel_id).update({"like_count": Reel.like_count + delta})
        db.flush()

    def increment_comment_count(self, db: Session, *, reel_id: UUID, delta: int) -> None:
        db.query(Reel).filter(Reel.id == reel_id).update({"comment_count": Reel.comment_count + delta})
        db.flush()


class CRUDReelLike(CRUDBase[ReelLike, None, None]):

    def toggle(self, db: Session, *, reel_id: UUID, user_id: UUID) -> bool:
        """
        Toggle like. Returns True if liked, False if unliked.
        """
        existing = (
            db.query(ReelLike)
            .filter(ReelLike.reel_id == reel_id, ReelLike.user_id == user_id)
            .first()
        )

        if existing:
            db.delete(existing)
            db.flush()
            return False
        else:
            like = ReelLike(reel_id=reel_id, user_id=user_id)
            db.add(like)
            db.flush()
            return True

    def is_liked(self, db: Session, *, reel_id: UUID, user_id: UUID) -> bool:
        return (
            db.query(ReelLike)
            .filter(ReelLike.reel_id == reel_id, ReelLike.user_id == user_id)
            .first()
        ) is not None


class CRUDReelComment(CRUDBase[ReelComment, ReelCommentCreate, None]):

    def create_comment(
            self, db: Session, *, reel_id: UUID, user_id: UUID, obj_in: ReelCommentCreate
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
        """Returns top-level comments (parent_id=NULL) + total count."""
        q = (
            db.query(ReelComment)
            .filter(ReelComment.reel_id == reel_id, ReelComment.parent_id.is_(None))
        )
        total = q.with_entities(func.count(ReelComment.id)).scalar() or 0
        comments = q.order_by(ReelComment.created_at.desc()).offset(skip).limit(limit).all()
        return comments, total


class CRUDReelView(CRUDBase[ReelView, None, None]):

    def record_view(
            self, db: Session, *,
            reel_id: UUID,
            viewer_id: Optional[UUID],
            watch_time_seconds: int,
            completed: bool,
    ) -> ReelView:
        """Record a view event (can be anonymous)."""
        view = ReelView(
            reel_id=reel_id,
            viewer_id=viewer_id,
            watch_time_seconds=watch_time_seconds,
            completed=completed,
        )
        db.add(view)
        db.flush()
        return view


reel_crud = CRUDReel(Reel)
reel_like_crud = CRUDReelLike(ReelLike)
reel_comment_crud = CRUDReelComment(ReelComment)
reel_view_crud = CRUDReelView(ReelView)