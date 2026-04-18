"""
app/services/story_service.py

FIXES:
  [AUDIT BUG-8] create_story() now enforces business.is_verified at the
  SERVICE LAYER — not just at the router dependency level.

  Root cause of original bug:
    The router (stories.py) correctly uses require_verified_business.
    However the SERVICE LAYER only checked ownership (business.user_id != user.id).
    Blueprint §8.5 HARD RULE must be enforced at every call site for
    defense-in-depth because:
      1. Celery tasks can call services directly, bypassing router dependencies.
      2. Admin endpoints / internal tools may call services with different
         dependency chains.
      3. A future developer could add a new route using require_business
         (not require_verified_business) and silently bypass the gate.

  Blueprint §8.5 HARD RULE:
    "Only VERIFIED businesses may post stories. Unverified businesses see
     these features LOCKED with a clear verification prompt — NOT a silent
     blank state."

  get_feed() uses lat/lng/radius_meters (radius-only). No LGA anywhere.
  Blueprint §4 HARD RULE: "No LGA logic anywhere in the codebase."
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.crud.stories_crud import story_crud, story_view_crud
from app.models.user_model import User
from app.schemas.stories_schema import StoryCreate, StoryUpdate
from app.core.exceptions import NotFoundException, PermissionDeniedException
from app.crud.business_crud import business_crud
from app.core.constants import DEFAULT_RADIUS_METERS


def _utcnow() -> datetime:
    """Blueprint §16.4 HARD RULE: always timezone-aware UTC."""
    return datetime.now(timezone.utc)


class StoryService:

    def create_story(
        self, db: Session, *, business_id: UUID, obj_in: StoryCreate, user: User
    ) -> dict:
        """
        Business owner creates a story.

        Blueprint §8.5 HARD RULE (enforced at SERVICE LAYER):
          "Only VERIFIED businesses may post stories."

        Blueprint §8.5: expires_at = created_at + 24 hours (server-side, UTC-aware).
        Blueprint §16.4: datetime.now(timezone.utc) — NEVER datetime.utcnow().
        """
        business = business_crud.get(db, id=business_id)
        if not business or business.user_id != user.id:
            raise PermissionDeniedException("You don't own this business.")

        # [BUG-8 FIX] — Blueprint §8.5 HARD RULE: verified businesses only.
        # This check is here in addition to the router's require_verified_business
        # dependency to prevent bypass from any call site that doesn't use the router.
        if not business.is_verified:
            raise PermissionDeniedException(
                "Your business must be verified by an admin before you can post "
                "stories. Complete your profile and await admin review."
            )

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

        # Blueprint §8.5: only active, non-expired stories are visible
        if not story.is_active:
            raise NotFoundException("Story not found")
        if story.expires_at and story.expires_at < _utcnow():
            raise NotFoundException("Story has expired")

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

        Blueprint §4 HARD RULE: GPS-only. No LGA parameter exists.
        Blueprint §7.3: businesses with unseen stories first, then subscription tier.
          Enterprise > Pro > Starter > Free.
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
        """
        User views a story — idempotent.

        Blueprint §8.5: viewer list visible to business owner.
        story_views table: story_id, viewer_user_id, viewed_at.
        """
        story = story_crud.get(db, id=story_id)
        if not story:
            raise NotFoundException("Story not found")

        # Do not record views on expired / inactive stories
        if not story.is_active or (story.expires_at and story.expires_at < _utcnow()):
            return {"viewed": False}

        view = story_view_crud.record_view(
            db, story_id=story_id, viewer_id=viewer_id
        )

        if view:  # Only increment on first view (idempotent)
            story_crud.increment_view_count(db, story_id=story_id)

        db.commit()
        return {"viewed": view is not None}

    def get_story_viewers(
        self,
        db: Session,
        *,
        story_id: UUID,
        business_user: User,
        skip: int = 0,
        limit: int = 50,
    ) -> dict:
        """
        Return viewer list for a story.
        Blueprint §8.5: "Viewer list visible to business owner: who viewed and when."
        Only the owning business may call this endpoint.
        """
        story = story_crud.get(db, id=story_id)
        if not story:
            raise NotFoundException("Story not found")

        business = business_crud.get(db, id=story.business_id)
        if not business or business.user_id != business_user.id:
            raise PermissionDeniedException("You don't own this story.")

        viewers, total = story_view_crud.get_viewers(
            db, story_id=story_id, skip=skip, limit=limit
        )
        return {"viewers": viewers, "total": total}

    def update_story(
        self, db: Session, *, story_id: UUID, obj_in: StoryUpdate, user: User
    ) -> dict:
        story = story_crud.get(db, id=story_id)
        if not story:
            raise NotFoundException("Story not found")

        business = business_crud.get(db, id=story.business_id)
        if not business or business.user_id != user.id:
            raise PermissionDeniedException("You don't own this story.")

        # Blueprint §8.5: pinned_until — Enterprise only
        update_data = obj_in.model_dump(exclude_unset=True)
        if "pinned_until" in update_data and update_data["pinned_until"] is not None:
            tier_val = (
                business.subscription_tier.value
                if hasattr(business.subscription_tier, "value")
                else str(business.subscription_tier or "free")
            )
            if tier_val != "enterprise":
                raise PermissionDeniedException(
                    "Pinning stories is available on the Enterprise plan only."
                )

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
            raise PermissionDeniedException("You don't own this story.")

        story_crud.remove(db, id=story_id)
        db.commit()


story_service = StoryService()