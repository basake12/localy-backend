"""
app/crud/stories_crud.py

FIX:
  get_feed() now injects business_name and business_avatar_url as dynamic
  attributes onto each Story ORM object after grouping. These are not real
  DB columns — they come from the Business join. Without this injection,
  StoryOut validation finds None for both fields and StoriesViewerScreen
  renders a blank business header (name and avatar missing).

  All other fixes from the previous version are retained:
  - PostGIS ST_DWithin radius filter (no LGA)
  - datetime.now(timezone.utc) everywhere
  - Subscription tier ranking in feed sort
"""
from typing import Optional, List, Dict
from sqlalchemy.orm import Session
from sqlalchemy import func, case, text
from uuid import UUID
from datetime import datetime, timedelta, timezone

from app.crud.base_crud import CRUDBase
from app.models.stories_model import Story, StoryView
from app.models.business_model import Business
from app.schemas.stories_schema import StoryCreate, StoryUpdate
from app.config import settings
from app.core.constants import DEFAULT_RADIUS_METERS

_FEED_MAX_STORIES = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _subscription_tier_rank():
    """Numeric tier for sorting: Enterprise=4, Pro=3, Starter=2, Free/other=1."""
    return case(
        (Business.subscription_plan == "enterprise", 4),
        (Business.subscription_plan == "pro",        3),
        (Business.subscription_plan == "starter",    2),
        else_=1,
    )


class CRUDStory(CRUDBase[Story, StoryCreate, StoryUpdate]):

    def create_for_business(
        self, db: Session, *, business_id: UUID, obj_in: StoryCreate
    ) -> Story:
        expires_at = _now() + timedelta(
            hours=getattr(settings, "STORY_EXPIRE_HOURS", 24)
        )
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
        q = db.query(Story).filter(
            Story.is_active == True,
            Story.expires_at > _now(),
        )
        if business_id:
            q = q.filter(Story.business_id == business_id)
        return q.order_by(Story.created_at.desc()).all()

    def get_feed(
        self,
        db: Session,
        *,
        viewer_id: Optional[UUID] = None,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
        radius_meters: int = DEFAULT_RADIUS_METERS,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple:
        """
        Returns active stories grouped by business, filtered by GPS radius.
        Injects business_name and business_avatar_url onto each Story ORM
        object so StoryOut can serialise them for the viewer screen header.
        """
        now = _now()
        tier_rank = _subscription_tier_rank()

        q = (
            db.query(Story, Business)
            .join(Business, Story.business_id == Business.id)
            .filter(
                Story.is_active == True,
                Story.expires_at > now,
                Business.is_active == True,
            )
        )

        # Radius-based filter via PostGIS ST_DWithin — Blueprint: no LGA
        if lat is not None and lng is not None:
            point = f"ST_SetSRID(ST_MakePoint({lng}, {lat}), 4326)::geography"
            q = q.filter(
                text(
                    f"ST_DWithin(businesses.location, {point}, :radius)"
                ).bindparams(radius=radius_meters)
            )

        rows = (
            q.order_by(tier_rank.desc(), Story.created_at.desc())
            .limit(_FEED_MAX_STORIES)
            .all()
        )

        # Group by business — maintains tier → recency order from query
        business_map: Dict[UUID, dict] = {}
        for story, business in rows:
            if business.id not in business_map:
                business_map[business.id] = {
                    "business_id":       business.id,
                    "business_name":     business.business_name,
                    "business_logo":     business.logo,
                    "subscription_rank": (
                        4 if business.subscription_plan == "enterprise" else
                        3 if business.subscription_plan == "pro"        else
                        2 if business.subscription_plan == "starter"    else 1
                    ),
                    "stories": [],
                }
            # FIX: inject business display fields onto the Story ORM object.
            # Story has no business_name / business_avatar_url columns.
            # Setting them as dynamic attributes lets Pydantic (from_attributes)
            # pick them up when serialising to StoryOut.
            story.business_name       = business.business_name
            story.business_avatar_url = business.logo
            business_map[business.id]["stories"].append(story)

        # Determine which stories the viewer has already seen
        if viewer_id:
            story_ids = [s.id for s, _ in rows]
            viewed_ids = {
                v[0]
                for v in db.query(StoryView.story_id).filter(
                    StoryView.viewer_id == viewer_id,
                    StoryView.story_id.in_(story_ids),
                ).all()
            } if story_ids else set()
        else:
            viewed_ids = set()

        # Build feed items
        feed = []
        for data in business_map.values():
            has_unseen = any(s.id not in viewed_ids for s in data["stories"])
            feed.append({
                "business_id":     data["business_id"],
                "business_name":   data["business_name"],
                "business_logo":   data["business_logo"],
                "story_count":     len(data["stories"]),
                "latest_story_at": max(s.created_at for s in data["stories"]),
                "has_unseen":      has_unseen,
                "stories":         data["stories"],
            })

        # Primary: unseen first. Secondary: subscription tier. Tertiary: recency.
        feed.sort(
            key=lambda x: (
                not x["has_unseen"],
                -business_map[x["business_id"]]["subscription_rank"],
                -x["latest_story_at"].timestamp(),
            )
        )

        total = len(feed)
        return feed[skip: skip + limit], total

    def increment_view_count(self, db: Session, *, story_id: UUID) -> None:
        db.query(Story).filter(Story.id == story_id).update(
            {"view_count": Story.view_count + 1}
        )
        db.flush()

    def expire_old_stories(self, db: Session) -> int:
        """Called by Celery cleanup task."""
        count = (
            db.query(Story)
            .filter(Story.expires_at < _now(), Story.is_active == True)
            .update({"is_active": False})
        )
        db.flush()
        return count


class CRUDStoryView(CRUDBase[StoryView, None, None]):

    def record_view(
        self, db: Session, *, story_id: UUID, viewer_id: UUID
    ) -> Optional[StoryView]:
        """Idempotent — returns None if already viewed."""
        existing = (
            db.query(StoryView)
            .filter(
                StoryView.story_id  == story_id,
                StoryView.viewer_id == viewer_id,
            )
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
        q     = db.query(StoryView).filter(StoryView.story_id == story_id)
        total = q.with_entities(func.count(StoryView.id)).scalar() or 0
        views = (
            q.order_by(StoryView.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return views, total


story_crud      = CRUDStory(Story)
story_view_crud = CRUDStoryView(StoryView)