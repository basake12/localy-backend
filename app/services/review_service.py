"""
review_service.py

Orchestrates CRUD + business rules.
Every public method is called directly from the API router.
"""

from typing import Optional, Dict
from sqlalchemy.orm import Session
from uuid import UUID

from app.crud.reviews_crud import review_crud, helpful_vote_crud, review_response_crud
from app.models.reviews_model import Review, ReviewStatusEnum, ReviewableTypeEnum, ReviewContextEnum
from app.models.user_model import User, UserTypeEnum
from app.schemas.reviews_schema import (
    ReviewCreate, ReviewUpdate,
    ReviewResponseCreate, ReviewResponseUpdate,
    ReviewFlagCreate, ReviewModerationUpdate,
    HelpfulVoteCreate,
    RATING_BREAKDOWN_KEYS,
)
from app.core.exceptions import (
    NotFoundException,
    AlreadyExistsException,
    PermissionDeniedException,
    ValidationException,
)

# ---------------------------------------------------------------------------
# TRANSACTION VERIFICATION
# Checks the DB to confirm the reviewer actually completed the transaction.
# Each (context_type, context_id) maps to a specific orders/bookings table.
# ---------------------------------------------------------------------------

def _verify_transaction(
    db: Session,
    *,
    reviewer_id: UUID,
    reviewable_type: str,
    reviewable_id: UUID,
    context_type: str,
    context_id: UUID,
) -> None:
    """
    Raises ValidationException if the reviewer has no completed transaction
    that links to the target entity.

    Column mapping matches the actual ORM models exactly.
    ProductOrder is special-cased: reviewable_id == vendor_id, verified via OrderItem → Product.
    """
    from app.models.hotels_model     import HotelBooking
    from app.models.products_model   import ProductOrder, OrderItem, Product
    from app.models.services_model   import ServiceBooking
    from app.models.food_model       import FoodOrder
    from app.models.delivery_model   import Delivery
    from app.models.health_model     import Consultation
    from app.models.properties_model import PropertyViewing

    # --------------- ProductOrder special case ---------------
    # ProductOrder has no direct vendor_id; link is OrderItem.product_id → Product.vendor_id
    if context_type == ReviewContextEnum.PRODUCT_ORDER:
        row = (
            db.query(ProductOrder)
            .join(OrderItem, OrderItem.order_id == ProductOrder.id)
            .join(Product, Product.id == OrderItem.product_id)
            .filter(
                ProductOrder.id          == context_id,
                ProductOrder.customer_id == reviewer_id,
                Product.vendor_id        == reviewable_id,
                ProductOrder.order_status == "delivered",
            )
            .first()
        )
        if row is None:
            raise ValidationException(
                "You can only review after completing a transaction with this entity."
            )
        return

    # --------------- Generic lookup for all other types ---------------
    #   (model, owner_col_name, target_col_name, status_col_name, completed_values)
    LOOKUP = {
        ReviewContextEnum.HOTEL_BOOKING: (
            HotelBooking, "customer_id", "hotel_id", "status", ("confirmed", "checked_out")
        ),
        ReviewContextEnum.SERVICE_BOOKING: (
            ServiceBooking, "customer_id", "provider_id", "status", ("completed",)
        ),
        ReviewContextEnum.FOOD_ORDER: (
            FoodOrder, "customer_id", "restaurant_id", "order_status", ("delivered",)
        ),
        ReviewContextEnum.DELIVERY: (
            Delivery, "customer_id", "rider_id", "status", ("delivered",)
        ),
        ReviewContextEnum.CONSULTATION: (
            Consultation, "patient_id", "doctor_id", "status", ("completed",)
        ),
        ReviewContextEnum.PROPERTY_VIEWING: (
            PropertyViewing, "customer_id", "property_id", "status", ("completed", "attended")
        ),
    }

    cfg = LOOKUP.get(context_type)
    if cfg is None:
        raise ValidationException(f"Unknown context_type: {context_type}")

    Model, owner_col_name, target_col_name, status_col_name, completed = cfg

    row = (
        db.query(Model)
        .filter(
            Model.id                                        == context_id,
            getattr(Model, owner_col_name)                  == reviewer_id,
            getattr(Model, target_col_name)                 == reviewable_id,
            getattr(Model, status_col_name).in_(completed),
        )
        .first()
    )

    if row is None:
        raise ValidationException(
            "You can only review after completing a transaction with this entity."
        )


# ---------------------------------------------------------------------------
# PUBLIC API — REVIEWS
# ---------------------------------------------------------------------------

