# app/schemas/products_schema.py

from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from decimal import Decimal
from uuid import UUID

from app.schemas.common_schema import LocationSchema


# ============================================
# VENDOR SCHEMAS
# ============================================

class VendorCreateRequest(BaseModel):
    store_name: str = Field(..., min_length=3, max_length=255)
    store_description: Optional[str] = None
    return_policy: Optional[str] = None
    shipping_policy: Optional[str] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "store_name": "TechHub Store",
            "store_description": "Your trusted electronics store",
            "return_policy": "30-day return policy",
            "shipping_policy": "Ships within 2 business days"
        }
    })


class VendorUpdateRequest(BaseModel):
    store_name: Optional[str] = Field(None, min_length=3, max_length=255)
    store_description: Optional[str] = None
    store_logo: Optional[str] = None
    store_banner: Optional[str] = None
    return_policy: Optional[str] = None
    shipping_policy: Optional[str] = None


class VendorResponse(BaseModel):
    id: UUID
    business_id: UUID
    store_name: str
    store_description: Optional[str]
    store_logo: Optional[str]
    store_banner: Optional[str]
    return_policy: Optional[str]
    shipping_policy: Optional[str]
    total_products: int
    total_sales: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# PRODUCT SCHEMAS
# ============================================

class ProductCreateRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = None
    category: str = Field(..., min_length=2, max_length=100)
    subcategory: Optional[str] = None
    brand: Optional[str] = None
    product_type: str = "physical"
    base_price: Decimal = Field(..., gt=0)
    sale_price: Optional[Decimal] = Field(None, gt=0)
    sku: Optional[str] = None
    stock_quantity: int = Field(default=0, ge=0)
    low_stock_threshold: int = Field(default=10, ge=0)
    specifications: Dict[str, Any] = Field(default_factory=dict)
    images: List[str] = Field(default_factory=list)
    videos: List[str] = Field(default_factory=list)
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    download_url: Optional[str] = None
    file_size_mb: Optional[Decimal] = None

    @field_validator('sale_price')
    @classmethod
    def sale_price_less_than_base(cls, v, info):
        base = info.data.get('base_price')
        if v and base and v >= base:
            raise ValueError('sale_price must be less than base_price')
        return v

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "Wireless Bluetooth Headphones",
            "category": "Electronics",
            "subcategory": "Audio",
            "brand": "SoundMax",
            "base_price": 45000.00,
            "sale_price": 39999.00,
            "stock_quantity": 50,
        }
    })


class ProductUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=3, max_length=255)
    description: Optional[str] = None
    category: Optional[str] = Field(None, min_length=2, max_length=100)
    subcategory: Optional[str] = None
    brand: Optional[str] = None
    base_price: Optional[Decimal] = Field(None, gt=0)
    sale_price: Optional[Decimal] = Field(None, gt=0)
    stock_quantity: Optional[int] = Field(None, ge=0)
    low_stock_threshold: Optional[int] = Field(None, ge=0)
    specifications: Optional[Dict[str, Any]] = None
    images: Optional[List[str]] = None
    videos: Optional[List[str]] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    is_active: Optional[bool] = None


class InventoryUpdateRequest(BaseModel):
    stock_quantity: int = Field(..., ge=0)
    low_stock_threshold: Optional[int] = Field(None, ge=0)


class ProductResponse(BaseModel):
    id: UUID
    vendor_id: UUID
    name: str
    description: Optional[str]
    category: str
    subcategory: Optional[str]
    brand: Optional[str]
    product_type: str
    base_price: Decimal
    sale_price: Optional[Decimal]
    sku: Optional[str]
    stock_quantity: int
    low_stock_threshold: int
    specifications: Dict[str, Any]
    images: List[str]
    videos: List[str]
    views_count: int
    sales_count: int
    average_rating: Decimal
    total_reviews: int
    is_active: bool
    created_at: datetime
    vendor: Optional[VendorResponse] = None
    in_stock: Optional[bool] = None

    model_config = ConfigDict(from_attributes=True)


class ProductListResponse(BaseModel):
    id: UUID
    name: str
    category: str
    brand: Optional[str]
    base_price: Decimal
    sale_price: Optional[Decimal]
    images: List[str]
    average_rating: Decimal
    in_stock: bool
    vendor_id: UUID
    vendor_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# ============================================
