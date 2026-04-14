from __future__ import annotations

from typing import Literal, Optional, List, Dict
from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import datetime
from uuid import UUID


# ============================================
# CONSTANTS
# ============================================

REVIEWABLE_TYPES = frozenset(
    {"hotel", "product", "service", "restaurant", "rider", "doctor", "property"}
)

CONTEXT_TYPES = frozenset({
    "hotel_booking", "product_order", "service_booking",
    "food_order", "delivery", "consultation", "property_viewing",
})

# FIX: was duplicated at the bottom of the file as a list[str] which shadowed this dict,
# breaking review_service.py which calls RATING_BREAKDOWN_KEYS.get(reviewable_type, set()).
RATING_BREAKDOWN_KEYS: Dict[str, frozenset] = {
    "hotel":      frozenset({"cleanliness", "service", "location", "value"}),
    "restaurant": frozenset({"food_quality", "service", "ambiance", "value"}),
    "doctor":     frozenset({"expertise", "communication", "timeliness"}),
    "product":    frozenset({"quality", "value", "delivery"}),
    "service":    frozenset({"quality", "punctuality", "value"}),
    "rider":      frozenset({"punctuality", "politeness", "safety"}),
    "property":   frozenset({"accuracy", "value", "agent_service"}),
}


# ============================================
# CREATE
# ============================================

class ReviewCreate(BaseModel):
    reviewable_type:  str
    reviewable_id:    UUID
    context_type:     str
    context_id:       UUID
    rating:           int                       = Field(..., ge=1, le=5)
    rating_breakdown: Optional[Dict[str, int]]  = None
    title:            Optional[str]             = Field(None, max_length=200)
    body:             Optional[str]             = Field(None, max_length=5000)
    photos:           Optional[List[Dict]]      = None   # [{url, width, height}]

    @field_validator("reviewable_type")
    @classmethod
    def validate_reviewable_type(cls, v: str) -> str:
        if v not in REVIEWABLE_TYPES:
            raise ValueError(f"reviewable_type must be one of {sorted(REVIEWABLE_TYPES)}")
        return v

    @field_validator("context_type")
    @classmethod
    def validate_context_type(cls, v: str) -> str:
        if v not in CONTEXT_TYPES:
            raise ValueError(f"context_type must be one of {sorted(CONTEXT_TYPES)}")
        return v

    @model_validator(mode="after")
    def validate_breakdown_keys(self) -> "ReviewCreate":
        """Validate that breakdown keys are valid for the given reviewable_type."""
        if self.rating_breakdown is None:
            return self
        allowed = RATING_BREAKDOWN_KEYS.get(self.reviewable_type, frozenset())
        for key, val in self.rating_breakdown.items():
            if key not in allowed:
                raise ValueError(
                    f"Invalid breakdown key '{key}' for {self.reviewable_type}. "
                    f"Allowed keys: {sorted(allowed)}"
                )
            if not (1 <= val <= 5):
                raise ValueError(f"rating_breakdown['{key}'] must be between 1 and 5")
        return self


# ============================================
# UPDATE
# ============================================

class ReviewUpdate(BaseModel):
    rating:           Optional[int]             = Field(None, ge=1, le=5)
    rating_breakdown: Optional[Dict[str, int]]  = None
    title:            Optional[str]             = Field(None, max_length=200)
    body:             Optional[str]             = Field(None, max_length=5000)
    photos:           Optional[List[Dict]]      = None


# ============================================
# RESPONSE — INDIVIDUAL
# ============================================

class ReviewerOut(BaseModel):
    id:         UUID
    full_name:  str
    avatar_url: Optional[str] = None

    model_config = {"from_attributes": True}


class ReviewResponseOut(BaseModel):
    id:           UUID
    responder_id: UUID
    body:         str
    created_at:   datetime
    updated_at:   datetime

    model_config = {"from_attributes": True}


class ReviewOut(BaseModel):
    id:               UUID
    reviewer:         ReviewerOut
    reviewable_type:  str
    reviewable_id:    UUID
    rating:           int
    rating_breakdown: Optional[Dict[str, int]] = None
    title:            Optional[str]            = None
    body:             Optional[str]            = None
    photos:           Optional[List[Dict]]     = None
    status:           str
    is_flagged:       bool
    helpful_count:    int
    unhelpful_count:  int
    response:         Optional[ReviewResponseOut] = None
    created_at:       datetime
    updated_at:       datetime

    model_config = {"from_attributes": True}


# ============================================
# RESPONSE — LIST / AGGREGATE
# ============================================

class RatingStats(BaseModel):
    average_rating:       float
    total_reviews:        int
    rating_breakdown_avg: Optional[Dict[str, float]] = None
    distribution:         Dict[str, int]   # {"1": n, "2": n, ... "5": n}


class ReviewListOut(BaseModel):
    stats:   RatingStats
    reviews: List[ReviewOut]
    total:   int
    skip:    int
    limit:   int


# ============================================
# HELPFUL VOTE
# ============================================

class HelpfulVoteCreate(BaseModel):
    is_helpful: bool


class HelpfulVoteOut(BaseModel):
    review_id:      UUID
    voter_id:       UUID
    is_helpful:     Optional[bool]  # None = vote was toggled off
    helpful_count:    int
    unhelpful_count:  int


# ============================================
# BUSINESS RESPONSE
# ============================================

class ReviewResponseCreate(BaseModel):
    body: str = Field(..., min_length=5, max_length=2000)


class ReviewResponseUpdate(BaseModel):
    body: str = Field(..., min_length=5, max_length=2000)


# ============================================
# FLAG
# ============================================

class ReviewFlagCreate(BaseModel):
    reason: str = Field(..., min_length=10, max_length=500)


# ============================================
# MODERATION (admin)
# ============================================

class ReviewModerationUpdate(BaseModel):
    status: Literal["approved", "removed"]   # strict — no other values accepted
    moderator_note: Optional[str] = Field(None, max_length=1000)