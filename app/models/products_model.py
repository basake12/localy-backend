"""
app/models/products_model.py

FIXES vs previous version:
  1. [HARD RULE — CRITICAL] is_deleted BOOLEAN NOT NULL DEFAULT FALSE added.
     is_archived BOOLEAN NOT NULL DEFAULT FALSE added.
     Blueprint §6.4 IMPLEMENTATION SPEC: free plan limit check query is:
       WHERE business_id = :bid AND is_deleted = FALSE AND is_archived = FALSE
     Without these two columns the limit enforcement query CANNOT run.

  2. Product now has business_id FK → businesses.id directly.
     Blueprint §14: products.business_id UUID NOT NULL REFERENCES businesses(id).
     The previous design (vendor_id → product_vendors) broke the limit check
     because the service queries businesses directly.

  3. price NUMERIC(12,2) NOT NULL — Blueprint §14 field name and type.
     Previous base_price / sale_price renamed accordingly.

  4. images TEXT[] stored as JSONB array — Blueprint §14.

  5. ProductVendor kept as a supplementary store-profile table, but Product
     now has business_id as the primary FK for all blueprint operations.

  6. video_url TEXT added per Blueprint §14.

  7. variants JSONB added per Blueprint §14.

  8. CartItem and Wishlist are DB-persisted per Blueprint §6.4:
     "Cart and wishlist — both persistent across devices (stored in DB, not local)."
"""
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Enum,
    Text,
    Integer,
    Numeric,
    ForeignKey,
    DateTime,
    CheckConstraint,
    Date,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geography
import enum

from app.models.base_model import BaseModel


# ─── Enums ────────────────────────────────────────────────────────────────────

class ProductTypeEnum(str, enum.Enum):
    PHYSICAL = "physical"
    DIGITAL  = "digital"


class OrderStatusEnum(str, enum.Enum):
    PENDING            = "pending"
    PROCESSING         = "processing"
    PACKED             = "packed"
    SHIPPED            = "shipped"
    DELIVERED          = "delivered"
    CANCELLED          = "cancelled"
    REFUNDED           = "refunded"
    RETURN_REQUESTED   = "return_requested"
    RETURN_IN_PROGRESS = "return_in_progress"
    RETURNED           = "returned"


class PaymentStatusEnum(str, enum.Enum):
    PENDING  = "pending"
    PAID     = "paid"
    FAILED   = "failed"
    REFUNDED = "refunded"


