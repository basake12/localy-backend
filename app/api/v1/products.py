from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from uuid import UUID, uuid4

from app.core.database import get_db
from app.dependencies import (
    get_current_active_user,
    require_customer,
    require_business,
    get_pagination_params,
)
from app.schemas.common_schema import SuccessResponse
from app.schemas.products_schema import (
    VendorCreateRequest,
    VendorUpdateRequest,
    VendorResponse,
    ProductCreateRequest,
    ProductUpdateRequest,
    ProductResponse,
    ProductListResponse,
    InventoryUpdateRequest,
    VariantCreateRequest,
    VariantUpdateRequest,
    VariantResponse,
    CartItemAddRequest,
    CartItemUpdateRequest,
    CartItemResponse,
    CartResponse,
    OrderCreateRequest,
    OrderResponse,
    OrderListResponse,
    OrderStatusUpdateRequest,
    ReturnRequest,
    ReturnResponse,
    WishlistItemResponse,
    WishlistResponse,
    ProductSearchFilters,
    VendorAnalyticsSummary,
    PRODUCT_PLATFORM_FEE,
)
from app.services.product_service import product_service
from app.crud.products_crud import (
    product_vendor_crud,
    product_crud,
    product_variant_crud,
    cart_crud,
    product_order_crud,
    wishlist_crud,
)
from app.models.products_model import Product
from app.models.business_model import Business
from app.models.user_model import User
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)

router = APIRouter()


# ============================================================
# HELPERS
# ============================================================

def _get_vendor_or_404(db: Session, user: User):
    """
    Shared helper — get vendor for current business user.
    Uses direct db.query(Business) — business_crud is async (AsyncCRUDBase)
    and cannot be called from a sync router.
    """
    business = db.query(Business).filter(Business.user_id == user.id).first()
    if not business:
        raise NotFoundException("Business")
    vendor = product_vendor_crud.get_by_business_id(db, business_id=business.id)
    if not vendor:
        raise NotFoundException("Store not found. Create a store first.")
    return vendor


def _assert_owns_product(vendor, product):
    """Raise PermissionDeniedException if vendor doesn't own the product."""
    if product.vendor_id != vendor.id:
        raise PermissionDeniedException("You don't own this product")


def _enum_val(v) -> str:
    """
    FIX: Safely extract a plain string from either a str or a SQLAlchemy/Python
    Enum instance.  Without this, ORM enum members serialise as
    'OrderStatusEnum.processing' instead of 'processing' when accessed outside
    of Pydantic's use_enum_values path (e.g. manual dict construction).
    """
    return v.value if hasattr(v, "value") else str(v)


def _build_wishlist_item_dict(wishlist_item) -> dict:
    """
    Build a WishlistItemResponse-compatible dict from a Wishlist ORM object.

    Why this exists:
      ProductListResponse requires two fields that have no backing DB column:
        - in_stock   → computed from product.stock_quantity > 0
        - vendor_name → lives on the related ProductVendor, not on Product

      Passing raw ORM objects directly to FastAPI would raise
      ResponseValidationError on both fields. We build an explicit dict
      instead, the same pattern used by the cart endpoints for item_total.

    Precondition: wishlist_item.product and wishlist_item.product.vendor must
    already be eagerly loaded (joinedload) before this is called.
    """
    p = wishlist_item.product
    return {
        "id": wishlist_item.id,
        "product_id": wishlist_item.product_id,
        "created_at": wishlist_item.created_at,
        "product": {
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "brand": p.brand,
            "base_price": p.base_price,
            "sale_price": p.sale_price,
            "images": p.images or [],
            "average_rating": p.average_rating,
            "in_stock": p.stock_quantity > 0,                          # computed — no DB column
            "vendor_id": p.vendor_id,
            "vendor_name": p.vendor.store_name if p.vendor else None,  # from joined relation
        },
    }


# ============================================================
# IMPORTANT: Route registration order — static paths FIRST,
# parameterised paths LAST. FastAPI matches top-to-bottom and
# a wildcard like /{product_id} shadows everything below it.
# ============================================================


# ────────────────────────────────────────────────────────────
# BLOCK 1 — FULLY STATIC PATHS (no path parameters)
# ────────────────────────────────────────────────────────────

@router.get("/categories/list", response_model=SuccessResponse[List[str]])
def get_categories(*, db: Session = Depends(get_db)) -> dict:
    """Get all active product categories."""
    from sqlalchemy import distinct
    categories = db.query(distinct(Product.category)).filter(
        Product.is_active == True
    ).all()
    return {"success": True, "data": [c[0] for c in categories if c[0]]}


