from sqlalchemy import (
    Column, String, Boolean, Enum, Text, Integer,
    Numeric, ForeignKey, Date, Time, DateTime, CheckConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geography
import enum

from app.models.base import BaseModel


# ============================================
# ENUMS
# ============================================

class CuisineTypeEnum(str, enum.Enum):
    NIGERIAN = "nigerian"
    AFRICAN = "african"
    CHINESE = "chinese"
    ITALIAN = "italian"
    INDIAN = "indian"
    AMERICAN = "american"
    FAST_FOOD = "fast_food"
    CONTINENTAL = "continental"
    SEAFOOD = "seafood"
    VEGETARIAN = "vegetarian"
    OTHER = "other"


class OrderStatusEnum(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PREPARING = "preparing"
    READY = "ready"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class TableStatusEnum(str, enum.Enum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    OCCUPIED = "occupied"
    MAINTENANCE = "maintenance"


class ReservationStatusEnum(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SEATED = "seated"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


# ============================================
# RESTAURANT MODEL
# ============================================

class Restaurant(BaseModel):
    """Restaurant business details"""

    __tablename__ = "restaurants"

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    # Restaurant Details
    cuisine_types = Column(JSONB, default=list)  # Multiple cuisines
    price_range = Column(String(20), nullable=True)  # $, $$, $$$, $$$$

    # Operating Hours
    opening_time = Column(Time, nullable=True)
    closing_time = Column(Time, nullable=True)

    # Seating
    total_tables = Column(Integer, default=0)
    seating_capacity = Column(Integer, default=0)

    # Services Offered
    offers_delivery = Column(Boolean, default=True)
    offers_takeout = Column(Boolean, default=True)
    offers_dine_in = Column(Boolean, default=True)
    offers_reservations = Column(Boolean, default=True)

    # Delivery Settings
    delivery_fee = Column(Numeric(10, 2), default=0.00)
    free_delivery_minimum = Column(Numeric(10, 2), nullable=True)
    delivery_radius_km = Column(Numeric(5, 2), default=10.00)
    average_delivery_time_minutes = Column(Integer, default=45)

    # Preparation Time
    average_preparation_time_minutes = Column(Integer, default=30)

    # Features
    features = Column(JSONB, default=list)  # ["parking", "wifi", "outdoor_seating", "live_music"]

    # Media
    menu_pdf_url = Column(Text, nullable=True)
    gallery_images = Column(JSONB, default=list)

    # Stats
    total_orders = Column(Integer, default=0)
    total_reservations = Column(Integer, default=0)

    # Relationships
    business = relationship("Business", back_populates="restaurant")
    menu_categories = relationship(
        "MenuCategory",
        back_populates="restaurant",
        cascade="all, delete-orphan",
        order_by="MenuCategory.display_order"
    )
    reservations = relationship(
        "TableReservation",
        back_populates="restaurant"
    )
    food_orders = relationship(
        "FoodOrder",
        back_populates="restaurant"
    )

    def __repr__(self):
        return f"<Restaurant {self.business_id}>"


# ============================================
# MENU CATEGORY MODEL
# ============================================

class MenuCategory(BaseModel):
    """Menu categories (Appetizers, Main Course, Drinks, etc.)"""

    __tablename__ = "menu_categories"

    restaurant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    display_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

    # Relationships
    restaurant = relationship("Restaurant", back_populates="menu_categories")
    menu_items = relationship(
        "MenuItem",
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="MenuItem.display_order"
    )

    def __repr__(self):
        return f"<MenuCategory {self.name}>"


# ============================================
# MENU ITEM MODEL
# ============================================

class MenuItem(BaseModel):
    """Individual menu items"""

    __tablename__ = "menu_items"

    category_id = Column(
        UUID(as_uuid=True),
        ForeignKey("menu_categories.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Basic Info
    name = Column(String(200), nullable=False, index=True)
    description = Column(Text, nullable=True)

    # Pricing
    price = Column(Numeric(10, 2), nullable=False)
    discount_price = Column(Numeric(10, 2), nullable=True)

    # Details
    preparation_time_minutes = Column(Integer, default=15)
    calories = Column(Integer, nullable=True)

    # Tags
    is_vegetarian = Column(Boolean, default=False)
    is_vegan = Column(Boolean, default=False)
    is_gluten_free = Column(Boolean, default=False)
    is_spicy = Column(Boolean, default=False)
    spice_level = Column(Integer, nullable=True)  # 1-5

    # Allergens
    allergens = Column(JSONB, default=list)  # ["nuts", "dairy", "shellfish"]

    # Media
    image_url = Column(Text, nullable=True)
    images = Column(JSONB, default=list)

    # Availability
    is_available = Column(Boolean, default=True)
    available_for_delivery = Column(Boolean, default=True)
    available_for_takeout = Column(Boolean, default=True)
    available_for_dine_in = Column(Boolean, default=True)

    # Customization Options
    modifiers = Column(JSONB, default=list)
    # Example: [
    #   {"name": "Size", "options": [{"value": "Small", "price": 0}, {"value": "Large", "price": 500}], "required": true},
    #   {"name": "Extra Toppings", "options": [{"value": "Cheese", "price": 200}], "required": false}
    # ]

    # Stats
    display_order = Column(Integer, default=0)
    popularity_score = Column(Integer, default=0)  # Based on orders

    # Relationships
    category = relationship("MenuCategory", back_populates="menu_items")
    order_items = relationship(
        "FoodOrderItem",
        back_populates="menu_item"
    )

    __table_args__ = (
        CheckConstraint('price > 0', name='positive_menu_price'),
        CheckConstraint('spice_level >= 1 AND spice_level <= 5', name='valid_spice_level'),
    )

    def __repr__(self):
        return f"<MenuItem {self.name}>"


# ============================================
# TABLE RESERVATION MODEL
# ============================================

class TableReservation(BaseModel):
    """Restaurant table reservations"""

    __tablename__ = "table_reservations"

    restaurant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Reservation Details
    reservation_date = Column(Date, nullable=False, index=True)
    reservation_time = Column(Time, nullable=False)
    number_of_guests = Column(Integer, nullable=False)

    # Contact Info
    customer_name = Column(String(200), nullable=False)
    customer_phone = Column(String(20), nullable=False)
    customer_email = Column(String(255), nullable=True)

    # Preferences
    seating_preference = Column(String(100), nullable=True)  # window, outdoor, quiet
    special_requests = Column(Text, nullable=True)
    occasion = Column(String(100), nullable=True)  # birthday, anniversary, business

    # Status
    status = Column(
        Enum(ReservationStatusEnum),
        default=ReservationStatusEnum.PENDING,
        nullable=False,
        index=True
    )

    # Table Assignment
    table_number = Column(String(20), nullable=True)

    # Confirmation
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    confirmation_code = Column(String(20), unique=True, nullable=True)

    # Arrival
    arrived_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Cancellation
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)

    # Relationships
    restaurant = relationship("Restaurant", back_populates="reservations")
    customer = relationship("User", foreign_keys=[customer_id])

    __table_args__ = (
        CheckConstraint('number_of_guests > 0', name='positive_guests'),
    )

    def __repr__(self):
        return f"<TableReservation {self.id} - {self.status}>"


# ============================================
# FOOD ORDER MODEL
# ============================================

class FoodOrder(BaseModel):
    """Food orders (delivery, takeout, dine-in)"""

    __tablename__ = "food_orders"

    restaurant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Order Type
    order_type = Column(String(20), nullable=False)  # delivery, takeout, dine_in

    # Delivery Details (if applicable)
    delivery_address = Column(Text, nullable=True)
    delivery_location = Column(Geography(geometry_type='POINT', srid=4326), nullable=True)
    delivery_instructions = Column(Text, nullable=True)

    # Contact Info
    customer_name = Column(String(200), nullable=False)
    customer_phone = Column(String(20), nullable=False)

    # Pricing
    subtotal = Column(Numeric(10, 2), nullable=False)
    delivery_fee = Column(Numeric(10, 2), default=0.00)
    service_charge = Column(Numeric(10, 2), default=0.00)
    tax = Column(Numeric(10, 2), default=0.00)
    discount = Column(Numeric(10, 2), default=0.00)
    tip = Column(Numeric(10, 2), default=0.00)
    total_amount = Column(Numeric(10, 2), nullable=False)

    # Payment
    payment_method = Column(String(50), nullable=True)
    payment_status = Column(String(50), default="pending", nullable=False)
    payment_reference = Column(String(100), nullable=True)

    # Status
    order_status = Column(
        Enum(OrderStatusEnum),
        default=OrderStatusEnum.PENDING,
        nullable=False,
        index=True
    )

    # Timing
    estimated_preparation_time = Column(Integer, nullable=True)  # minutes
    estimated_delivery_time = Column(DateTime(timezone=True), nullable=True)

    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    prepared_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    # Special Instructions
    special_instructions = Column(Text, nullable=True)

    # Delivery Assignment
    delivery_id = Column(UUID(as_uuid=True), nullable=True)  # Links to deliveries table

    # Rating
    rating = Column(Integer, nullable=True)
    review = Column(Text, nullable=True)

    # Cancellation
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)

    # Relationships
    restaurant = relationship("Restaurant", back_populates="food_orders")
    customer = relationship("User", foreign_keys=[customer_id])
    items = relationship(
        "FoodOrderItem",
        back_populates="order",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint('total_amount >= 0', name='non_negative_food_total'),
        CheckConstraint('rating >= 1 AND rating <= 5', name='valid_food_rating'),
    )

    def __repr__(self):
        return f"<FoodOrder {self.id} - {self.order_status}>"


# ============================================
# FOOD ORDER ITEM MODEL
# ============================================

class FoodOrderItem(BaseModel):
    """Individual items in a food order"""

    __tablename__ = "food_order_items"

    order_id = Column(
        UUID(as_uuid=True),
        ForeignKey("food_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    menu_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("menu_items.id", ondelete="SET NULL"),
        nullable=True
    )

    # Order Details
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    total_price = Column(Numeric(10, 2), nullable=False)

    # Item Snapshot (store details at time of order)
    item_name = Column(String(200), nullable=False)
    item_snapshot = Column(JSONB, nullable=False)

    # Customizations
    selected_modifiers = Column(JSONB, default=list)
    # Example: [{"name": "Size", "value": "Large", "price": 500}]

    special_instructions = Column(Text, nullable=True)

    # Relationships
    order = relationship("FoodOrder", back_populates="items")
    menu_item = relationship("MenuItem", back_populates="order_items")

    __table_args__ = (
        CheckConstraint('quantity > 0', name='positive_food_quantity'),
    )

    def __repr__(self):
        return f"<FoodOrderItem {self.item_name} x{self.quantity}>"


# ============================================
# COOKING SERVICE MODEL
# ============================================

class CookingService(BaseModel):
    """Private cooking services (catering, home chef)"""

    __tablename__ = "cooking_services"

    restaurant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("restaurants.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Service Details
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)

    # Pricing
    base_price = Column(Numeric(10, 2), nullable=False)
    price_per_person = Column(Numeric(10, 2), nullable=True)

    # Service Type
    service_type = Column(String(50), nullable=False)  # catering, private_chef, meal_prep

    # Capacity
    min_guests = Column(Integer, default=1)
    max_guests = Column(Integer, nullable=True)

    # Lead Time
    advance_booking_days = Column(Integer, default=3)

    # Media
    images = Column(JSONB, default=list)

    # Availability
    is_active = Column(Boolean, default=True)

    # Relationships
    restaurant = relationship("Restaurant")
    bookings = relationship(
        "CookingBooking",
        back_populates="service",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<CookingService {self.name}>"


# ============================================
# COOKING BOOKING MODEL
# ============================================

class CookingBooking(BaseModel):
    """Bookings for cooking services"""

    __tablename__ = "cooking_bookings"

    service_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cooking_services.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Event Details
    event_date = Column(Date, nullable=False, index=True)
    event_time = Column(Time, nullable=False)
    number_of_guests = Column(Integer, nullable=False)

    # Location
    event_address = Column(Text, nullable=False)
    event_location = Column(Geography(geometry_type='POINT', srid=4326), nullable=True)

    # Pricing
    base_price = Column(Numeric(10, 2), nullable=False)
    total_price = Column(Numeric(10, 2), nullable=False)

    # Menu/Requirements
    menu_requirements = Column(Text, nullable=True)
    dietary_restrictions = Column(JSONB, default=list)
    special_requests = Column(Text, nullable=True)

    # Status
    status = Column(String(50), default="pending", nullable=False)
    payment_status = Column(String(50), default="pending", nullable=False)

    # Relationships
    service = relationship("CookingService", back_populates="bookings")
    customer = relationship("User", foreign_keys=[customer_id])

    def __repr__(self):
        return f"<CookingBooking {self.id}>"