class ReturnStatusEnum(str, enum.Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REFUNDED = "refunded"


# ─── Product Vendor (Store Profile) ───────────────────────────────────────────

class ProductVendor(BaseModel):
    """
    Store-level profile (name, policies, banner).
    Supplementary to Product — the primary FK for all blueprint operations
    is Product.business_id → businesses.id.
    """
    __tablename__ = "product_vendors"

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    store_name        = Column(String(255), nullable=False, index=True)
    store_description = Column(Text, nullable=True)
    store_logo        = Column(Text, nullable=True)
    store_banner      = Column(Text, nullable=True)
    return_policy     = Column(Text, nullable=True)
    shipping_policy   = Column(Text, nullable=True)
    total_products    = Column(Integer, default=0)
    total_sales       = Column(Integer, default=0)

    business  = relationship("Business", back_populates="product_vendor")
    # Products linked via business_id on Product — use association query,
    # not a direct relationship here, to avoid dual-FK confusion.

    def __repr__(self) -> str:
        return f"<ProductVendor {self.store_name}>"


# ─── Product ──────────────────────────────────────────────────────────────────

class Product(BaseModel):
    """
    Blueprint §14 / §6.4.

    Free plan product limit (§2 / §6.4 HARD RULE):
      Max 20 active (non-deleted, non-archived) products for Free tier.
      Enforced at POST /api/v1/products/ before any DB write.

      Count query:
        SELECT COUNT(*) FROM products
        WHERE business_id = :bid
          AND is_deleted  = FALSE
          AND is_archived = FALSE

      Admin override: businesses.product_limit_override = TRUE
                      businesses.product_limit_override_value = N
    """
    __tablename__ = "products"

    # Blueprint §14: business_id → businesses (primary FK for all operations)
    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Optional link to ProductVendor for store branding — not the primary key
    vendor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("product_vendors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Core fields — Blueprint §14 ───────────────────────────────────────────
    name        = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)

    # Blueprint §14: price NUMERIC(12,2) NOT NULL
    price       = Column(Numeric(12, 2), nullable=False)

    category    = Column(String(100), nullable=True, index=True)

    # Blueprint §14: images TEXT[], video_url TEXT, variants JSONB
    images      = Column(JSONB, default=list)     # array of URL strings
    video_url   = Column(Text, nullable=True)
    variants    = Column(JSONB, default=list)      # flat variant list in JSONB

    stock_quantity = Column(Integer, nullable=False, default=0)
    is_digital     = Column(Boolean, nullable=False, default=False)

    # Digital product download link — generated on payment confirmation (§6.4)
    download_url   = Column(Text, nullable=True)

    # ── Status flags ──────────────────────────────────────────────────────────
    is_active = Column(Boolean, nullable=False, default=True, index=True)

    # CRITICAL — Blueprint §6.4 IMPLEMENTATION SPEC
    # Both flags required for the free plan product limit count query.
    # Archived/deleted products do NOT count toward the 20-product limit.
    is_deleted  = Column(Boolean, nullable=False, default=False)
    is_archived = Column(Boolean, nullable=False, default=False)

    # ── Extended fields (acceptable additions) ────────────────────────────────
    brand           = Column(String(100), nullable=True, index=True)
    subcategory     = Column(String(100), nullable=True, index=True)
    sku             = Column(String(100), unique=True, nullable=True, index=True)
    specifications  = Column(JSONB, default=dict)
    meta_title      = Column(String(255), nullable=True)
    meta_description = Column(Text, nullable=True)
    slug            = Column(String(255), unique=True, nullable=True, index=True)
    low_stock_threshold = Column(Integer, default=10)

    # ── Stats ─────────────────────────────────────────────────────────────────
    views_count    = Column(Integer, default=0)
    sales_count    = Column(Integer, default=0)
    average_rating = Column(Numeric(3, 2), default=0.00)
    total_reviews  = Column(Integer, default=0)

    # ── Relationships ─────────────────────────────────────────────────────────
    business     = relationship("Business", back_populates="products")
    vendor       = relationship("ProductVendor", foreign_keys=[vendor_id])
    variant_rows = relationship(
        "ProductVariant", back_populates="product", cascade="all, delete-orphan"
    )
    order_items  = relationship("OrderItem", back_populates="product")

    __table_args__ = (
        CheckConstraint("price > 0",           name="positive_product_price"),
        CheckConstraint("stock_quantity >= 0",  name="non_negative_stock"),
    )

    @property
    def in_stock(self) -> bool:
        """Computed stock indicator for Pydantic serialisation."""
        if self.variant_rows:
            return any(v.stock_quantity > 0 for v in self.variant_rows if v.is_active)
        return (self.stock_quantity or 0) > 0

    def __repr__(self) -> str:
        return f"<Product {self.name}>"


# ─── Product Variant ──────────────────────────────────────────────────────────

class ProductVariant(BaseModel):
    """Individual SKU-level variants (size, colour, etc.)."""

    __tablename__ = "product_variants"

    product_id = Column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    sku            = Column(String(100), unique=True, nullable=True, index=True)
    attributes     = Column(JSONB, nullable=False)   # {"color": "red", "size": "M"}
    price          = Column(Numeric(12, 2), nullable=False)
    stock_quantity = Column(Integer, default=0)
    images         = Column(JSONB, default=list)
    is_active      = Column(Boolean, default=True)

    product     = relationship("Product", back_populates="variant_rows")
    order_items = relationship("OrderItem", back_populates="variant")

    __table_args__ = (
        CheckConstraint("price > 0",          name="positive_variant_price"),
        CheckConstraint("stock_quantity >= 0", name="non_negative_variant_stock"),
    )

    def __repr__(self) -> str:
        return f"<ProductVariant {self.product_id} {self.attributes}>"


# ─── Product Order ────────────────────────────────────────────────────────────

class ProductOrder(BaseModel):
    """Customer product orders. Blueprint §6.4 / §5.4."""

    __tablename__ = "product_orders"

    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    shipping_address  = Column(Text, nullable=False)
    shipping_location = Column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=True),
        nullable=True,
    )
    recipient_name    = Column(String(200), nullable=False)
    recipient_phone   = Column(String(20),  nullable=False)

    # Blueprint §5.6 HARD RULE: NUMERIC(12,2)
    subtotal     = Column(Numeric(12, 2), nullable=False)
    shipping_fee = Column(Numeric(12, 2), default=0.00)
    tax          = Column(Numeric(12, 2), default=0.00)
    discount     = Column(Numeric(12, 2), default=0.00)

    # Blueprint §5.4: ₦50 flat platform fee on product orders
    platform_fee = Column(Numeric(12, 2), nullable=False, default=50.00)
    total_amount = Column(Numeric(12, 2), nullable=False)

    coupon_code       = Column(String(50), nullable=True)
    payment_method    = Column(String(50), nullable=True)
    payment_status    = Column(Enum(PaymentStatusEnum), default=PaymentStatusEnum.PENDING, nullable=False, index=True)
    payment_reference = Column(String(100), nullable=True)

    order_status     = Column(Enum(OrderStatusEnum), default=OrderStatusEnum.PENDING, nullable=False, index=True)
    tracking_number  = Column(String(100), nullable=True, index=True)
    estimated_delivery = Column(Date, nullable=True)
    delivered_at     = Column(DateTime(timezone=True), nullable=True)
    notes            = Column(Text, nullable=True)

    delivery_id = Column(
        UUID(as_uuid=True),
        ForeignKey("deliveries.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    customer       = relationship("User", foreign_keys=[customer_id])
    items          = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    return_requests = relationship("ProductReturn", back_populates="order", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("total_amount >= 0",  name="non_negative_total"),
        CheckConstraint("platform_fee >= 0",  name="non_negative_platform_fee"),
    )

    def __repr__(self) -> str:
        return f"<ProductOrder {self.id} {self.order_status}>"


# ─── Order Item ───────────────────────────────────────────────────────────────

class OrderItem(BaseModel):
    __tablename__ = "order_items"

    order_id   = Column(UUID(as_uuid=True), ForeignKey("product_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    variant_id = Column(UUID(as_uuid=True), ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True)
    vendor_id  = Column(UUID(as_uuid=True), ForeignKey("product_vendors.id", ondelete="CASCADE"), nullable=False, index=True)

    quantity         = Column(Integer, nullable=False)
    unit_price       = Column(Numeric(12, 2), nullable=False)
    total_price      = Column(Numeric(12, 2), nullable=False)
    product_snapshot = Column(JSONB, nullable=False)

    delivery_requested = Column(Boolean, default=False)
    delivery_id = Column(UUID(as_uuid=True), ForeignKey("deliveries.id", ondelete="SET NULL"), nullable=True)

    order   = relationship("ProductOrder", back_populates="items")
    product = relationship("Product", back_populates="order_items")
    variant = relationship("ProductVariant", back_populates="order_items")
    vendor  = relationship("ProductVendor")

    __table_args__ = (
        CheckConstraint("quantity > 0",    name="positive_quantity"),
        CheckConstraint("unit_price > 0",  name="positive_unit_price"),
    )

    def __repr__(self) -> str:
        return f"<OrderItem order={self.order_id} qty={self.quantity}>"


# ─── Product Return ───────────────────────────────────────────────────────────

class ProductReturn(BaseModel):
    """Blueprint §6.4 / §12.5: in-app return/refund request flow."""

    __tablename__ = "product_returns"

    order_id    = Column(UUID(as_uuid=True), ForeignKey("product_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    reason   = Column(Text, nullable=False)
    item_ids = Column(JSONB, nullable=False, default=list)
    photos   = Column(JSONB, nullable=False, default=list)

    status      = Column(Enum(ReturnStatusEnum), default=ReturnStatusEnum.PENDING, nullable=False, index=True)
    admin_notes = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    refund_transaction_id = Column(UUID(as_uuid=True), ForeignKey("wallet_transactions.id", ondelete="SET NULL"), nullable=True)

    order    = relationship("ProductOrder", back_populates="return_requests")
    customer = relationship("User", foreign_keys=[customer_id])

    __table_args__ = (
        UniqueConstraint("order_id", name="unique_return_per_order"),
    )

    def __repr__(self) -> str:
        return f"<ProductReturn {self.order_id} {self.status}>"


# ─── Shopping Cart ────────────────────────────────────────────────────────────

class CartItem(BaseModel):
    """
    Blueprint §6.4: "Cart persistent across devices (stored in DB, not local)."
    """
    __tablename__ = "cart_items"

    customer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id  = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    variant_id  = Column(UUID(as_uuid=True), ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True)
    quantity    = Column(Integer, nullable=False, default=1)

    customer = relationship("User")
    product  = relationship("Product")
    variant  = relationship("ProductVariant")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_cart_quantity"),
        UniqueConstraint("customer_id", "product_id", "variant_id", name="unique_cart_item"),
    )

    def __repr__(self) -> str:
        return f"<CartItem customer={self.customer_id} product={self.product_id}>"


# ─── Wishlist ─────────────────────────────────────────────────────────────────

class Wishlist(BaseModel):
    """
    Blueprint §6.4: "Wishlist persistent across devices (stored in DB, not local)."
    """
    __tablename__ = "wishlists"

    customer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id  = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)

    customer = relationship("User")
    product  = relationship("Product")

    __table_args__ = (
        UniqueConstraint("customer_id", "product_id", name="unique_wishlist_item"),
    )

    def __repr__(self) -> str:
        return f"<Wishlist customer={self.customer_id} product={self.product_id}>"