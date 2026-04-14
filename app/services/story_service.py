"""
app/services/story_service.py

FIX:
  get_feed() signature updated from lga to lat/lng/radius_meters to align
  with Blueprint radius-only location model and corrected stories.py route.
"""
from typing import Optional
from sqlalchemy.orm import Session
from uuid import UUID

from app.crud.stories_crud import story_crud, story_view_crud
from app.models.user_model import User
from app.schemas.stories_schema import StoryCreate, StoryUpdate
from app.core.exceptions import NotFoundException, PermissionDeniedException
from app.crud.business_crud import business_crud
from app.core.constants import DEFAULT_RADIUS_METERS


class StoryService:

    def create_story(
        self, db: Session, *, business_id: UUID, obj_in: StoryCreate, user: User
    ) -> dict:
        """Business owner creates a story."""
        business = business_crud.get(db, id=business_id)
        if not business or business.user_id != user.id:
            raise PermissionDeniedException("You don't own this business")

        story = story_crud.create_for_business(
            db, business_id=business_id, obj_in=obj_in
        )
        db.commit()
        db.refresh(story)
        return story

    def get_story(
        self, db: Session, *, story_id: UUID, viewer_id: Optional[UUID] = None
    ) -> dict:
        story = story_crud.get(db, id=story_id)
        if not story:
            raise NotFoundException("Story not found")

        viewed_by_me = False
        if viewer_id:
            viewed_by_me = (
                db.query(story_view_crud.model)
                .filter_by(story_id=story_id, viewer_id=viewer_id)
                .first()
            ) is not None

        return {"story": story, "viewed_by_me": viewed_by_me}

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
    ) -> dict:
        """
        Returns stories grouped by business, filtered by GPS radius.
        Blueprint §3: radius-only, no LGA.
        Blueprint §5.4: businesses with unseen stories first, then tier rank.
        """
        items, total = story_crud.get_feed(
            db,
            viewer_id=viewer_id,
            lat=lat,
            lng=lng,
            radius_meters=radius_meters,
            skip=skip,
            limit=limit,
        )

        # Attach viewed_by_me flag to each story in every feed group
        if viewer_id:
            all_story_ids = [s.id for item in items for s in item["stories"]]
            if all_story_ids:
                viewed_ids = {
                    v[0]
                    for v in db.query(story_view_crud.model.story_id)
                    .filter(
                        story_view_crud.model.viewer_id == viewer_id,
                        story_view_crud.model.story_id.in_(all_story_ids),
                    )
                    .all()
                }
            else:
                viewed_ids = set()

            for item in items:
                for story in item["stories"]:
                    story.viewed_by_me = story.id in viewed_ids

        return {"items": items, "total": total}

    def record_view(
        self, db: Session, *, story_id: UUID, viewer_id: UUID
    ) -> dict:
        """User views a story — idempotent."""
        story = story_crud.get(db, id=story_id)
        if not story:
            raise NotFoundException("Story not found")

        view = story_view_crud.record_view(
            db, story_id=story_id, viewer_id=viewer_id
        )

        if view:  # Only increment on first view
            story_crud.increment_view_count(db, story_id=story_id)

        db.commit()
        return {"viewed": view is not None}

    def update_story(
        self, db: Session, *, story_id: UUID, obj_in: StoryUpdate, user: User
    ) -> dict:
        story = story_crud.get(db, id=story_id)
        if not story:
            raise NotFoundException("Story not found")

        business = business_crud.get(db, id=story.business_id)
        if not business or business.user_id != user.id:
            raise PermissionDeniedException("You don't own this story")

        updated = story_crud.update(db, db_obj=story, obj_in=obj_in)
        db.commit()
        db.refresh(updated)
        return updated

    def delete_story(self, db: Session, *, story_id: UUID, user: User) -> None:
        story = story_crud.get(db, id=story_id)
        if not story:
            raise NotFoundException("Story not found")

        business = business_crud.get(db, id=story.business_id)
        if not business or business.user_id != user.id:
            raise PermissionDeniedException("You don't own this story")

        story_crud.remove(db, id=story_id)
        db.commit()


story_service = StoryService()