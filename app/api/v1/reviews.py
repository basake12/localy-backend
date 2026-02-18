from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from uuid import UUID

from app.core.database import get_db
from app.dependencies import get_current_user, get_current_active_user, require_admin
from app.models.user import User
from app.schemas.reviews import (
    ReviewCreate, ReviewUpdate, ReviewOut, ReviewListOut,
    HelpfulVoteCreate, HelpfulVoteOut,
    ReviewResponseCreate, ReviewResponseUpdate, ReviewResponseOut,
    ReviewFlagCreate,
    ReviewModerationUpdate,
)
from app.services.review_service import (
    review_service,
    helpful_vote_service,
    review_response_service,
    moderation_service,
)

router = APIRouter()


# ============================================
# REVIEWS — CRUD
# ============================================

@router.post("", response_model=ReviewOut, status_code=201)
def create_review(
    payload: ReviewCreate,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_active_user),
):
    """
    Submit a review.  Requires a completed transaction (context_id) with the target entity.
    Rating breakdown keys are validated against the reviewable_type.
    """
    review = review_service.create_review(db, reviewer=user, payload=payload)
    return review


@router.get("/{review_id}", response_model=ReviewOut)
def get_review(
    review_id: UUID,
    db: Session = Depends(get_db),
    _:  User    = Depends(get_current_user),      # auth required, any status
):
    """Fetch a single review by ID."""
    return review_service.get_review(db, review_id=review_id)


@router.put("/{review_id}", response_model=ReviewOut)
def update_review(
    review_id: UUID,
    payload:   ReviewUpdate,
    db:        Session = Depends(get_db),
    user:      User    = Depends(get_current_active_user),
):
    """Update your own review."""
    return review_service.update_review(db, reviewer=user, review_id=review_id, payload=payload)


@router.delete("/{review_id}", status_code=204)
def delete_review(
    review_id: UUID,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_active_user),
):
    """Soft-delete your own review."""
    review_service.delete_review(db, reviewer=user, review_id=review_id)


# ============================================
# REVIEWS — LIST (per entity)
# ============================================

@router.get("/entity/{reviewable_type}/{reviewable_id}", response_model=ReviewListOut)
def list_reviews_for_entity(
    reviewable_type: str,
    reviewable_id:   UUID,
    sort_by:  str = Query("created_at", enum=["created_at", "rating", "helpful_count"]),
    sort_dir: str = Query("desc",        enum=["asc", "desc"]),
    skip:     int = Query(0,  ge=0),
    limit:    int = Query(20, ge=1, le=100),
    db: Session   = Depends(get_db),
):
    """
    List + aggregate stats for a reviewable entity.
    No auth required — public listing.
    """
    return review_service.list_reviews(
        db,
        reviewable_type=reviewable_type,
        reviewable_id=reviewable_id,
        sort_by=sort_by,
        sort_dir=sort_dir,
        skip=skip,
        limit=limit,
    )


# ============================================
# REVIEWS — MY REVIEWS
# ============================================

@router.get("/mine", response_model=list[ReviewOut])
def list_my_reviews(
    reviewable_type: str | None = Query(None),
    skip:  int = Query(0,  ge=0),
    limit: int = Query(20, ge=1, le=100),
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_active_user),
):
    """All reviews the current user has left."""
    return review_service.list_my_reviews(
        db,
        reviewer_id=user.id,
        reviewable_type=reviewable_type,
        skip=skip,
        limit=limit,
    )


# ============================================
# REVIEWS — FLAG
# ============================================

@router.post("/{review_id}/flag", response_model=ReviewOut)
def flag_review(
    review_id: UUID,
    payload:   ReviewFlagCreate,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_active_user),
):
    """Flag a review for moderation. Cannot flag your own."""
    return review_service.flag_review(db, reviewer=user, review_id=review_id, payload=payload)


# ============================================
# HELPFUL VOTES
# ============================================

@router.post("/{review_id}/vote", response_model=HelpfulVoteOut)
def vote_helpful(
    review_id: UUID,
    payload:   HelpfulVoteCreate,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_active_user),
):
    """
    Toggle helpful / unhelpful vote on a review.
    Posting the same value again removes the vote (toggle off).
    Cannot vote on your own reviews.
    """
    return helpful_vote_service.vote(db, voter=user, review_id=review_id, payload=payload)


# ============================================
# BUSINESS RESPONSE
# ============================================

@router.post("/{review_id}/response", response_model=ReviewResponseOut, status_code=201)
def create_response(
    review_id: UUID,
    payload:   ReviewResponseCreate,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_active_user),
):
    """Business owner posts a reply to a review. One response per review."""
    return review_response_service.create_response(db, review_id=review_id, responder=user, payload=payload)


@router.put("/{review_id}/response", response_model=ReviewResponseOut)
def update_response(
    review_id: UUID,
    payload:   ReviewResponseUpdate,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_active_user),
):
    """Update existing business response."""
    return review_response_service.update_response(db, review_id=review_id, responder=user, payload=payload)


@router.delete("/{review_id}/response", status_code=204)
def delete_response(
    review_id: UUID,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_active_user),
):
    """Delete business response."""
    review_response_service.delete_response(db, review_id=review_id, responder=user)


# ============================================
# MODERATION  (admin)
# ============================================

@router.put("/admin/{review_id}/moderate", response_model=ReviewOut)
def moderate_review(
    review_id: UUID,
    payload:   ReviewModerationUpdate,
    db:   Session = Depends(get_db),
    admin: User   = Depends(require_admin),
):
    """Approve or remove a flagged review. Admin only."""
    return moderation_service.moderate(db, moderator=admin, review_id=review_id, payload=payload)