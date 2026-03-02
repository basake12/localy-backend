from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict
from datetime import datetime
from uuid import UUID


# ============================================
# ENUMS (re-exported for schema use)
# ============================================

REVIEWABLE_TYPES = {"hotel", "product", "service", "restaurant", "rider", "doctor", "property"}

RATING_BREAKDOWN_KEYS = {
    "hotel":      {"cleanliness", "service", "location", "value"},
    "restaurant": {"food_quality", "service", "ambiance", "value"},
    "doctor":     {"expertise", "communication", "timeliness"},
    "product":    {"quality", "value", "delivery"},
    "service":    {"quality", "punctuality", "value"},
    "rider":      {"punctuality", "politeness", "safety"},
    "property":   {"accuracy", "value", "agent_service"},
}


# ============================================
# CREATE
# ============================================

class ReviewCreate(BaseModel):
    reviewable_type: str
    reviewable_id:   UUID
    context_type:    str       # the transaction type
    context_id:      UUID      # the transaction ID
    rating:          int       = Field(..., ge=1, le=5)
    rating_breakdown: Optional[Dict[str, int]] = None
    title:           Optional[str] = Field(None, max_length=200)
    body:            Optional[str] = None
    photos:          Optional[List[Dict]] = None   # [{url, width, height}]

    @field_validator("reviewable_type")
    @classmethod
    def validate_reviewable_type(cls, v: str) -> str:
        if v not in REVIEWABLE_TYPES:
            raise ValueError(f"reviewable_type must be one of {sorted(REVIEWABLE_TYPES)}")
        return v

    @field_validator("rating_breakdown")
    @classmethod
    def validate_breakdown(cls, v, info):
        if v is None:
            return v
        # All values must be 1-5
        for key, val in v.items():
            if not (1 <= val <= 5):
                raise ValueError(f"rating_breakdown.{key} must be between 1 and 5")
        return v


# ============================================
# UPDATE
# ============================================

class ReviewUpdate(BaseModel):
    rating:          Optional[int]              = Field(None, ge=1, le=5)
    rating_breakdown: Optional[Dict[str, int]] = None
    title:           Optional[str]              = Field(None, max_length=200)
    body:            Optional[str]              = None
    photos:          Optional[List[Dict]]       = None


# ============================================
# RESPONSE — INDIVIDUAL
# ============================================

class ReviewerOut(BaseModel):
    id:           UUID
    full_name:    str
    avatar_url:   Optional[str] = None

    class Config:
        from_attributes = True


class ReviewResponseOut(BaseModel):
    id:            UUID
    responder_id:  UUID
    body:          str
    created_at:    datetime
    updated_at:    datetime

    class Config:
        from_attributes = True


class ReviewOut(BaseModel):
    id:               UUID
    reviewer:         ReviewerOut
    reviewable_type:  str
    reviewable_id:    UUID
    rating:           int
    rating_breakdown: Optional[Dict[str, int]] = None
    title:            Optional[str] = None
    body:             Optional[str] = None
    photos:           Optional[List[Dict]] = None
    status:           str
    helpful_count:    int
    unhelpful_count:  int
    response:         Optional[ReviewResponseOut] = None
    created_at:       datetime
    updated_at:       datetime

    class Config:
        from_attributes = True


# ============================================
# RESPONSE — LIST / AGGREGATE
# ============================================

class RatingStats(BaseModel):
    """Aggregate stats for a reviewable entity"""
    average_rating:   float
    total_reviews:    int
    rating_breakdown_avg: Optional[Dict[str, float]] = None
    distribution:     Dict[str, int]   # {"1": n, "2": n, ... "5": n}


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
    review_id:    UUID
    voter_id:     UUID
    is_helpful:   bool
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
    status: str   # approved | removed