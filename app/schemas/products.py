from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from decimal import Decimal
from uuid import UUID

from app.schemas.common import LocationSchema


# ============================================
# VENDOR SCHEMAS
# ============================================

class VendorCreateRequest(BaseModel):
    """Create vendor/store request"""
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


class VendorResponse(BaseModel):
    """Vendor response"""
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
    """Create product request"""
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

    # Digital product fields
    download_url: Optional[str] = None
    file_size_mb: Optional[Decimal] = None

    @field_validator('sale_price')
    @classmethod
    def validate_sale_price(cls, v, info):
        base_price = info.data.get('base_price')
        if v and base_price and v >= base_price:
            raise ValueError('Sale price must be less than base price')
        return v

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "Wireless Bluetooth Headphones",
            "description": "Premium noise-canceling headphones with 30-hour battery",
            "category": "Electronics",
            "subcategory": "Audio",
            "brand": "SoundMax",
            "product_type": "physical",
            "base_price": 45000.00,
            "sale_price": 39999.00,
            "sku": "WBH-001",
            "stock_quantity": 50,
            "specifications": {
                "battery_life": "30 hours",
                "weight": "250g",
                "color": "Black"
            },
            "images": ["https://example.com/headphones.jpg"]
        }
    })


class ProductUpdateRequest(BaseModel):
    """Update product request"""
    name: Optional[str] = Field(None, min_length=3, max_length=255)
    description: Optional[str] = None
    base_price: Optional[Decimal] = Field(None, gt=0)
    sale_price: Optional[Decimal] = Field(None, gt=0)
    stock_quantity: Optional[int] = Field(None, ge=0)
    specifications: Optional[Dict[str, Any]] = None
    images: Optional[List[str]] = None
    is_active: Optional[bool] = None


class ProductResponse(BaseModel):
    """Product response"""
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

    # Nested
    vendor: Optional[VendorResponse] = None
    in_stock: Optional[bool] = None

    model_config = ConfigDict(from_attributes=True)


class ProductListResponse(BaseModel):
    """Simplified product list response"""
    id: UUID
    name: str
    category: str
    brand: Optional[str]
    base_price: Decimal
    sale_price: Optional[Decimal]
    images: List[str]
    average_rating: Decimal
    in_stock: bool
    vendor_name: str


# ============================================
# VARIANT SCHEMAS
# ============================================

class VariantCreateRequest(BaseModel):
    """Create product variant"""
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
            "sku": "WBH-001-BL-L"
        }
    })


class VariantResponse(BaseModel):
    """Variant response"""
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
    """Add item to cart"""
    product_id: UUID
    variant_id: Optional[UUID] = None
    quantity: int = Field(default=1, gt=0)


class CartItemUpdateRequest(BaseModel):
    """Update cart item quantity"""
    quantity: int = Field(..., gt=0)


class CartItemResponse(BaseModel):
    """Cart item response"""
    id: UUID
    product_id: UUID
    variant_id: Optional[UUID]
    quantity: int

    # Nested product info
    product: ProductResponse
    variant: Optional[VariantResponse] = None
    item_total: Decimal

    model_config = ConfigDict(from_attributes=True)


class CartResponse(BaseModel):
    """Full cart response"""
    items: List[CartItemResponse]
    subtotal: Decimal
    total_items: int


# ============================================
# ORDER SCHEMAS
# ============================================

class OrderItemCreate(BaseModel):
    """Order item for checkout"""
    product_id: UUID
    variant_id: Optional[UUID] = None
    quantity: int = Field(..., gt=0)


class OrderCreateRequest(BaseModel):
    """Create order from cart"""
    items: List[OrderItemCreate]
    shipping_address: str = Field(..., min_length=10)
    shipping_location: Optional[LocationSchema] = None
    recipient_name: str = Field(..., min_length=2, max_length=200)
    recipient_phone: str = Field(..., min_length=10, max_length=20)
    notes: Optional[str] = None
    payment_method: str = "wallet"

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "items": [
                {
                    "product_id": "123e4567-e89b-12d3-a456-426614174000",
                    "variant_id": None,
                    "quantity": 2
                }
            ],
            "shipping_address": "123 Main Street, Garki, Abuja",
            "recipient_name": "John Doe",
            "recipient_phone": "+2348012345678",
            "payment_method": "wallet"
        }
    })


class OrderItemResponse(BaseModel):
    """Order item response"""
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
    """Order response"""
    id: UUID
    customer_id: UUID
    shipping_address: str
    recipient_name: str
    recipient_phone: str
    subtotal: Decimal
    shipping_fee: Decimal
    tax: Decimal
    discount: Decimal
    total_amount: Decimal
    payment_method: Optional[str]
    payment_status: str
    order_status: str
    tracking_number: Optional[str]
    estimated_delivery: Optional[date]
    notes: Optional[str]
    created_at: datetime

    # Nested
    items: List[OrderItemResponse]

    model_config = ConfigDict(from_attributes=True)


class OrderListResponse(BaseModel):
    """Simplified order list"""
    id: UUID
    order_status: str
    payment_status: str
    total_amount: Decimal
    total_items: int
    created_at: datetime


# ============================================
# SEARCH FILTERS
# ============================================

class ProductSearchFilters(BaseModel):
    """Product search filters"""
    query: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    brand: Optional[str] = None
    min_price: Optional[Decimal] = Field(None, ge=0)
    max_price: Optional[Decimal] = Field(None, ge=0)
    in_stock_only: bool = False
    sort_by: Optional[str] = "created_at"  # created_at, price_asc, price_desc, popular, rating
    location: Optional[LocationSchema] = None
    radius_km: Optional[float] = Field(None, gt=0)