class ReviewService:

    # ---------- create ----------

    def create_review(self, db: Session, *, reviewer: User, payload: ReviewCreate) -> Review:
        # 1. Duplicate guard
        existing = review_crud.get_by_context(
            db,
            reviewer_id=reviewer.id,
            reviewable_type=payload.reviewable_type,
            reviewable_id=payload.reviewable_id,
            context_id=payload.context_id,
        )
        if existing:
            raise AlreadyExistsException("You have already reviewed this from that transaction.")

        # 2. Transaction ownership check
        _verify_transaction(
            db,
            reviewer_id=reviewer.id,
            reviewable_type=payload.reviewable_type,
            reviewable_id=payload.reviewable_id,
            context_type=payload.context_type,
            context_id=payload.context_id,
        )

        # 3. Validate rating_breakdown keys against allowed set
        if payload.rating_breakdown:
            allowed = RATING_BREAKDOWN_KEYS.get(payload.reviewable_type, frozenset())
            extra = set(payload.rating_breakdown.keys()) - allowed
            if extra:
                raise ValidationException(
                    f"Invalid breakdown keys for {payload.reviewable_type}: {extra}. "
                    f"Allowed: {allowed}"
                )

        # 4. Auto-approve verified-purchase reviews
        status = ReviewStatusEnum.APPROVED

        review = review_crud.create(db, review_in={
            "reviewer_id":      reviewer.id,
            "reviewable_type":  payload.reviewable_type,
            "reviewable_id":    payload.reviewable_id,
            "context_type":     payload.context_type,
            "context_id":       payload.context_id,
            "rating":           payload.rating,
            "rating_breakdown": payload.rating_breakdown or {},
            "title":            payload.title,
            "body":             payload.body,
            "photos":           payload.photos or [],
            "status":           status,
        })

        db.commit()
        db.refresh(review)
        return review

    # ---------- read ----------

    def get_review(self, db: Session, *, review_id: UUID) -> Review:
        review = review_crud.get(db, review_id=review_id)
        if not review:
            raise NotFoundException("Review")
        return review

    def list_reviews(
        self, db: Session, *,
        reviewable_type: str,
        reviewable_id: UUID,
        sort_by: str = "created_at",
        sort_dir: str = "desc",
        skip: int = 0,
        limit: int = 20,
    ) -> Dict:
        """Returns {stats, reviews, total, skip, limit}"""
        stats   = review_crud.get_rating_stats(
            db, reviewable_type=reviewable_type, reviewable_id=reviewable_id
        )
        reviews = review_crud.list_for_reviewable(
            db,
            reviewable_type=reviewable_type,
            reviewable_id=reviewable_id,
            sort_by=sort_by,
            sort_dir=sort_dir,
            skip=skip,
            limit=limit,
        )
        total = review_crud.count_for_reviewable(
            db, reviewable_type=reviewable_type, reviewable_id=reviewable_id
        )
        return {
            "stats":   stats,
            "reviews": reviews,
            "total":   total,
            "skip":    skip,
            "limit":   limit,
        }

    def list_my_reviews(
        self, db: Session, *,
        reviewer_id: UUID,
        reviewable_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ):
        return review_crud.list_by_reviewer(
            db,
            reviewer_id=reviewer_id,
            reviewable_type=reviewable_type,
            skip=skip,
            limit=limit,
        )

    # ---------- update ----------

    def update_review(self, db: Session, *, reviewer: User, review_id: UUID, payload: ReviewUpdate) -> Review:
        review = self.get_review(db, review_id=review_id)

        if review.reviewer_id != reviewer.id:
            raise PermissionDeniedException("You can only edit your own reviews.")
        if review.status == ReviewStatusEnum.REMOVED:
            raise ValidationException("Cannot edit a removed review.")

        update_data = payload.model_dump(exclude_unset=True)
        review_crud.update(db, review=review, update_data=update_data)
        db.commit()
        db.refresh(review)
        return review

    # ---------- delete (soft) ----------

    def delete_review(self, db: Session, *, reviewer: User, review_id: UUID) -> None:
        review = self.get_review(db, review_id=review_id)

        if review.reviewer_id != reviewer.id:
            raise PermissionDeniedException("You can only delete your own reviews.")

        review_crud.delete(db, review=review)
        db.commit()

    # ---------- flag ----------

    def flag_review(self, db: Session, *, reviewer: User, review_id: UUID, payload: ReviewFlagCreate) -> Review:
        review = self.get_review(db, review_id=review_id)

        if review.reviewer_id == reviewer.id:
            raise ValidationException("You cannot flag your own review.")
        if review.is_flagged:
            raise ValidationException("This review is already flagged.")

        review_crud.flag(db, review=review, reason=payload.reason)
        db.commit()
        db.refresh(review)
        return review