@router.get("/brands/list", response_model=SuccessResponse[List[str]])
def get_brands(
        *, db: Session = Depends(get_db),
        category: Optional[str] = Query(None)
) -> dict:
    """Get all brands, optionally filtered by category."""
    from sqlalchemy import distinct
    query = db.query(distinct(Product.brand)).filter(
        Product.is_active == True, Product.brand.isnot(None)
    )
    if category:
        query = query.filter(Product.category == category)
    brands = query.all()
    return {"success": True, "data": [b[0] for b in brands if b[0]]}


@router.post("/search", response_model=SuccessResponse[List[ProductListResponse]])
def search_products(
        *, db: Session = Depends(get_db),
        search_params: ProductSearchFilters,
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Search products with filters. Public endpoint."""
    location = None
    if search_params.location:
        location = (search_params.location.latitude, search_params.location.longitude)

    results = product_service.search_products(
        db,
        query_text=search_params.query,
        category=search_params.category,
        subcategory=search_params.subcategory,
        brand=search_params.brand,
        min_price=search_params.min_price,
        max_price=search_params.max_price,
        in_stock_only=search_params.in_stock_only,
        location=location,
        # FIX: Blueprint §3.1 — default discovery radius is 5 km, not 10 km.
        radius_km=search_params.radius_km or 5.0,
        sort_by=search_params.sort_by or "created_at",
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": results}


# ── Vendor ──────────────────────────────────────────────────

@router.get("/vendors/my", response_model=SuccessResponse[VendorResponse])
def get_my_vendor(
        *, db: Session = Depends(get_db),
        current_user: User = Depends(require_business)
) -> dict:
    """Get current business's vendor/store profile."""
    vendor = _get_vendor_or_404(db, current_user)
    return {"success": True, "data": vendor}


@router.post("/vendors", response_model=SuccessResponse[VendorResponse], status_code=status.HTTP_201_CREATED)
def create_vendor(
        *, db: Session = Depends(get_db),
        vendor_in: VendorCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Create a product vendor/store for the current business."""
    business = db.query(Business).filter(Business.user_id == current_user.id).first()
    if not business:
        raise NotFoundException("Business")
    if business.category != "products":
        raise ValidationException("Only products category businesses can create stores")

    existing = product_vendor_crud.get_by_business_id(db, business_id=business.id)
    if existing:
        raise ValidationException("Store already exists for this business")

    vendor_data = vendor_in.model_dump()
    vendor_data["business_id"] = business.id
    vendor = product_vendor_crud.create_from_dict(db, obj_in=vendor_data)
    return {"success": True, "data": vendor}


@router.patch("/vendors/me", response_model=SuccessResponse[VendorResponse])
def update_my_vendor(
        *, db: Session = Depends(get_db),
        vendor_in: VendorUpdateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Update current vendor store name, logo, banner, policies."""
    vendor = _get_vendor_or_404(db, current_user)
    update_data = vendor_in.model_dump(exclude_unset=True)
    vendor = product_vendor_crud.update(db, db_obj=vendor, obj_in=update_data)
    return {"success": True, "data": vendor}


# ── Products ─────────────────────────────────────────────────

@router.post("/", response_model=SuccessResponse[ProductResponse], status_code=status.HTTP_201_CREATED)
def create_product(
        *, db: Session = Depends(get_db),
        product_in: ProductCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Create a new product listing."""
    vendor = _get_vendor_or_404(db, current_user)

    product_data = product_in.model_dump()
    product_data["vendor_id"] = vendor.id

    import re
    slug_base = re.sub(r'[^\w\s-]', '', product_data['name'].lower())
    slug_base = re.sub(r'[-\s]+', '-', slug_base)
    product_data["slug"] = f"{slug_base}-{str(uuid4())[:8]}"

    sku = product_data.get("sku")
    if sku:
        archived = db.query(Product).filter(
            Product.sku == sku,
            Product.vendor_id == vendor.id,
            Product.is_active == False
        ).first()
        if archived:
            for k, v in product_data.items():
                setattr(archived, k, v)
            archived.is_active = True
            db.commit()
            db.refresh(archived)
            return {"success": True, "data": archived}

    try:
        product = product_crud.create_from_dict(db, obj_in=product_data)
        vendor.total_products += 1
        db.commit()
    except IntegrityError as e:
        db.rollback()
        if "ix_products_sku" in str(e):
            raise HTTPException(status_code=400, detail="A product with this SKU already exists.")
        raise

    return {"success": True, "data": product}


@router.get("/my/products", response_model=SuccessResponse[List[ProductResponse]])
def get_my_products(
        *, db: Session = Depends(get_db),
        current_user: User = Depends(require_business),
        pagination: dict = Depends(get_pagination_params),
        active_only: bool = Query(True)
) -> dict:
    """Get all products for current vendor."""
    vendor = _get_vendor_or_404(db, current_user)
    products = product_crud.get_by_vendor(
        db, vendor_id=vendor.id,
        skip=pagination["skip"], limit=pagination["limit"],
        active_only=active_only
    )
    return {"success": True, "data": products}


@router.get("/my/inventory/low-stock", response_model=SuccessResponse[List[ProductResponse]])
def get_low_stock(
        *, db: Session = Depends(get_db),
        current_user: User = Depends(require_business)
) -> dict:
    """Get products at or below low stock threshold — for dashboard alerts."""
    vendor = _get_vendor_or_404(db, current_user)
    products = product_crud.get_low_stock(db, vendor_id=vendor.id)
    return {"success": True, "data": products}


@router.get("/analytics/summary", response_model=SuccessResponse[VendorAnalyticsSummary])
def get_analytics_summary(
        *, db: Session = Depends(get_db),
        current_user: User = Depends(require_business)
) -> dict:
    """Vendor dashboard KPI summary: revenue, orders, top products."""
    vendor = _get_vendor_or_404(db, current_user)
    analytics = product_crud.get_vendor_analytics(db, vendor_id=vendor.id)
    return {"success": True, "data": analytics}


# ── Cart ─────────────────────────────────────────────────────

@router.get("/cart/my", response_model=SuccessResponse[CartResponse])
def get_my_cart(
        *, db: Session = Depends(get_db),
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Get current customer's cart with line-item totals and grand subtotal.

    calculate_cart_total() returns items as serialization-ready dicts that
    include item_total — a response-only computed field (price × quantity) that
    has no backing column on CartItem. We forward those dicts directly rather
    than passing raw ORM objects, which would raise ResponseValidationError.
    """
    cart_data = product_service.calculate_cart_total(db, customer_id=current_user.id)
    return {
        "success": True,
        "data": {
            "items": cart_data["items"],      # already List[Dict] matching CartItemResponse
            "subtotal": cart_data["subtotal"],
            "total_items": cart_data["total_items"],
        }
    }


@router.post("/cart", response_model=SuccessResponse[CartItemResponse], status_code=status.HTTP_201_CREATED)
def add_to_cart(
        *, db: Session = Depends(get_db),
        cart_item_in: CartItemAddRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Add item to cart. Returns the updated cart item with item_total.

    Flow:
      1. Validate product exists and is active
      2. Check available stock
      3. Persist cart item (insert or increment quantity)
      4. Reload with joinedload so product + variant are available
      5. Build CartItemResponse-compatible dict via _build_cart_item_dict()

    Step 4 is required because add_to_cart() returns a post-refresh CartItem
    whose relationships are NOT eagerly loaded. Passing it directly to FastAPI
    would raise ResponseValidationError on item_total (no backing column).
    """
    product = product_crud.get(db, id=cart_item_in.product_id)
    if not product or not product.is_active:
        raise NotFoundException("Product")

    if not product_crud.check_stock(
            db, product_id=cart_item_in.product_id,
            variant_id=cart_item_in.variant_id,
            quantity=cart_item_in.quantity
    ):
        raise ValidationException("Insufficient stock")

    raw_item = cart_crud.add_to_cart(
        db,
        customer_id=current_user.id,
        product_id=cart_item_in.product_id,
        variant_id=cart_item_in.variant_id,
        quantity=cart_item_in.quantity
    )

    # Reload with all relationships eagerly populated before serialization
    item = cart_crud.get_cart_item_with_relations(db, cart_item_id=raw_item.id)
    if not item:
        raise NotFoundException("Cart item")

    return {
        "success": True,
        "data": product_service._build_cart_item_dict(item),
    }


# DELETE /cart/clear MUST be registered BEFORE DELETE /cart/{cart_item_id}.
# Otherwise the literal string "clear" is matched as a UUID cart_item_id → 422.
@router.delete("/cart/clear", response_model=SuccessResponse[dict])
def clear_cart(
        *, db: Session = Depends(get_db),
        current_user: User = Depends(require_customer)
) -> dict:
    """Clear entire cart."""
    cart_crud.clear_cart(db, customer_id=current_user.id)
    return {"success": True, "data": {"message": "Cart cleared"}}


@router.put("/cart/{cart_item_id}", response_model=SuccessResponse[CartItemResponse])
def update_cart_item(
        *, db: Session = Depends(get_db),
        cart_item_id: UUID,
        update_data: CartItemUpdateRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Update cart item quantity. Returns the updated item with item_total.

    Same reload pattern as add_to_cart — update_quantity() returns a bare ORM
    object; we re-fetch with joinedload before building the response dict.
    """
    cart_item = cart_crud.get(db, id=cart_item_id)
    if not cart_item:
        raise NotFoundException("Cart item")
    if cart_item.customer_id != current_user.id:
        raise PermissionDeniedException()

    if not product_crud.check_stock(
            db, product_id=cart_item.product_id,
            variant_id=cart_item.variant_id,
            quantity=update_data.quantity
    ):
        raise ValidationException("Insufficient stock")

    cart_crud.update_quantity(db, cart_item_id=cart_item_id, quantity=update_data.quantity)

    # Reload with relationships after the update
    item = cart_crud.get_cart_item_with_relations(db, cart_item_id=cart_item_id)
    if not item:
        raise NotFoundException("Cart item")

    return {
        "success": True,
        "data": product_service._build_cart_item_dict(item),
    }


@router.delete("/cart/{cart_item_id}", response_model=SuccessResponse[dict])
def remove_from_cart(
        *, db: Session = Depends(get_db),
        cart_item_id: UUID,
        current_user: User = Depends(require_customer)
) -> dict:
    """Remove a single item from cart."""
    cart_item = cart_crud.get(db, id=cart_item_id)
    if not cart_item:
        raise NotFoundException("Cart item")
    if cart_item.customer_id != current_user.id:
        raise PermissionDeniedException()
    cart_crud.delete(db, id=cart_item_id)
    return {"success": True, "data": {"message": "Item removed"}}


# ── Wishlist ──────────────────────────────────────────────────

@router.get("/wishlist", response_model=SuccessResponse[WishlistResponse])
def get_wishlist(
        *, db: Session = Depends(get_db),
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Get customer's wishlist.

    wishlist_crud.get_by_customer() must joinedload Wishlist.product and
    Product.vendor so that _build_wishlist_item_dict() can access both
    in_stock (computed from stock_quantity) and vendor_name (from vendor.store_name)
    without issuing lazy-load queries on a closed session.

    Raw ORM objects are never passed directly to FastAPI here — doing so would
    raise ResponseValidationError because ProductListResponse.in_stock and
    ProductListResponse.vendor_name have no backing columns on the Product model.
    """
    items = wishlist_crud.get_by_customer(db, customer_id=current_user.id)
    serialized = [_build_wishlist_item_dict(w) for w in items]
    return {"success": True, "data": {"items": serialized, "total": len(serialized)}}


# ── Wishlist (parameterised) ──────────────────────────────────

@router.post("/wishlist/{product_id}", response_model=SuccessResponse[dict], status_code=status.HTTP_201_CREATED)
def add_to_wishlist(
        *, db: Session = Depends(get_db),
        product_id: UUID,
        current_user: User = Depends(require_customer)
) -> dict:
    """Add product to wishlist. Idempotent — safe to call multiple times."""
    product = product_crud.get(db, id=product_id)
    if not product or not product.is_active:
        raise NotFoundException("Product")
    wishlist_crud.add(db, customer_id=current_user.id, product_id=product_id)
    return {"success": True, "data": {"message": "Added to wishlist"}}


@router.delete("/wishlist/{product_id}", response_model=SuccessResponse[dict])
def remove_from_wishlist(
        *, db: Session = Depends(get_db),
        product_id: UUID,
        current_user: User = Depends(require_customer)
) -> dict:
    """Remove product from wishlist."""
    wishlist_crud.remove(db, customer_id=current_user.id, product_id=product_id)
    return {"success": True, "data": {"message": "Removed from wishlist"}}


# ── Orders (static paths) ────────────────────────────────────

@router.post("/orders", response_model=SuccessResponse[OrderResponse], status_code=status.HTTP_201_CREATED)
def create_order(
        *, db: Session = Depends(get_db),
        order_in: OrderCreateRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Checkout and create order. Processes wallet payment atomically.
    Stock is only reduced after wallet balance check passes.

    recipient_name and recipient_phone are optional in the request body.
    If omitted, they are resolved from the customer's profile:
      - name  → CustomerProfile.first_name + last_name
      - phone → User.phone

    Platform fee (₦50) is deducted by the service before crediting business
    wallet — blueprint §4.4. It is never negotiable and always shown in the
    order summary returned to the client.

    NOTE FOR SERVICE LAYER (checkout_and_pay must apply these two fixes):
      1. order_status must be set to "pending" (lowercase) — NOT "PENDING" or
         "PROCESSING". The DB enum orderstatusenum only accepts lowercase values.
         Use the VALID_ORDER_STATUSES constant from products_schema.py.
      2. payment_reference must be stored as None (SQL NULL) if the payment
         gateway did not return a reference — NOT str(None) which inserts the
         literal string "None". Use: payment_reference=reference or None
    """
    from app.models.user_model import CustomerProfile

    recipient_name = order_in.recipient_name
    recipient_phone = order_in.recipient_phone

    if not recipient_name or not recipient_phone:
        # Explicitly query profile — lazy-loaded relationships are not reliable
        # in a sync context (same pattern used in food.py create_food_order).
        profile = db.query(CustomerProfile).filter(
            CustomerProfile.user_id == current_user.id
        ).first()

        if profile:
            if not recipient_name:
                recipient_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip()
            if not recipient_phone:
                recipient_phone = current_user.phone or ""

    # Guard: fail loudly rather than storing empty strings in the DB
    if not recipient_name:
        raise ValidationException(
            "recipient_name could not be resolved. "
            "Add your name to your profile or include recipient_name in the request."
        )
    if not recipient_phone:
        raise ValidationException(
            "recipient_phone could not be resolved. "
            "Add your phone number to your profile or include recipient_phone in the request."
        )

    items = [item.model_dump() for item in order_in.items]
    order = product_service.checkout_and_pay(
        db, current_user=current_user,
        items=items,
        shipping_address=order_in.shipping_address,
        recipient_name=recipient_name,
        recipient_phone=recipient_phone,
        payment_method=order_in.payment_method,
        coupon_code=order_in.coupon_code,
        notes=order_in.notes,
    )
    cart_crud.clear_cart(db, customer_id=current_user.id)
    return {"success": True, "data": order}


@router.get("/orders/my", response_model=SuccessResponse[List[OrderListResponse]])
def get_my_orders(
        *, db: Session = Depends(get_db),
        current_user: User = Depends(require_customer),
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get current customer's order history."""
    orders = product_order_crud.get_customer_orders(
        db, customer_id=current_user.id,
        skip=pagination["skip"], limit=pagination["limit"]
    )
    # FIX: Use _enum_val() to safely extract string from both plain str and
    # SQLAlchemy Enum instances. Previously `o.order_status` could serialise
    # as "OrderStatusEnum.processing" if the ORM returned an enum object.
    order_list = [
        {
            "id": o.id,
            "order_status": _enum_val(o.order_status),
            "payment_status": _enum_val(o.payment_status),
            "total_amount": o.total_amount,
            "total_items": len(o.items),
            "created_at": o.created_at,
        }
        for o in orders
    ]
    return {"success": True, "data": order_list}


@router.get("/orders/vendor/my", response_model=SuccessResponse[List[OrderResponse]])
def get_vendor_orders(
        *, db: Session = Depends(get_db),
        current_user: User = Depends(require_business),
        pagination: dict = Depends(get_pagination_params),
        order_status: Optional[str] = Query(None)
) -> dict:
    """Get all orders containing this vendor's products. Optionally filter by status."""
    # FIX: Normalise status filter to lowercase before passing to crud so that
    # "Processing" / "PROCESSING" from the query string don't silently return
    # empty results (the DB enum is lowercase).
    normalised_status = order_status.lower().strip() if order_status else None
    vendor = _get_vendor_or_404(db, current_user)
    orders = product_order_crud.get_vendor_orders(
        db, vendor_id=vendor.id,
        status=normalised_status,
        skip=pagination["skip"], limit=pagination["limit"]
    )
    return {"success": True, "data": orders}


# ── Orders (parameterised) ────────────────────────────────────

@router.get("/orders/{order_id}", response_model=SuccessResponse[OrderResponse])
def get_order_details(
        *, db: Session = Depends(get_db),
        order_id: UUID,
        current_user: User = Depends(get_current_active_user)
) -> dict:
    """Get full order details. Accessible by the placing customer or a vendor with items in it."""
    order = product_order_crud.get(db, id=order_id)
    if not order:
        raise NotFoundException("Order")

    if current_user.user_type == "customer":
        if order.customer_id != current_user.id:
            raise PermissionDeniedException()
    elif current_user.user_type == "business":
        vendor = _get_vendor_or_404(db, current_user)
        if not any(item.vendor_id == vendor.id for item in order.items):
            raise PermissionDeniedException()

    return {"success": True, "data": order}


@router.patch("/orders/{order_id}/status", response_model=SuccessResponse[OrderResponse])
def update_order_status(
        *, db: Session = Depends(get_db),
        order_id: UUID,
        status_in: OrderStatusUpdateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Update order status. Vendor can move orders through:
    pending → processing → packed → shipped → delivered

    status_in.status is already normalised to lowercase by the schema validator.
    """
    order = product_order_crud.get(db, id=order_id)
    if not order:
        raise NotFoundException("Order")

    vendor = _get_vendor_or_404(db, current_user)
    if not any(item.vendor_id == vendor.id for item in order.items):
        raise PermissionDeniedException()

    order = product_order_crud.update_order_status(
        db, order_id=order_id,
        new_status=status_in.status,   # already lowercase from schema validator
        tracking_number=status_in.tracking_number,
        estimated_delivery=status_in.estimated_delivery,
    )
    return {"success": True, "data": order}


@router.post("/orders/{order_id}/cancel", response_model=SuccessResponse[OrderResponse])
def cancel_order(
        *, db: Session = Depends(get_db),
        order_id: UUID,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Cancel order and restore stock. Only valid for pending/processing orders.
    Blueprint §13.1 — refund goes back to customer wallet within 24 hours.
    """
    order = product_order_crud.cancel_order(
        db, order_id=order_id, customer_id=current_user.id
    )
    return {"success": True, "data": order}


@router.get("/orders/tracking/{tracking_number}", response_model=SuccessResponse[OrderResponse])
def track_order(
        *, db: Session = Depends(get_db),
        tracking_number: str,
        current_user: User = Depends(get_current_active_user)
) -> dict:
    """Track order by tracking number."""
    order = product_order_crud.get_by_tracking_number(db, tracking_number=tracking_number)
    if not order:
        raise NotFoundException("Order")
    if order.customer_id != current_user.id:
        raise PermissionDeniedException()
    return {"success": True, "data": order}


# ── Returns & Refunds ────────────────────────────────────────
# FIX: ReturnRequest schema existed but no endpoint was wired up.
# Blueprint §11.4 requires an in-app return/refund request flow.

@router.post(
    "/orders/{order_id}/return",
    response_model=SuccessResponse[ReturnResponse],
    status_code=status.HTTP_201_CREATED,
)
def request_return(
        *, db: Session = Depends(get_db),
        order_id: UUID,
        return_in: ReturnRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Submit a return request for a delivered order.

    Rules (blueprint §11.4 / §13.1):
      - Only the customer who placed the order may request a return.
      - Disputes must be raised within 48 hours of the transaction.
      - Refunds are returned to the customer's Localy wallet within 24 hours
        of approved cancellation (handled by admin approval in admin panel).

    NOTE: This endpoint creates the return record and notifies admin.
    The actual refund credit is applied by admin after review — not immediately.
    Implement product_order_crud.create_return_request() in the CRUD layer to:
      1. Validate order belongs to customer and is in "delivered" status.
      2. Validate order_item IDs belong to this order.
      3. Insert a return record with status="pending".
      4. Notify admin via the support/admin notification channel.
    """
    return_record = product_order_crud.create_return_request(
        db,
        order_id=order_id,
        customer_id=current_user.id,
        reason=return_in.reason,
        item_ids=return_in.items,
        photos=return_in.photos,
    )
    return {"success": True, "data": return_record}


# ────────────────────────────────────────────────────────────
# BLOCK 2 — PARAMETERISED PATHS (wildcards — MUST come last)
# ────────────────────────────────────────────────────────────

@router.patch("/{product_id}", response_model=SuccessResponse[ProductResponse])
def update_product(
        *, db: Session = Depends(get_db),
        product_id: UUID,
        product_in: ProductUpdateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Update product fields (partial update)."""
    product = product_crud.get(db, id=product_id)
    if not product:
        raise NotFoundException("Product")
    vendor = _get_vendor_or_404(db, current_user)
    _assert_owns_product(vendor, product)

    update_data = product_in.model_dump(exclude_unset=True)
    product = product_crud.update(db, db_obj=product, obj_in=update_data)
    return {"success": True, "data": product}


@router.patch("/{product_id}/inventory", response_model=SuccessResponse[ProductResponse])
def update_inventory(
        *, db: Session = Depends(get_db),
        product_id: UUID,
        inventory_in: InventoryUpdateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Quick stock adjustment — no need to send full product update."""
    product = product_crud.get(db, id=product_id)
    if not product:
        raise NotFoundException("Product")
    vendor = _get_vendor_or_404(db, current_user)
    _assert_owns_product(vendor, product)

    update_data = inventory_in.model_dump(exclude_unset=True)
    product = product_crud.update(db, db_obj=product, obj_in=update_data)
    return {"success": True, "data": product}


@router.delete("/{product_id}", response_model=SuccessResponse[dict])
def delete_product(
        *, db: Session = Depends(get_db),
        product_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Soft-delete product (sets is_active=False)."""
    product = product_crud.get(db, id=product_id)
    if not product:
        raise NotFoundException("Product")
    vendor = _get_vendor_or_404(db, current_user)
    _assert_owns_product(vendor, product)

    product.is_active = False
    db.commit()
    return {"success": True, "data": {"message": "Product deleted"}}


@router.post("/{product_id}/variants", response_model=SuccessResponse[VariantResponse], status_code=status.HTTP_201_CREATED)
def create_variant(
        *, db: Session = Depends(get_db),
        product_id: UUID,
        variant_in: VariantCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Add a variant to an existing product."""
    product = product_crud.get(db, id=product_id)
    if not product:
        raise NotFoundException("Product")
    vendor = _get_vendor_or_404(db, current_user)
    _assert_owns_product(vendor, product)

    variant_data = variant_in.model_dump()
    variant_data["product_id"] = product_id
    variant = product_variant_crud.create_from_dict(db, obj_in=variant_data)
    return {"success": True, "data": variant}


@router.get("/{product_id}/variants", response_model=SuccessResponse[List[VariantResponse]])
def get_product_variants(
        *, db: Session = Depends(get_db), product_id: UUID
) -> dict:
    """Get all active variants for a product. Public endpoint."""
    variants = product_variant_crud.get_by_product(db, product_id=product_id)
    return {"success": True, "data": variants}


@router.patch("/variants/{variant_id}", response_model=SuccessResponse[VariantResponse])
def update_variant(
        *, db: Session = Depends(get_db),
        variant_id: UUID,
        variant_in: VariantUpdateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Update variant price, stock, attributes, or images."""
    variant = product_variant_crud.get(db, id=variant_id)
    if not variant:
        raise NotFoundException("Variant")
    vendor = _get_vendor_or_404(db, current_user)
    product = product_crud.get(db, id=variant.product_id)
    _assert_owns_product(vendor, product)

    update_data = variant_in.model_dump(exclude_unset=True)
    variant = product_variant_crud.update(db, db_obj=variant, obj_in=update_data)
    return {"success": True, "data": variant}


@router.delete("/variants/{variant_id}", response_model=SuccessResponse[dict])
def delete_variant(
        *, db: Session = Depends(get_db),
        variant_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Remove a variant from a product."""
    variant = product_variant_crud.get(db, id=variant_id)
    if not variant:
        raise NotFoundException("Variant")
    vendor = _get_vendor_or_404(db, current_user)
    product = product_crud.get(db, id=variant.product_id)
    _assert_owns_product(vendor, product)

    variant.is_active = False
    db.commit()
    return {"success": True, "data": {"message": "Variant removed"}}


# GET /{product_id} is a wildcard — MUST be registered LAST
@router.get("/{product_id}", response_model=SuccessResponse[dict])
def get_product_details(
        *, db: Session = Depends(get_db), product_id: UUID
) -> dict:
    """
    Get full product detail with variants and vendor info. Public endpoint.
    Wildcard route registered last — does not shadow any static paths.
    """
    product_data = product_service.get_product_details(db, product_id=product_id)
    return {"success": True, "data": product_data}