# VARIANT SCHEMAS
# ============================================

class VariantCreateRequest(BaseModel):
    attributes: Dict[str, str] = Field(..., min_length=1)
    price: Decimal = Field(..., gt=0)
    stock_quantity: int = Field(default=0, ge=0)
    sku: Optional[str] = None
    images: List[str] = Field(default_factory=list)

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "attributes": {"color": "Blue", "size": "Large"},
            "price": 42000.00,
            "stock_quantity": 20,
        }
    })


class VariantUpdateRequest(BaseModel):
    attributes: Optional[Dict[str, str]] = None
    price: Optional[Decimal] = Field(None, gt=0)
    stock_quantity: Optional[int] = Field(None, ge=0)
    sku: Optional[str] = None
    images: Optional[List[str]] = None
    is_active: Optional[bool] = None


class VariantResponse(BaseModel):
    id: UUID
    product_id: UUID
    sku: Optional[str]
    attributes: Dict[str, str]
    price: Decimal
    stock_quantity: int
    images: List[str]
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# CART SCHEMAS
# ============================================

class CartItemAddRequest(BaseModel):
    product_id: UUID
    variant_id: Optional[UUID] = None
    quantity: int = Field(default=1, gt=0)


class CartItemUpdateRequest(BaseModel):
    quantity: int = Field(..., gt=0)


class CartItemResponse(BaseModel):
    id: UUID
    product_id: UUID
    variant_id: Optional[UUID]
    quantity: int
    product: ProductResponse
    variant: Optional[VariantResponse] = None
    item_total: Decimal


class CartResponse(BaseModel):
    items: List[CartItemResponse]
    subtotal: Decimal
    total_items: int

    model_config = ConfigDict(from_attributes=True)


# ============================================
# ORDER SCHEMAS
# ============================================

# FIX: "card" removed from VALID_PAYMENT_METHODS.
# Blueprint: card payments go through Paystack to top up the wallet; checkout
# always draws from wallet balance.  Accepting "card" here while the service
# rejects it caused a confusing 422-at-schema vs 500-in-service split.
# Both schema and service now agree: only "wallet" is a valid checkout method.
VALID_PAYMENT_METHODS = {"wallet"}

# Valid order status values — must match the DB enum exactly (all lowercase).
VALID_ORDER_STATUSES = {"pending", "processing", "packed", "shipped", "delivered", "cancelled"}

# Platform fee per Blueprint §4.4 — ₦50 flat fee on every product order.
PRODUCT_PLATFORM_FEE = Decimal("50")


class OrderItemCreate(BaseModel):
    product_id: UUID
    variant_id: Optional[UUID] = None
    quantity: int = Field(..., gt=0)


class OrderCreateRequest(BaseModel):
    items: List[OrderItemCreate]
    shipping_address: str = Field(..., min_length=10)
    shipping_location: Optional[LocationSchema] = None
    # Optional — resolved from the user's registration details (full_name,
    # phone_number) in the service if not supplied.  Provide them to override
    # when ordering as a gift for someone else.
    recipient_name: Optional[str] = Field(None, min_length=2, max_length=200)
    recipient_phone: Optional[str] = Field(None, min_length=10, max_length=20)
    notes: Optional[str] = None
    payment_method: str = Field(default="wallet")
    coupon_code: Optional[str] = None

    @field_validator("payment_method")
    @classmethod
    def validate_payment_method(cls, v: str) -> str:
        if v not in VALID_PAYMENT_METHODS:
            raise ValueError(
                f"payment_method must be one of: {', '.join(sorted(VALID_PAYMENT_METHODS))}"
            )
        return v

    @field_validator("items")
    @classmethod
    def items_not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("Order must contain at least one item")
        return v

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "items": [{"product_id": "123e4567-e89b-12d3-a456-426614174000", "quantity": 2}],
            "shipping_address": "123 Main Street, Garki, Abuja",
            "payment_method": "wallet",
            # recipient_name and recipient_phone are optional —
            # omit them to use the values from your registered profile
        }
    })


