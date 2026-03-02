from sqlalchemy import (
    Column, String, Boolean, Integer, Text,
    ForeignKey, UniqueConstraint, CheckConstraint, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
import enum

from app.models.base_model import BaseModel


# ============================================
# ENUMS
# ============================================

class ReviewableTypeEnum(str, enum.Enum):
    HOTEL = "hotel"
    PRODUCT = "product"
    SERVICE = "service"
    RESTAURANT = "restaurant"
    RIDER = "rider"
    DOCTOR = "doctor"
    PROPERTY = "property"


class ReviewStatusEnum(str, enum.Enum):
    PENDING = "pending"       # Awaiting moderation
    APPROVED = "approved"     # Live
    FLAGGED = "flagged"       # Under review
    REMOVED = "removed"       # Moderation removed


class ReviewContextEnum(str, enum.Enum):
    """The transaction that earned the right to review"""
    HOTEL_BOOKING = "hotel_booking"
    PRODUCT_ORDER = "product_order"
    SERVICE_BOOKING = "service_booking"
    FOOD_ORDER = "food_order"
    DELIVERY = "delivery"
    CONSULTATION = "consultation"
    PROPERTY_VIEWING = "property_viewing"


# ============================================
# REVIEW MODEL
# ============================================

class Review(BaseModel):
    """
    Polymorphic review — one table across all modules.
    reviewable_type + reviewable_id together identify the target entity.
    context_type + context_id identify the completed transaction that
    gives the reviewer the right to leave this review (enforced at service layer).
    """

    __tablename__ = "reviews"

    # --- Who & What ---
    reviewer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Polymorphic target
    reviewable_type = Column(String(30), nullable=False, index=True)   # ReviewableTypeEnum
    reviewable_id   = Column(UUID(as_uuid=True), nullable=False, index=True)

    # Transaction that earned this review
    context_type = Column(String(40), nullable=False)   # ReviewContextEnum
    context_id   = Column(UUID(as_uuid=True), nullable=False, index=True)

    # --- Content ---
    rating = Column(
        Integer,
        CheckConstraint("rating >= 1 AND rating <= 5"),
        nullable=False
    )
    # Per-dimension scores (JSONB, keys vary by reviewable_type)
    # hotel  → {cleanliness, service, location, value}
    # food   → {food_quality, service, ambiance, value}
    # doctor → {expertise, communication, timeliness}
    # others → {} (overall only)
    rating_breakdown = Column(JSONB, default=dict)

    title   = Column(String(200), nullable=True)
    body    = Column(Text, nullable=True)
    photos  = Column(JSONB, default=list)    # [{url, width, height}]

    # --- Moderation ---
    status = Column(String(20), default=ReviewStatusEnum.PENDING)  # auto-approved for verified purchases
    is_flagged = Column(Boolean, default=False)
    flag_reason = Column(Text, nullable=True)
    moderated_by_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )

    # --- Engagement ---
    helpful_count   = Column(Integer, default=0)     # denormalized
    unhelpful_count = Column(Integer, default=0)

    # --- Relationships ---
    reviewer         = relationship("User", foreign_keys=[reviewer_id])
    moderator        = relationship("User", foreign_keys=[moderated_by_id])
    helpful_votes    = relationship("ReviewHelpfulVote", back_populates="review", cascade="all, delete-orphan")
    response         = relationship("ReviewResponse", back_populates="review", uselist=False, cascade="all, delete-orphan")

    # --- Constraints ---
    __table_args__ = (
        # One review per (reviewer, reviewable, transaction)
        UniqueConstraint("reviewer_id", "reviewable_type", "reviewable_id", "context_id",
                         name="uq_review_per_transaction"),
        # Composite index for listing reviews of a target
        Index("ix_reviewable", "reviewable_type", "reviewable_id"),
        # Composite index for filtering by reviewer
        Index("ix_reviewer_reviews", "reviewer_id", "reviewable_type"),
    )


# ============================================
# HELPFUL VOTE MODEL
# ============================================

class ReviewHelpfulVote(BaseModel):
    """Tracks whether a review was helpful / unhelpful per user."""

    __tablename__ = "review_helpful_votes"

    review_id = Column(
        UUID(as_uuid=True),
        ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    voter_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False
    )
    is_helpful = Column(Boolean, nullable=False)  # True=helpful, False=unhelpful

    # Relationships
    review = relationship("Review", back_populates="helpful_votes")
    voter  = relationship("User")

    __table_args__ = (
        UniqueConstraint("review_id", "voter_id", name="uq_vote_per_user"),
    )


# ============================================
# BUSINESS RESPONSE MODEL
# ============================================

class ReviewResponse(BaseModel):
    """
    Business owner's reply to a review.
    One response per review (can be updated).
    """

    __tablename__ = "review_responses"

    review_id = Column(
        UUID(as_uuid=True),
        ForeignKey("reviews.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True
    )
    responder_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False
    )
    body = Column(Text, nullable=False)

    # Relationships
    review    = relationship("Review", back_populates="response")
    responder = relationship("User")