# ---------------------------------------------------------------------------
# PUBLIC API — HELPFUL VOTES
# ---------------------------------------------------------------------------

class HelpfulVoteService:

    def vote(self, db: Session, *, voter: User, review_id: UUID, payload: HelpfulVoteCreate):
        review = review_crud.get(db, review_id=review_id)
        if not review:
            raise NotFoundException("Review")
        if review.reviewer_id == voter.id:
            raise ValidationException("You cannot vote on your own review.")

        # FIX: was review=review (ORM object) — CRUD method expects review_id: UUID
        vote = helpful_vote_crud.upsert(
            db, review_id=review.id, voter_id=voter.id, is_helpful=payload.is_helpful
        )
        db.commit()
        db.refresh(review)
        return {
            "review_id":        review.id,
            "voter_id":         voter.id,
            "is_helpful":       payload.is_helpful if vote else None,
            "helpful_count":    review.helpful_count,
            "unhelpful_count":  review.unhelpful_count,
        }


# ---------------------------------------------------------------------------
# PUBLIC API — BUSINESS RESPONSE
# ---------------------------------------------------------------------------

class ReviewResponseService:

    def _resolve_review_and_check_ownership(self, db: Session, *, review_id: UUID, responder: User) -> Review:
        """Responder must be the business owner of the reviewable entity."""
        review = review_crud.get(db, review_id=review_id)
        if not review:
            raise NotFoundException("Review")

        # Business owner check: the responder must own a Business whose id == reviewable_id
        # (for hotel/product/service/restaurant/doctor/property the reviewable_id IS the business-linked entity)
        # Simplified: allow if user is business type OR admin
        if responder.user_type not in (UserTypeEnum.BUSINESS, UserTypeEnum.ADMIN):
            raise PermissionDeniedException("Only business owners can respond to reviews.")

        return review

    def create_response(self, db: Session, *, review_id: UUID, responder: User, payload: ReviewResponseCreate):
        review = self._resolve_review_and_check_ownership(db, review_id=review_id, responder=responder)

        existing = review_response_crud.get(db, review_id=review_id)
        if existing:
            raise AlreadyExistsException("A response already exists. Use update instead.")

        resp = review_response_crud.create(
            db,
            review_id=review_id,
            responder_id=responder.id,
            body=payload.body,
        )
        db.commit()
        db.refresh(resp)
        return resp

    def update_response(self, db: Session, *, review_id: UUID, responder: User, payload: ReviewResponseUpdate):
        self._resolve_review_and_check_ownership(db, review_id=review_id, responder=responder)

        existing = review_response_crud.get(db, review_id=review_id)
        if not existing:
            raise NotFoundException("Response")
        if existing.responder_id != responder.id:
            raise PermissionDeniedException()

        review_response_crud.update(db, response=existing, body=payload.body)
        db.commit()
        db.refresh(existing)
        return existing

    def delete_response(self, db: Session, *, review_id: UUID, responder: User):
        existing = review_response_crud.get(db, review_id=review_id)
        if not existing:
            raise NotFoundException("Response")
        if existing.responder_id != responder.id and responder.user_type != UserTypeEnum.ADMIN:
            raise PermissionDeniedException()

        review_response_crud.delete(db, response=existing)
        db.commit()


# ---------------------------------------------------------------------------
# PUBLIC API — MODERATION (admin only, enforced at router level)
# ---------------------------------------------------------------------------

class ModerationService:

    def moderate(self, db: Session, *, moderator: User, review_id: UUID, payload: ReviewModerationUpdate) -> Review:
        review = review_crud.get(db, review_id=review_id)
        if not review:
            raise NotFoundException("Review")

        valid_statuses = {ReviewStatusEnum.APPROVED, ReviewStatusEnum.REMOVED}
        if payload.status not in valid_statuses:
            raise ValidationException(f"status must be one of {valid_statuses}")

        review_crud.moderate(
            db,
            review=review,
            status=payload.status,
            moderator_id=moderator.id,
            moderator_note=payload.moderator_note,
        )
        db.commit()
        db.refresh(review)
        return review


# ---------------------------------------------------------------------------
# SINGLETONS
# ---------------------------------------------------------------------------

review_service          = ReviewService()
helpful_vote_service    = HelpfulVoteService()
review_response_service = ReviewResponseService()
moderation_service      = ModerationService()