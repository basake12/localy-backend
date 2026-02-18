from sqlalchemy import (
    Column, String, Boolean, Enum, Text, Integer,
    Numeric, ForeignKey, DateTime, CheckConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum
from geoalchemy2 import Geography
from sqlalchemy import Date, UniqueConstraint
from app.models.base import BaseModel



# ============================================
# ENUMS
# ============================================

class ProductTypeEnum(str, enum.Enum):
    PHYSICAL = "physical"
    DIGITAL = "digital"


class OrderStatusEnum(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PACKED = "packed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentStatusEnum(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


# ============================================
# PRODUCT VENDOR MODEL
# ============================================

class ProductVendor(BaseModel):
    """Vendor/seller managing products"""

    __tablename__ = "product_vendors"

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    # Store Info
    store_name = Column(String(255), nullable=False, index=True)
    store_description = Column(Text, nullable=True)
    store_logo = Column(Text, nullable=True)
    store_banner = Column(Text, nullable=True)

    # Policies
    return_policy = Column(Text, nullable=True)
    shipping_policy = Column(Text, nullable=True)

    # Stats
    total_products = Column(Integer, default=0)
    total_sales = Column(Integer, default=0)

    # Relationships
    business = relationship("Business", back_populates="product_vendor")
    products = relationship(
        "Product",
        back_populates="vendor",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<ProductVendor {self.store_name}>"


# ============================================
# PRODUCT MODEL
# ============================================

class Product(BaseModel):
    """Main product model"""

    __tablename__ = "products"

    vendor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("product_vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Basic Info
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=False, index=True)
    subcategory = Column(String(100), nullable=True, index=True)
    brand = Column(String(100), nullable=True, index=True)

    # Product Type
    product_type = Column(
        Enum(ProductTypeEnum),
        default=ProductTypeEnum.PHYSICAL,
        nullable=False
    )

    # Pricing
    base_price = Column(Numeric(10, 2), nullable=False)
    sale_price = Column(Numeric(10, 2), nullable=True)

    # Inventory (for products without variants)
    sku = Column(String(100), unique=True, nullable=True, index=True)
    stock_quantity = Column(Integer, default=0)
    low_stock_threshold = Column(Integer, default=10)

    # Specifications
    specifications = Column(JSONB, default=dict)  # {weight: "500g", dimensions: "10x10x5"}

    # Media
    images = Column(JSONB, default=list)  # Array of image URLs
    videos = Column(JSONB, default=list)  # Array of video URLs

    # Digital Products
    download_url = Column(Text, nullable=True)
    file_size_mb = Column(Numeric(10, 2), nullable=True)

    # SEO
    meta_title = Column(String(255), nullable=True)
    meta_description = Column(Text, nullable=True)
    slug = Column(String(255), unique=True, nullable=True, index=True)

    # Stats
    views_count = Column(Integer, default=0)
    sales_count = Column(Integer, default=0)
    average_rating = Column(Numeric(3, 2), default=0.00)
    total_reviews = Column(Integer, default=0)

    # Status
    is_active = Column(Boolean, default=True, index=True)

    # Relationships
    vendor = relationship("ProductVendor", back_populates="products")
    variants = relationship(
        "ProductVariant",
        back_populates="product",
        cascade="all, delete-orphan"
    )
    order_items = relationship(
        "OrderItem",
        back_populates="product"
    )

    __table_args__ = (
        CheckConstraint('base_price > 0', name='positive_base_price'),
        CheckConstraint('stock_quantity >= 0', name='non_negative_stock'),
    )

    def __repr__(self):
        return f"<Product {self.name}>"


# ============================================
# PRODUCT VARIANT MODEL
# ============================================

class ProductVariant(BaseModel):
    """Product variants (size, color, etc.)"""

    __tablename__ = "product_variants"

    product_id = Column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    sku = Column(String(100), unique=True, nullable=True, index=True)

    # Variant Attributes (stored as JSONB)
    attributes = Column(JSONB, nullable=False)  # {color: "red", size: "M"}

    # Pricing
    price = Column(Numeric(10, 2), nullable=False)

    # Inventory
    stock_quantity = Column(Integer, default=0)

    # Media
    images = Column(JSONB, default=list)

    # Status
    is_active = Column(Boolean, default=True)

    # Relationships
    product = relationship("Product", back_populates="variants")
    order_items = relationship(
        "OrderItem",
        back_populates="variant"
    )

    __table_args__ = (
        CheckConstraint('price > 0', name='positive_variant_price'),
        CheckConstraint('stock_quantity >= 0', name='non_negative_variant_stock'),
    )

    def __repr__(self):
        return f"<ProductVariant {self.product_id} - {self.attributes}>"


# ============================================
# PRODUCT ORDER MODEL
# ============================================

class ProductOrder(BaseModel):
    """Customer orders"""

    __tablename__ = "product_orders"

    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Delivery Address
    shipping_address = Column(Text, nullable=False)
    shipping_location = Column(Geography(geometry_type='POINT', srid=4326), nullable=True)
    recipient_name = Column(String(200), nullable=False)
    recipient_phone = Column(String(20), nullable=False)

    # Pricing
    subtotal = Column(Numeric(10, 2), nullable=False)
    shipping_fee = Column(Numeric(10, 2), default=0.00)
    tax = Column(Numeric(10, 2), default=0.00)
    discount = Column(Numeric(10, 2), default=0.00)
    total_amount = Column(Numeric(10, 2), nullable=False)

    # Payment
    payment_method = Column(String(50), nullable=True)
    payment_status = Column(
        Enum(PaymentStatusEnum),
        default=PaymentStatusEnum.PENDING,
        nullable=False,
        index=True
    )
    payment_reference = Column(String(100), nullable=True)

    # Status
    order_status = Column(
        Enum(OrderStatusEnum),
        default=OrderStatusEnum.PENDING,
        nullable=False,
        index=True
    )

    # Tracking
    tracking_number = Column(String(100), nullable=True, index=True)
    estimated_delivery = Column(Date, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    # Notes
    notes = Column(Text, nullable=True)

    # Delivery assignment
    delivery_id = Column(UUID(as_uuid=True), nullable=True)  # Links to deliveries table

    # Relationships
    customer = relationship("User", foreign_keys=[customer_id])
    items = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint('total_amount >= 0', name='non_negative_total'),
    )

    def __repr__(self):
        return f"<ProductOrder {self.id} - {self.order_status}>"


# ============================================
# ORDER ITEM MODEL
# ============================================

class OrderItem(BaseModel):
    """Individual items in an order"""

    __tablename__ = "order_items"

    order_id = Column(
        UUID(as_uuid=True),
        ForeignKey("product_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    product_id = Column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True
    )
    variant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="SET NULL"),
        nullable=True
    )
    vendor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("product_vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Order Details
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    total_price = Column(Numeric(10, 2), nullable=False)

    # Product Snapshot (store details at time of order)
    product_snapshot = Column(JSONB, nullable=False)

    # Relationships
    order = relationship("ProductOrder", back_populates="items")
    product = relationship("Product", back_populates="order_items")
    variant = relationship("ProductVariant", back_populates="order_items")
    vendor = relationship("ProductVendor")

    delivery_requested = Column(Boolean, default=False)
    delivery_id = Column(UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        CheckConstraint('quantity > 0', name='positive_quantity'),
        CheckConstraint('unit_price > 0', name='positive_unit_price'),
    )

    def __repr__(self):
        return f"<OrderItem {self.order_id} - Qty: {self.quantity}>"


# ============================================
# SHOPPING CART MODEL (Optional - can use session storage)
# ============================================

class CartItem(BaseModel):
    """Shopping cart items"""

    __tablename__ = "cart_items"

    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    product_id = Column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False
    )
    variant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="CASCADE"),
        nullable=True
    )

    quantity = Column(Integer, nullable=False, default=1)

    # Relationships
    customer = relationship("User")
    product = relationship("Product")
    variant = relationship("ProductVariant")

    __table_args__ = (
        CheckConstraint('quantity > 0', name='positive_cart_quantity'),
        UniqueConstraint('customer_id', 'product_id', 'variant_id', name='unique_cart_item'),
    )

    def __repr__(self):
        return f"<CartItem {self.customer_id} - {self.product_id}>"


# ============================================
# WISHLIST MODEL
# ============================================

class Wishlist(BaseModel):
    """User wishlists"""

    __tablename__ = "wishlists"

    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    product_id = Column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False
    )

    # Relationships
    customer = relationship("User")
    product = relationship("Product")

    __table_args__ = (
        UniqueConstraint('customer_id', 'product_id', name='unique_wishlist_item'),
    )

    def __repr__(self):
        return f"<Wishlist {self.customer_id} - {self.product_id}>"