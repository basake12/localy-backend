from typing import Optional, List, Dict
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from uuid import UUID
from datetime import datetime, timedelta

from app.crud.base_crud import CRUDBase
from app.models.stories_model import Story, StoryView
from app.models.business_model import Business
from app.schemas.stories_schema import StoryCreate, StoryUpdate
from app.config import settings


class CRUDStory(CRUDBase[Story, StoryCreate, StoryUpdate]):

    def create_for_business(
            self, db: Session, *, business_id: UUID, obj_in: StoryCreate
    ) -> Story:
        """Create a new story with auto-calculated expiry."""
        expires_at = datetime.utcnow() + timedelta(hours=settings.STORY_EXPIRE_HOURS)

        story = Story(
            business_id=business_id,
            story_type=obj_in.story_type,
            media_url=obj_in.media_url,
            thumbnail_url=obj_in.thumbnail_url,
            text_content=obj_in.text_content,
            background_color=obj_in.background_color,
            duration_seconds=obj_in.duration_seconds,
            cta_text=obj_in.cta_text,
            cta_url=obj_in.cta_url,
            expires_at=expires_at,
        )
        db.add(story)
        db.flush()
        return story

    def get_active_stories(
            self, db: Session, *, business_id: Optional[UUID] = None
    ) -> List[Story]:
        """Get all active (non-expired) stories, optionally filtered by business."""
        q = db.query(Story).filter(
            Story.is_active == True,
            Story.expires_at > datetime.utcnow(),
        )
        if business_id:
            q = q.filter(Story.business_id == business_id)

        return q.order_by(Story.created_at.desc()).all()

    def get_feed(
            self, db: Session, *, viewer_id: Optional[UUID] = None, skip: int = 0, limit: int = 20
    ) -> List[Dict]:
        """
        Returns stories grouped by business.
        Each group includes: business info, story count, has_unseen flag.
        """
        now = datetime.utcnow()

        # Get active stories with business info
        stories = (
            db.query(Story, Business)
            .join(Business, Story.business_id == Business.id)
            .filter(
                Story.is_active == True,
                Story.expires_at > now,
            )
            .order_by(Story.created_at.desc())
            .all()
        )

        # Group by business
        business_map = {}
        for story, business in stories:
            if business.id not in business_map:
                business_map[business.id] = {
                    "business_id": business.id,
                    "business_name": business.business_name,
                    "business_logo": business.logo_url,
                    "stories": [],
                }
            business_map[business.id]["stories"].append(story)

        # Check which stories the viewer has seen
        if viewer_id:
            story_ids = [s.id for s, _ in stories]
            viewed = (
                db.query(StoryView.story_id)
                .filter(
                    StoryView.viewer_id == viewer_id,
                    StoryView.story_id.in_(story_ids),
                )
                .all()
            )
            viewed_ids = {v[0] for v in viewed}
        else:
            viewed_ids = set()

        # Build feed items
        feed = []
        for biz_id, data in business_map.items():
            has_unseen = any(s.id not in viewed_ids for s in data["stories"])
            feed.append({
                "business_id": data["business_id"],
                "business_name": data["business_name"],
                "business_logo": data["business_logo"],
                "story_count": len(data["stories"]),
                "latest_story_at": max(s.created_at for s in data["stories"]),
                "has_unseen": has_unseen,
                "stories": data["stories"],
            })

        # Sort by has_unseen first, then by latest_story_at
        feed.sort(key=lambda x: (not x["has_unseen"], -x["latest_story_at"].timestamp()))

        return feed[skip:skip + limit], len(feed)

    def increment_view_count(self, db: Session, *, story_id: UUID) -> None:
        """Denormalized counter update."""
        db.query(Story).filter(Story.id == story_id).update(
            {"view_count": Story.view_count + 1}
        )
        db.flush()

    def expire_old_stories(self, db: Session) -> int:
        """Mark expired stories as inactive. Returns count."""
        count = (
            db.query(Story)
            .filter(Story.expires_at < datetime.utcnow(), Story.is_active == True)
            .update({"is_active": False})
        )
        db.flush()
        return count


class CRUDStoryView(CRUDBase[StoryView, None, None]):

    def record_view(
            self, db: Session, *, story_id: UUID, viewer_id: UUID
    ) -> Optional[StoryView]:
        """Idempotent view recording. Returns None if already viewed."""
        existing = (
            db.query(StoryView)
            .filter(StoryView.story_id == story_id, StoryView.viewer_id == viewer_id)
            .first()
        )
        if existing:
            return None

        view = StoryView(story_id=story_id, viewer_id=viewer_id)
        db.add(view)
        db.flush()
        return view

    def get_viewers(
            self, db: Session, *, story_id: UUID, skip: int = 0, limit: int = 50
    ) -> tuple:
        """Returns (views, total_count)."""
        q = db.query(StoryView).filter(StoryView.story_id == story_id)
        total = q.with_entities(func.count(StoryView.id)).scalar() or 0
        views = q.order_by(StoryView.created_at.desc()).offset(skip).limit(limit).all()
        return views, total


story_crud = CRUDStory(Story)
story_view_crud = CRUDStoryView(StoryView)