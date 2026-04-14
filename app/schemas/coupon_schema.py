from pydantic import BaseModel, field_validator, model_validator
from typing import Optional, List
from decimal import Decimal
from datetime import datetime
from uuid import UUID

from app.models.coupon_model import CouponType, CouponStatus


# ============================================
# BASE
# ============================================

class CouponBase(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    coupon_type: CouponType
    discount_value: Decimal
    max_discount: Optional[Decimal] = None

    # BUY_X_GET_Y
    buy_quantity: Optional[int] = None
    get_quantity: Optional[int] = None

    # BUNDLE — list of product/item IDs that must all be in cart
    bundle_item_ids: List[str] = []

    # Validity
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None

    # Usage limits
    max_uses: Optional[int] = None
    max_uses_per_user: int = 1

    # Restrictions
    min_order_value: Optional[Decimal] = None
    applicable_categories: List[str] = []
    applicable_businesses: List[str] = []
    new_users_only: bool = False
    is_public: bool = True

    @field_validator("code")
    @classmethod
    def code_uppercase(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("discount_value")
    @classmethod
    def discount_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("discount_value must be positive")
        return v

    @model_validator(mode="after")
    def validate_type_fields(self) -> "CouponBase":
        ct = self.coupon_type

        # Percentage cap
        if ct in (CouponType.PERCENTAGE, CouponType.CASHBACK, CouponType.CATEGORY):
            if self.discount_value > 100:
                raise ValueError("Percentage/cashback discount cannot exceed 100")

        # BUY_X_GET_Y requires quantities
        if ct == CouponType.BUY_X_GET_Y:
            if not self.buy_quantity or not self.get_quantity:
                raise ValueError("buy_quantity and get_quantity are required for BUY_X_GET_Y coupons")
            if self.buy_quantity < 1 or self.get_quantity < 1:
                raise ValueError("buy_quantity and get_quantity must be >= 1")

        # BUNDLE requires item list
        if ct == CouponType.BUNDLE and not self.bundle_item_ids:
            raise ValueError("bundle_item_ids must not be empty for BUNDLE coupons")

        # FLASH requires an end_date
        if ct == CouponType.FLASH and not self.end_date:
            raise ValueError("end_date is required for FLASH coupons")

        # CATEGORY requires at least one category
        if ct == CouponType.CATEGORY and not self.applicable_categories:
            raise ValueError("applicable_categories must not be empty for CATEGORY coupons")

        # BUSINESS_SPECIFIC requires a business_id — enforced at create level
        return self

    @model_validator(mode="after")
    def end_after_start(self) -> "CouponBase":
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self


# ============================================
# CREATE / UPDATE
# ============================================

class CouponCreate(CouponBase):
    # business_id None = platform-funded; set = business-funded
    business_id: Optional[UUID] = None

    @model_validator(mode="after")
    def business_specific_requires_business_id(self) -> "CouponCreate":
        if self.coupon_type == CouponType.BUSINESS_SPECIFIC and not self.business_id:
            raise ValueError("business_id is required for BUSINESS_SPECIFIC coupons")
        return self


class CouponUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    discount_value: Optional[Decimal] = None
    max_discount: Optional[Decimal] = None
    buy_quantity: Optional[int] = None
    get_quantity: Optional[int] = None
    bundle_item_ids: Optional[List[str]] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    max_uses: Optional[int] = None
    max_uses_per_user: Optional[int] = None
    min_order_value: Optional[Decimal] = None
    applicable_categories: Optional[List[str]] = None
    applicable_businesses: Optional[List[str]] = None
    new_users_only: Optional[bool] = None
    is_public: Optional[bool] = None
    status: Optional[CouponStatus] = None


# ============================================
# RESPONSES
# ============================================

class CouponResponse(CouponBase):
    id: UUID
    business_id: Optional[UUID]
    status: CouponStatus
    current_uses: int
    created_at: datetime

    model_config = {"from_attributes": True}


class CouponSummary(BaseModel):
    """Lightweight response for listing and apply preview."""
    id: UUID
    code: str
    name: str
    description: Optional[str] = None
    coupon_type: CouponType
    discount_value: Decimal
    max_discount: Optional[Decimal]
    min_order_value: Optional[Decimal]
    end_date: Optional[datetime]
    is_valid: bool
    business_id: Optional[UUID] = None

    model_config = {"from_attributes": True}


# ============================================
# APPLY
# ============================================

class CouponApplyRequest(BaseModel):
    code: str
    order_total: Decimal
    order_type: str            # "hotel_booking", "food_order", "service_booking", etc.
    category: Optional[str] = None
    business_id: Optional[UUID] = None
    item_count: int = 1        # For BUY_X_GET_Y: total quantity of qualifying items
    item_ids: List[str] = []   # For BUNDLE: list of item IDs in cart
    delivery_fee: Decimal = Decimal("0")  # For FREE_DELIVERY: actual delivery fee to waive


class CouponApplyResponse(BaseModel):
    coupon_id: UUID
    code: str
    coupon_type: CouponType
    discount_amount: Decimal
    cashback_amount: Decimal = Decimal("0")  # CASHBACK type — credited post-payment
    final_amount: Decimal
    message: str
    delivery_fee_waived: bool = False        # FREE_DELIVERY type flag


# ============================================
# USAGE
# ============================================

class CouponUsageCreate(BaseModel):
    coupon_id: UUID
    order_type: str
    order_id: Optional[UUID] = None
    discount_amount: Decimal
    order_total: Decimal
    final_amount: Decimal
    cashback_amount: Decimal = Decimal("0")


class CouponUsageResponse(BaseModel):
    id: UUID
    coupon_id: UUID
    order_type: str
    order_id: Optional[UUID]
    discount_amount: Decimal
    order_total: Decimal
    final_amount: Decimal
    cashback_amount: Optional[Decimal]
    cashback_credited: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ============================================
# ANALYTICS
# ============================================

class CouponAnalyticsResponse(BaseModel):
    coupon_id: UUID
    code: str
    name: str
    coupon_type: CouponType
    total_redemptions: int
    total_discount_given: Decimal
    total_cashback_credited: Decimal
    current_uses: int
    max_uses: Optional[int]
    status: CouponStatus

    model_config = {"from_attributes": True}