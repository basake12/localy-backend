from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime, date, time
from decimal import Decimal
from uuid import UUID

from app.schemas.common_schema import LocationSchema


# ============================================
# RESTAURANT SCHEMAS
# ============================================

class RestaurantCreateRequest(BaseModel):
    """Create restaurant request"""
    cuisine_types: List[str] = Field(..., min_length=1)
    price_range: Optional[str] = None
    opening_time: Optional[time] = None
    closing_time: Optional[time] = None
    total_tables: int = Field(default=0, ge=0)
    seating_capacity: int = Field(default=0, ge=0)
    offers_delivery: bool = True
    offers_takeout: bool = True
    offers_dine_in: bool = True
    offers_reservations: bool = True
    delivery_fee: Decimal = Field(default=0.00, ge=0)
    delivery_radius_km: Decimal = Field(default=10.00, gt=0)
    features: List[str] = Field(default_factory=list)

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "cuisine_types": ["nigerian", "african"],
            "price_range": "$$",
            "opening_time": "08:00",
            "closing_time": "22:00",
            "total_tables": 20,
            "seating_capacity": 80,
            "offers_delivery": True,
            "delivery_fee": 1000.00,
            "features": ["parking", "wifi", "outdoor_seating"]
        }
    })


class RestaurantResponse(BaseModel):
    """Restaurant response"""
    id: UUID
    business_id: UUID
    cuisine_types: List[str]
    price_range: Optional[str]
    opening_time: Optional[time]
    closing_time: Optional[time]
    total_tables: int
    seating_capacity: int
    offers_delivery: bool
    offers_takeout: bool
    offers_dine_in: bool
    offers_reservations: bool
    delivery_fee: Decimal
    delivery_radius_km: Decimal
    average_delivery_time_minutes: int
    features: List[str]
    gallery_images: List[str]
    total_orders: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# MENU SCHEMAS
# ============================================

class MenuCategoryCreateRequest(BaseModel):
    """Create menu category"""
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    display_order: int = Field(default=0, ge=0)


class MenuCategoryResponse(BaseModel):
    """Menu category response"""
    id: UUID
    restaurant_id: UUID
    name: str
    description: Optional[str]
    display_order: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MenuItemCreateRequest(BaseModel):
    """Create menu item"""
    category_id: UUID
    name: str = Field(..., min_length=2, max_length=200)
    description: Optional[str] = None
    price: Decimal = Field(..., gt=0)
    discount_price: Optional[Decimal] = Field(None, gt=0)
    preparation_time_minutes: int = Field(default=15, gt=0)
    calories: Optional[int] = Field(None, ge=0)
    is_vegetarian: bool = False
    is_vegan: bool = False
    is_gluten_free: bool = False
    is_spicy: bool = False
    spice_level: Optional[int] = Field(None, ge=1, le=5)
    allergens: List[str] = Field(default_factory=list)
    image_url: Optional[str] = None
    modifiers: List[Dict[str, Any]] = Field(default_factory=list)
    available_for_delivery: bool = True
    available_for_takeout: bool = True
    available_for_dine_in: bool = True

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "category_id": "123e4567-e89b-12d3-a456-426614174000",
            "name": "Jollof Rice with Chicken",
            "description": "Classic Nigerian jollof rice served with grilled chicken",
            "price": 3500.00,
            "preparation_time_minutes": 25,
            "is_spicy": True,
            "spice_level": 3,
            "modifiers": [
                {
                    "name": "Protein Choice",
                    "options": [
                        {"value": "Chicken", "price": 0},
                        {"value": "Beef", "price": 500},
                        {"value": "Fish", "price": 800}
                    ],
                    "required": True
                }
            ]
        }
    })


class MenuItemResponse(BaseModel):
    """Menu item response"""
    id: UUID
    category_id: UUID
    name: str
    description: Optional[str]
    price: Decimal
    discount_price: Optional[Decimal]
    preparation_time_minutes: int
    calories: Optional[int]
    is_vegetarian: bool
    is_vegan: bool
    is_spicy: bool
    spice_level: Optional[int]
    allergens: List[str]
    image_url: Optional[str]
    images: List[str]
    modifiers: List[Dict[str, Any]]
    is_available: bool
    popularity_score: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MenuResponse(BaseModel):
    """Full menu with categories and items"""
    categories: List[Dict[str, Any]]


# ============================================
# RESERVATION SCHEMAS
# ============================================

