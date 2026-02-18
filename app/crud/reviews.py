from typing import Optional, List, Dict
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, func, case, distinct
from sqlalchemy.dialects.postgresql import array_agg
from uuid import UUID
from decimal import Decimal

from app.models.reviews import (
    Review, ReviewHelpfulVote, ReviewResponse,
    ReviewStatusEnum, ReviewableTypeEnum
)
from app.core.exceptions import (
    NotFoundException,
    AlreadyExistsException,
    ValidationException,
    PermissionDeniedException,
)


# ============================================
# REVIEW CRUD
# ============================================

class CRUDReview:

    # ---------- fetch ----------

    def get(self, db: Session, *, review_id: UUID) -> Optional[Review]:
        return (
            db.query(Review)
            .options(
                joinedload(Review.reviewer),
                joinedload(Review.response).joinedload(ReviewResponse.responder),
            )
            .filter(Review.id == review_id)
            .first()
        )

    def get_by_context(
        self, db: Session, *, reviewer_id: UUID,
        reviewable_type: str, reviewable_id: UUID, context_id: UUID
    ) -> Optional[Review]:
        """Check if this reviewer already left a review for this transaction."""
        return (
            db.query(Review)
            .filter(
                Review.reviewer_id   == reviewer_id,
                Review.reviewable_type == reviewable_type,
                Review.reviewable_id == reviewable_id,
                Review.context_id    == context_id,
            )
            .first()
        )

    def list_for_reviewable(
        self,
        db: Session,
        *,
        reviewable_type: str,
        reviewable_id: UUID,
        status: Optional[str] = ReviewStatusEnum.APPROVED,
        sort_by: str = "created_at",       # created_at | rating | helpful_count
        sort_dir: str = "desc",
        skip: int = 0,
        limit: int = 20,
    ) -> List[Review]:
        query = (
            db.query(Review)
            .options(
                joinedload(Review.reviewer),
                joinedload(Review.response).joinedload(ReviewResponse.responder),
            )
            .filter(
                Review.reviewable_type == reviewable_type,
                Review.reviewable_id   == reviewable_id,
            )
        )
        if status:
            query = query.filter(Review.status == status)

        order_col = getattr(Review, sort_by, Review.created_at)
        order = order_col.desc() if sort_dir == "desc" else order_col.asc()

        return query.order_by(order).offset(skip).limit(limit).all()

    def list_by_reviewer(
        self,
        db: Session,
        *,
        reviewer_id: UUID,
        reviewable_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> List[Review]:
        query = (
            db.query(Review)
            .options(joinedload(Review.reviewer), joinedload(Review.response))
            .filter(Review.reviewer_id == reviewer_id)
        )
        if reviewable_type:
            query = query.filter(Review.reviewable_type == reviewable_type)
        return query.order_by(Review.created_at.desc()).offset(skip).limit(limit).all()

    def count_for_reviewable(
        self, db: Session, *,
        reviewable_type: str,
        reviewable_id: UUID,
        status: Optional[str] = ReviewStatusEnum.APPROVED,
    ) -> int:
        query = db.query(func.count(Review.id)).filter(
            Review.reviewable_type == reviewable_type,
            Review.reviewable_id   == reviewable_id,
        )
        if status:
            query = query.filter(Review.status == status)
        return query.scalar() or 0

    # ---------- aggregate stats ----------

    def get_rating_stats(
        self, db: Session, *,
        reviewable_type: str,
        reviewable_id: UUID,
    ) -> Dict:
        """
        Returns {average_rating, total_reviews, distribution, rating_breakdown_avg}
        Only counts APPROVED reviews.
        """
        base = db.query(Review).filter(
            Review.reviewable_type == reviewable_type,
            Review.reviewable_id   == reviewable_id,
            Review.status          == ReviewStatusEnum.APPROVED,
        )

        # Distribution: count per star 1-5
        distribution_row = (
            db.query(
                Review.rating,
                func.count(Review.id).label("cnt"),
            )
            .filter(
                Review.reviewable_type == reviewable_type,
                Review.reviewable_id   == reviewable_id,
                Review.status          == ReviewStatusEnum.APPROVED,
            )
            .group_by(Review.rating)
            .all()
        )
        distribution = {str(i): 0 for i in range(1, 6)}
        for row in distribution_row:
            distribution[str(row.rating)] = row.cnt

        # Avg overall
        agg = base.with_entities(
            func.avg(Review.rating).label("avg_rating"),
            func.count(Review.id).label("total"),
        ).one()

        avg_rating = float(agg.avg_rating) if agg.avg_rating else 0.0
        total      = agg.total or 0

        # Breakdown avg — pull all reviews and compute in Python (JSONB keys are dynamic)
        breakdown_avg: Optional[Dict[str, float]] = None
        if total > 0:
            reviews = base.with_entities(Review.rating_breakdown).all()
            breakdown_sums: Dict[str, float] = {}
            breakdown_counts: Dict[str, int] = {}
            for (rb,) in reviews:
                if not rb:
                    continue
                for k, v in rb.items():
                    breakdown_sums[k]   = breakdown_sums.get(k, 0.0) + v
                    breakdown_counts[k] = breakdown_counts.get(k, 0) + 1
            if breakdown_sums:
                breakdown_avg = {
                    k: round(breakdown_sums[k] / breakdown_counts[k], 2)
                    for k in breakdown_sums
                }

        return {
            "average_rating":       round(avg_rating, 2),
            "total_reviews":        total,
            "distribution":         distribution,
            "rating_breakdown_avg": breakdown_avg,
        }

    # ---------- write ----------

    def create(self, db: Session, *, review_in: dict) -> Review:
        review = Review(**review_in)
        db.add(review)
        db.flush()
        db.refresh(review)
        return review

    def update(self, db: Session, *, review: Review, update_data: dict) -> Review:
        for k, v in update_data.items():
            if v is not None:
                setattr(review, k, v)
        db.flush()
        db.refresh(review)
        return review

    def delete(self, db: Session, *, review: Review) -> None:
        """Soft-delete: set status to REMOVED, preserve data for stats history."""
        review.status = ReviewStatusEnum.REMOVED
        db.flush()

    # ---------- flag / moderate ----------

    def flag(self, db: Session, *, review: Review, reason: str) -> Review:
        review.is_flagged  = True
        review.flag_reason = reason
        review.status      = ReviewStatusEnum.FLAGGED
        db.flush()
        return review

    def moderate(self, db: Session, *, review: Review, status: str, moderator_id: UUID) -> Review:
        review.status        = status
        review.is_flagged    = (status == ReviewStatusEnum.FLAGGED)
        review.moderated_by_id = moderator_id
        db.flush()
        return review


# ============================================
# HELPFUL VOTE CRUD
# ============================================

class CRUDHelpfulVote:

    def get(self, db: Session, *, review_id: UUID, voter_id: UUID) -> Optional[ReviewHelpfulVote]:
        return (
            db.query(ReviewHelpfulVote)
            .filter(
                ReviewHelpfulVote.review_id == review_id,
                ReviewHelpfulVote.voter_id  == voter_id,
            )
            .first()
        )

    def upsert(
        self, db: Session, *,
        review: Review,
        voter_id: UUID,
        is_helpful: bool,
    ) -> ReviewHelpfulVote:
        existing = self.get(db, review_id=review.id, voter_id=voter_id)

        if existing:
            # Undo old vote on counts
            if existing.is_helpful:
                review.helpful_count -= 1
            else:
                review.unhelpful_count -= 1

            if existing.is_helpful == is_helpful:
                # Toggle off — remove vote entirely
                db.delete(existing)
                db.flush()
                db.refresh(review)
                return None  # vote removed

            existing.is_helpful = is_helpful
            vote = existing
        else:
            vote = ReviewHelpfulVote(
                review_id  = review.id,
                voter_id   = voter_id,
                is_helpful = is_helpful,
            )
            db.add(vote)

        # Apply new vote on counts
        if is_helpful:
            review.helpful_count += 1
        else:
            review.unhelpful_count += 1

        db.flush()
        db.refresh(review)
        return vote


# ============================================
# REVIEW RESPONSE (business reply) CRUD
# ============================================

class CRUDReviewResponse:

    def get(self, db: Session, *, review_id: UUID) -> Optional[ReviewResponse]:
        return (
            db.query(ReviewResponse)
            .options(joinedload(ReviewResponse.responder))
            .filter(ReviewResponse.review_id == review_id)
            .first()
        )

    def create(self, db: Session, *, review_id: UUID, responder_id: UUID, body: str) -> ReviewResponse:
        resp = ReviewResponse(
            review_id    = review_id,
            responder_id = responder_id,
            body         = body,
        )
        db.add(resp)
        db.flush()
        db.refresh(resp)
        return resp

    def update(self, db: Session, *, response: ReviewResponse, body: str) -> ReviewResponse:
        response.body = body
        db.flush()
        db.refresh(response)
        return response

    def delete(self, db: Session, *, response: ReviewResponse) -> None:
        db.delete(response)
        db.flush()


# ============================================
# SINGLETONS
# ============================================

review_crud         = CRUDReview()
helpful_vote_crud   = CRUDHelpfulVote()
review_response_crud = CRUDReviewResponse()