class OrderItemResponse(BaseModel):
    id: UUID
    product_id: Optional[UUID]
    variant_id: Optional[UUID]
    vendor_id: UUID
    quantity: int
    unit_price: Decimal
    total_price: Decimal
    product_snapshot: Dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


class OrderResponse(BaseModel):
    id: UUID
    customer_id: UUID
    shipping_address: str
    recipient_name: str
    recipient_phone: str
    subtotal: Decimal
    shipping_fee: Decimal
    tax: Decimal
    discount: Decimal
    platform_fee: Decimal = PRODUCT_PLATFORM_FEE
    total_amount: Decimal
    coupon_code: Optional[str]
    payment_method: Optional[str]
    payment_status: str
    order_status: str
    tracking_number: Optional[str]
    estimated_delivery: Optional[date]
    notes: Optional[str]
    created_at: datetime
    items: List[OrderItemResponse]

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class OrderListResponse(BaseModel):
    id: UUID
    order_status: str
    payment_status: str
    total_amount: Decimal
    total_items: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class OrderStatusUpdateRequest(BaseModel):
    status: str = Field(..., description="New order status")
    tracking_number: Optional[str] = None
    estimated_delivery: Optional[date] = None

    @field_validator('status')
    @classmethod
    def validate_status(cls, v: str) -> str:
        v = v.lower().strip()
        vendor_allowed = {"processing", "packed", "shipped", "delivered", "cancelled"}
        if v not in vendor_allowed:
            raise ValueError(
                f"status must be one of: {', '.join(sorted(vendor_allowed))}"
            )
        return v


class ReturnRequest(BaseModel):
    reason: str = Field(..., min_length=10, max_length=500)
    items: List[UUID] = Field(..., description="Order item IDs to return")
    photos: List[str] = Field(default_factory=list)

    @field_validator("items")
    @classmethod
    def items_not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("Return request must specify at least one item")
        return v


class ReturnResponse(BaseModel):
    """
    Response returned after a return request is submitted.
    Blueprint §11.4: in-app return/refund request flow.
    """
    id: UUID
    order_id: UUID
    reason: str
    photos: List[str]
    status: str  # "pending" | "approved" | "rejected"
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


# ============================================
# WISHLIST SCHEMAS
# ============================================

class WishlistItemResponse(BaseModel):
    id: UUID
    product_id: UUID
    product: ProductListResponse
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WishlistResponse(BaseModel):
    items: List[WishlistItemResponse]
    total: int

    model_config = ConfigDict(from_attributes=True)


# ============================================
# SEARCH FILTERS
# ============================================

class ProductSearchFilters(BaseModel):
    """
    Filters for product discovery.

    Blueprint §3.1: radius-based search ONLY — no LGA filtering anywhere.
    lga_id has been removed. Location is resolved from user GPS + radius_km
    (default 5 km per blueprint §3.1).
    """
    query: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    brand: Optional[str] = None
    min_price: Optional[Decimal] = Field(None, ge=0)
    max_price: Optional[Decimal] = Field(None, ge=0)
    in_stock_only: bool = False
    sort_by: Optional[str] = "created_at"
    location: Optional[LocationSchema] = None
    radius_km: Optional[float] = Field(None, gt=0, le=50)

    @field_validator("sort_by")
    @classmethod
    def validate_sort_by(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return "created_at"
        allowed = {"created_at", "base_price", "average_rating", "sales_count", "views_count"}
        if v not in allowed:
            raise ValueError(f"sort_by must be one of: {', '.join(sorted(allowed))}")
        return v

    @field_validator("max_price")
    @classmethod
    def max_gte_min(cls, v: Optional[Decimal], info) -> Optional[Decimal]:
        min_p = info.data.get("min_price")
        if v is not None and min_p is not None and v < min_p:
            raise ValueError("max_price must be greater than or equal to min_price")
        return v


# ============================================
# ANALYTICS SCHEMAS
# ============================================

class TopProductSummary(BaseModel):
    product_id: UUID
    name: str
    sales_count: int
    revenue: Decimal

    model_config = ConfigDict(from_attributes=True)


class VendorAnalyticsSummary(BaseModel):
    total_revenue: Decimal
    total_orders: int
    total_products: int
    active_products: int
    low_stock_count: int
    top_products: List[TopProductSummary]

    model_config = ConfigDict(from_attributes=True)