class ReservationCreateRequest(BaseModel):
    """Create table reservation"""
    restaurant_id: UUID
    reservation_date: date
    reservation_time: time
    number_of_guests: int = Field(..., gt=0)
    customer_name: str = Field(..., min_length=2)
    customer_phone: str = Field(..., min_length=10)
    customer_email: Optional[str] = None
    seating_preference: Optional[str] = None
    special_requests: Optional[str] = None
    occasion: Optional[str] = None

    @field_validator('reservation_date')
    @classmethod
    def validate_future_date(cls, v):
        from datetime import date as dt_date
        if v < dt_date.today():
            raise ValueError('Reservation date must be in the future')
        return v


class ReservationResponse(BaseModel):
    """Reservation response"""
    id: UUID
    restaurant_id: UUID
    customer_id: UUID
    reservation_date: date
    reservation_time: time
    number_of_guests: int
    customer_name: str
    customer_phone: str
    seating_preference: Optional[str]
    special_requests: Optional[str]
    occasion: Optional[str]
    status: str
    table_number: Optional[str]
    confirmation_code: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# FOOD ORDER SCHEMAS
# ============================================

class OrderItemCreate(BaseModel):
    """Order item for checkout"""
    menu_item_id: UUID
    quantity: int = Field(..., gt=0)
    selected_modifiers: List[Dict[str, Any]] = Field(default_factory=list)
    special_instructions: Optional[str] = None


class FoodOrderCreateRequest(BaseModel):
    """Create food order"""
    restaurant_id: UUID
    order_type: str  # delivery, takeout, dine_in
    items: List[OrderItemCreate]

    # Delivery details (required if order_type is delivery)
    delivery_address: Optional[str] = None
    delivery_location: Optional[LocationSchema] = None
    delivery_instructions: Optional[str] = None

    # Contact
    customer_name: str = Field(..., min_length=2)
    customer_phone: str = Field(..., min_length=10)

    # Payment
    payment_method: str = "wallet"
    tip: Decimal = Field(default=0.00, ge=0)

    special_instructions: Optional[str] = None

    @field_validator('order_type')
    @classmethod
    def validate_order_type(cls, v):
        valid_types = ["delivery", "takeout", "dine_in"]
        if v not in valid_types:
            raise ValueError(f'Invalid order type. Must be one of: {valid_types}')
        return v

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "restaurant_id": "123e4567-e89b-12d3-a456-426614174000",
            "order_type": "delivery",
            "items": [
                {
                    "menu_item_id": "123e4567-e89b-12d3-a456-426614174001",
                    "quantity": 2,
                    "selected_modifiers": [
                        {"name": "Protein Choice", "value": "Chicken", "price": 0}
                    ]
                }
            ],
            "delivery_address": "123 Main St, Garki, Abuja",
            "customer_name": "John Doe",
            "customer_phone": "+2348012345678",
            "payment_method": "wallet"
        }
    })


class FoodOrderItemResponse(BaseModel):
    """Order item response"""
    id: UUID
    menu_item_id: Optional[UUID]
    item_name: str
    quantity: int
    unit_price: Decimal
    total_price: Decimal
    selected_modifiers: List[Dict[str, Any]]
    special_instructions: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class FoodOrderResponse(BaseModel):
    """Food order response"""
    id: UUID
    restaurant_id: UUID
    customer_id: UUID
    order_type: str
    delivery_address: Optional[str]
    customer_name: str
    customer_phone: str
    subtotal: Decimal
    delivery_fee: Decimal
    service_charge: Decimal
    tax: Decimal
    discount: Decimal
    tip: Decimal
    total_amount: Decimal
    payment_method: Optional[str]
    payment_status: str
    order_status: str
    estimated_delivery_time: Optional[datetime]
    special_instructions: Optional[str]
    delivery_id: Optional[UUID]
    created_at: datetime

    # Nested
    items: List[FoodOrderItemResponse]

    model_config = ConfigDict(from_attributes=True)


class FoodOrderListResponse(BaseModel):
    """Simplified order list"""
    id: UUID
    restaurant_name: str
    order_type: str
    total_amount: Decimal
    order_status: str
    created_at: datetime


# ============================================
# SEARCH FILTERS
# ============================================

class RestaurantSearchFilters(BaseModel):
    """Restaurant search filters"""
    query: Optional[str] = None
    cuisine_type: Optional[str] = None
    location: Optional[LocationSchema] = None
    radius_km: Optional[float] = Field(None, gt=0)
    price_range: Optional[str] = None
    offers_delivery: Optional[bool] = None
    is_open_now: Optional[bool] = None
    min_rating: Optional[Decimal] = Field(None, ge=0, le=5)