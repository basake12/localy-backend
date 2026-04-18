"""
app/api/v1/products.py

FIXES:
  [AUDIT BUG-2] _build_wishlist_item_dict(): p.base_price / p.sale_price /
    p.vendor.store_name replaced with blueprint-aligned field p.price.
    Root cause: blueprint-aligned Product model has single price field.
    p.base_price and p.sale_price no longer exist — AttributeError crash
    on every wishlist endpoint.

  [AUDIT BUG-3] _assert_owns_product() / _get_vendor_or_404():
    Ownership check now uses product.business_id vs business.id.
    Root cause: Product's primary ownership FK is business_id (Blueprint §14).
    vendor_id is an optional supplementary FK for store branding only.
    Checking vendor_id silently blocked ownership for products where vendor_id
    is NULL (any product created without a vendor profile).

  [AUDIT BUG-4] create_product: Depends(require_business) →
    Depends(require_verified_business).
    Root cause: Blueprint §6.4 IMPLEMENTATION SPEC explicitly states:
      "Dependencies: [get_current_business_user, require_verified_business]"
    Unverified businesses (awaiting admin review) could create product listings,
    bypassing the admin verification gate entirely.

EXISTING FIXES (from previous version, retained):
  [HARD RULE §2] Free plan 20-product limit enforced BEFORE any DB write.
  [HARD RULE §2] Soft delete sets is_deleted = True (not just is_active = False).
  Blueprint §14 field names: role (not user_type), phone_number (not phone).
"""
import re
from typing import List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import (
    get_current_active_user,
    get_pagination_params,
    require_business,
    require_customer,
    require_verified_business,   # [BUG-4 FIX] — needed for create_product
)
from app.models.business_model import Business
from app.models.products_model import Product
from app.models.user_model import User
from app.schemas.common_schema import SuccessResponse
from app.schemas.products_schema import (
    PRODUCT_PLATFORM_FEE,
    CartItemAddRequest,
    CartItemResponse,
    CartItemUpdateRequest,
    CartResponse,
    InventoryUpdateRequest,
    OrderCreateRequest,
    OrderListResponse,
    OrderResponse,
    OrderStatusUpdateRequest,
    ProductCreateRequest,
    ProductListResponse,
    ProductResponse,
    ProductSearchFilters,
    ProductUpdateRequest,
    ReturnRequest,
    ReturnResponse,
    VariantCreateRequest,
    VariantResponse,
    VariantUpdateRequest,
    VendorAnalyticsSummary,
    VendorCreateRequest,
    VendorResponse,
    VendorUpdateRequest,
    WishlistItemResponse,
    WishlistResponse,
)
from app.crud.products_crud import (
    cart_crud,
    product_crud,
    product_order_crud,
    product_variant_crud,
    product_vendor_crud,
    wishlist_crud,
)
from app.services.product_service import product_service
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)

router = APIRouter()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_business_or_404(db: Session, user: User) -> Business:
    """Get the Business record for the current user."""
    business = db.query(Business).filter(Business.user_id == user.id).first()
    if not business:
        raise NotFoundException("Business")
    return business


def _get_vendor_optional(db: Session, business: Business):
    """
    Return the ProductVendor for this business, or None.
    [BUG-3 FIX] Vendor is supplementary — not required for ownership checks.
    Business is the authoritative owner per Blueprint §14.
    """
    return product_vendor_crud.get_by_business_id(db, business_id=business.id)


def _get_vendor_or_404(db: Session, user: User):
    """
    Get vendor for endpoints that specifically manage store branding.
    For product ownership, use _get_business_or_404 + _assert_product_owned_by_business.
    """
    business = _get_business_or_404(db, user)
    vendor = product_vendor_crud.get_by_business_id(db, business_id=business.id)
    if not vendor:
        raise NotFoundException("Store profile not found. Create a store first.")
    return vendor


def _assert_product_owned_by_business(business: Business, product: Product) -> None:
    """
    [BUG-3 FIX] Ownership check uses product.business_id, not product.vendor_id.

    Root cause of original bug:
      _assert_owns_product(vendor, product) checked product.vendor_id != vendor.id.
      - product.vendor_id is an optional FK (can be NULL for products without a store profile)
      - NULL vendor_id caused PermissionDeniedException for ALL products created
        before a store profile exists
      - Blueprint §14: business_id is the primary ownership FK on products
        ("products.business_id UUID NOT NULL REFERENCES businesses(id)")

    Correct check: product.business_id must equal the authenticated business's id.
    """
    if product.business_id != business.id:
        raise PermissionDeniedException("You don't own this product.")


def _enum_val(v) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _build_wishlist_item_dict(wishlist_item) -> dict:
    """
    [BUG-2 FIX] Use p.price instead of p.base_price / p.sale_price.

    Root cause:
      Blueprint-aligned Product model (products_model.py) uses single field:
        price NUMERIC(12,2) NOT NULL  — Blueprint §14
      The old fields p.base_price and p.sale_price no longer exist on the ORM
      object. Any call to this function crashed with:
        AttributeError: 'Product' object has no attribute 'base_price'

    Fix: Use p.price for both. Guard p.vendor safely (optional relationship).
    """
    p = wishlist_item.product
    # Guard: vendor is an optional relationship — may not be eagerly loaded
    vendor_name = None
    if hasattr(p, "vendor") and p.vendor is not None:
        vendor_name = getattr(p.vendor, "store_name", None)

    return {
        "id":          wishlist_item.id,
        "product_id":  wishlist_item.product_id,
        "created_at":  wishlist_item.created_at,
        "product": {
            "id":             p.id,
            "name":           p.name,
            "category":       p.category,
            "brand":          getattr(p, "brand", None),
            # [BUG-2 FIX]: blueprint-aligned Product uses single price field
            "price":          p.price,
            "images":         p.images or [],
            "average_rating": getattr(p, "average_rating", 0.0),
            "in_stock":       (p.stock_quantity or 0) > 0,
            "vendor_id":      p.vendor_id,
            "vendor_name":    vendor_name,
        },
    }


# ─── [HARD RULE §2] Free plan product limit enforcement ──────────────────────

def _enforce_free_plan_product_limit(db: Session, business: Business) -> None:
    """
    Enforce the 20-product free plan limit BEFORE any DB write.

    Blueprint §2 / §6.4 IMPLEMENTATION SPEC:
      - Free plan: maximum 20 active (non-deleted, non-archived) products.
      - Archived/deleted products do NOT count toward the limit.
      - Admin override: business.product_limit_override = TRUE
                        business.product_limit_override_value = N
      - Starter / Pro / Enterprise: unlimited — not checked.

    HTTP 403 body (exact blueprint format):
      {
        "error": "product_limit_reached",
        "message": "Free plan allows up to 20 active product listings.",
        "upgrade_url": "/plans/upgrade",
        "current_count": N,
        "limit": 20
      }

    Flutter must handle error code "product_limit_reached" and render
    UpgradePlanBottomSheet — NOT a generic error snackbar.
    Blueprint §6.4 / §P07.
    """
    # Only enforce on Free plan — all other plans are unlimited
    tier     = business.subscription_tier
    tier_val = tier.value if hasattr(tier, "value") else str(tier or "free")
    if tier_val != "free":
        return

    # Admin override — if set, use override limit instead of 20
    limit = 20
    if business.product_limit_override:
        override_val = business.product_limit_override_value
        if override_val is not None:
            limit = int(override_val)
        else:
            return  # Override enabled with no value = unlimited for this business

    # Count active (non-deleted, non-archived) products
    # Blueprint §6.4 IMPLEMENTATION SPEC — exact query per blueprint:
    #   SELECT COUNT(*) FROM products
    #   WHERE business_id = :bid AND is_deleted = FALSE AND is_archived = FALSE
    active_count: int = db.execute(
        select(func.count(Product.id)).where(
            Product.business_id == business.id,
            Product.is_deleted  == False,
            Product.is_archived == False,
        )
    ).scalar() or 0

    if active_count >= limit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error":         "product_limit_reached",
                "message":       "Free plan allows up to 20 active product listings.",
                "upgrade_url":   "/plans/upgrade",
                "current_count": active_count,
                "limit":         limit,
            },
        )


# ─── STATIC PATHS (must be declared before parameterised paths) ───────────────

@router.get("/categories/list", response_model=SuccessResponse[List[str]])
def get_categories(*, db: Session = Depends(get_db)) -> dict:
    from sqlalchemy import distinct
    categories = db.query(distinct(Product.category)).filter(
        Product.is_active == True
    ).all()
    return {"success": True, "data": [c[0] for c in categories if c[0]]}


@router.get("/brands/list", response_model=SuccessResponse[List[str]])
def get_brands(
    *, db: Session = Depends(get_db),
    category: Optional[str] = Query(None),
) -> dict:
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
    pagination: dict = Depends(get_pagination_params),
) -> dict:
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
        # Blueprint §4.1: default 5 km radius
        radius_km=search_params.radius_km or 5.0,
        sort_by=search_params.sort_by or "created_at",
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": results}


# ─── Vendor (Store Profile) ───────────────────────────────────────────────────

@router.get("/vendors/my", response_model=SuccessResponse[VendorResponse])
def get_my_vendor(
    *, db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
) -> dict:
    vendor = _get_vendor_or_404(db, current_user)
    return {"success": True, "data": vendor}


@router.post("/vendors", response_model=SuccessResponse[VendorResponse], status_code=status.HTTP_201_CREATED)
def create_vendor(
    *, db: Session = Depends(get_db),
    vendor_in: VendorCreateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    business = _get_business_or_404(db, current_user)
    if business.category != "products":
        raise ValidationException("Only businesses in the 'products' category can create store profiles.")

    existing = product_vendor_crud.get_by_business_id(db, business_id=business.id)
    if existing:
        raise ValidationException("Store profile already exists for this business.")

    vendor_data = vendor_in.model_dump()
    vendor_data["business_id"] = business.id
    vendor = product_vendor_crud.create_from_dict(db, obj_in=vendor_data)
    return {"success": True, "data": vendor}


@router.patch("/vendors/me", response_model=SuccessResponse[VendorResponse])
def update_my_vendor(
    *, db: Session = Depends(get_db),
    vendor_in: VendorUpdateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    vendor = _get_vendor_or_404(db, current_user)
    update_data = vendor_in.model_dump(exclude_unset=True)
    vendor = product_vendor_crud.update(db, db_obj=vendor, obj_in=update_data)
    return {"success": True, "data": vendor}


@router.get("/analytics/summary", response_model=SuccessResponse[VendorAnalyticsSummary])
def get_analytics_summary(
    *, db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
) -> dict:
    vendor = _get_vendor_or_404(db, current_user)
    analytics = product_crud.get_vendor_analytics(db, vendor_id=vendor.id)
    return {"success": True, "data": analytics}


# ─── Products ─────────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=SuccessResponse[ProductResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_product(
    *,
    db:           Session = Depends(get_db),
    product_in:   ProductCreateRequest,
    # [BUG-4 FIX] require_verified_business — Blueprint §6.4 IMPLEMENTATION SPEC:
    # "Dependencies: [get_current_business_user, require_verified_business]"
    # Unverified businesses must not be able to create product listings.
    current_user: User    = Depends(require_verified_business),
) -> dict:
    """
    Create a new product listing.

    [HARD RULE §2] FREE PLAN LIMIT: enforced BEFORE any DB write.
    Free plan businesses are limited to 20 active (non-deleted, non-archived)
    products. Returns HTTP 403 with error code "product_limit_reached" if exceeded.
    Admin override via business.product_limit_override.
    Starter / Pro / Enterprise: unlimited.

    Blueprint §6.4 IMPLEMENTATION SPEC:
      Dependencies: [get_current_business_user, require_verified_business]
    """
    business = _get_business_or_404(db, current_user)

    # ── [HARD RULE §2] Enforce free plan limit BEFORE any DB write ────────────
    _enforce_free_plan_product_limit(db, business)

    product_data = product_in.model_dump()
    # Blueprint §14: business_id is the primary FK for all blueprint operations
    product_data["business_id"] = business.id

    # Optionally link to vendor store profile if one exists
    vendor = _get_vendor_optional(db, business)
    if vendor:
        product_data["vendor_id"] = vendor.id

    slug_base = re.sub(r"[^\w\s-]", "", product_data["name"].lower())
    slug_base = re.sub(r"[-\s]+", "-", slug_base)
    product_data["slug"] = f"{slug_base}-{str(uuid4())[:8]}"

    # SKU restore path: if archived product with same SKU exists, restore it
    # Note: _enforce_free_plan_product_limit was already called above.
    # Restoring an archived product that was previously active is safe because:
    #   - archived products don't count toward the 20-product limit
    #   - restoring returns count back to what it was when the product was archived
    sku = product_data.get("sku")
    if sku:
        archived = db.query(Product).filter(
            Product.sku         == sku,
            Product.business_id == business.id,
            Product.is_active   == False,
            Product.is_archived == True,
        ).first()
        if archived:
            for k, v in product_data.items():
                setattr(archived, k, v)
            archived.is_active  = True
            archived.is_deleted  = False
            archived.is_archived = False
            db.commit()
            db.refresh(archived)
            return {"success": True, "data": archived}

    try:
        product = product_crud.create_from_dict(db, obj_in=product_data)
        if vendor:
            vendor.total_products += 1
        db.commit()
    except IntegrityError as e:
        db.rollback()
        if "ix_products_sku" in str(e) or "unique" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A product with this SKU already exists.",
            )
        raise

    return {"success": True, "data": product}


@router.get("/my/products", response_model=SuccessResponse[List[ProductResponse]])
def get_my_products(
    *, db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
    pagination: dict = Depends(get_pagination_params),
    active_only: bool = Query(True),
) -> dict:
    """
    [BUG-3 FIX] Now queries by business_id, not vendor_id.
    Businesses without a vendor store profile can still manage products.
    """
    business = _get_business_or_404(db, current_user)
    products = product_crud.get_by_business(
        db,
        business_id=business.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
        active_only=active_only,
    )
    return {"success": True, "data": products}


@router.get("/my/inventory/low-stock", response_model=SuccessResponse[List[ProductResponse]])
def get_low_stock(
    *, db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
) -> dict:
    business = _get_business_or_404(db, current_user)
    products = product_crud.get_low_stock(db, business_id=business.id)
    return {"success": True, "data": products}


# ─── Cart ─────────────────────────────────────────────────────────────────────

@router.get("/cart/my", response_model=SuccessResponse[CartResponse])
def get_my_cart(
    *, db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
) -> dict:
    cart_data = product_service.calculate_cart_total(db, customer_id=current_user.id)
    return {
        "success": True,
        "data": {
            "items":       cart_data["items"],
            "subtotal":    cart_data["subtotal"],
            "total_items": cart_data["total_items"],
        },
    }


@router.post("/cart", response_model=SuccessResponse[CartItemResponse], status_code=status.HTTP_201_CREATED)
def add_to_cart(
    *, db: Session = Depends(get_db),
    cart_item_in: CartItemAddRequest,
    current_user: User = Depends(require_customer),
) -> dict:
    product = product_crud.get_with_relations(db, product_id=cart_item_in.product_id)
    if not product or not product.is_active:
        raise NotFoundException("Product")

    active_variants = [v for v in (product.variant_rows or []) if v.is_active]
    if active_variants and not cart_item_in.variant_id:
        raise ValidationException(
            f"This product has variants. Please select one. "
            f"Available options: {[v.attributes for v in active_variants]}"
        )

    if not product_crud.check_stock(
        db, product_id=cart_item_in.product_id,
        variant_id=cart_item_in.variant_id,
        quantity=cart_item_in.quantity,
    ):
        raise ValidationException("Insufficient stock for the requested quantity.")

    raw_item = cart_crud.add_to_cart(
        db,
        customer_id=current_user.id,
        product_id=cart_item_in.product_id,
        variant_id=cart_item_in.variant_id,
        quantity=cart_item_in.quantity,
    )

    item = cart_crud.get_cart_item_with_relations(db, cart_item_id=raw_item.id)
    if not item:
        raise NotFoundException("Cart item")

    return {"success": True, "data": product_service._build_cart_item_dict(item)}


@router.delete("/cart/clear", response_model=SuccessResponse[dict])
def clear_cart(
    *, db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
) -> dict:
    cart_crud.clear_cart(db, customer_id=current_user.id)
    return {"success": True, "data": {"message": "Cart cleared"}}


@router.put("/cart/{cart_item_id}", response_model=SuccessResponse[CartItemResponse])
def update_cart_item(
    *, db: Session = Depends(get_db),
    cart_item_id: UUID,
    update_data: CartItemUpdateRequest,
    current_user: User = Depends(require_customer),
) -> dict:
    cart_item = cart_crud.get(db, id=cart_item_id)
    if not cart_item:
        raise NotFoundException("Cart item")
    if cart_item.customer_id != current_user.id:
        raise PermissionDeniedException()

    if not product_crud.check_stock(
        db, product_id=cart_item.product_id,
        variant_id=cart_item.variant_id,
        quantity=update_data.quantity,
    ):
        raise ValidationException("Insufficient stock for the requested quantity.")

    cart_crud.update_quantity(db, cart_item_id=cart_item_id, quantity=update_data.quantity)
    item = cart_crud.get_cart_item_with_relations(db, cart_item_id=cart_item_id)
    if not item:
        raise NotFoundException("Cart item")

    return {"success": True, "data": product_service._build_cart_item_dict(item)}


@router.delete("/cart/{cart_item_id}", response_model=SuccessResponse[dict])
def remove_from_cart(
    *, db: Session = Depends(get_db),
    cart_item_id: UUID,
    current_user: User = Depends(require_customer),
) -> dict:
    cart_item = cart_crud.get(db, id=cart_item_id)
    if not cart_item:
        raise NotFoundException("Cart item")
    if cart_item.customer_id != current_user.id:
        raise PermissionDeniedException()
    cart_crud.delete(db, id=cart_item_id)
    return {"success": True, "data": {"message": "Item removed from cart"}}


# ─── Wishlist ─────────────────────────────────────────────────────────────────

@router.get("/wishlist", response_model=SuccessResponse[WishlistResponse])
def get_wishlist(
    *, db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
) -> dict:
    items      = wishlist_crud.get_by_customer(db, customer_id=current_user.id)
    # [BUG-2 FIX] _build_wishlist_item_dict now uses p.price, not p.base_price
    serialized = [_build_wishlist_item_dict(w) for w in items]
    return {"success": True, "data": {"items": serialized, "total": len(serialized)}}


@router.post("/wishlist/{product_id}", response_model=SuccessResponse[dict], status_code=status.HTTP_201_CREATED)
def add_to_wishlist(
    *, db: Session = Depends(get_db),
    product_id: UUID,
    current_user: User = Depends(require_customer),
) -> dict:
    product = product_crud.get(db, id=product_id)
    if not product or not product.is_active:
        raise NotFoundException("Product")
    wishlist_crud.add(db, customer_id=current_user.id, product_id=product_id)
    return {"success": True, "data": {"message": "Added to wishlist"}}


@router.delete("/wishlist/{product_id}", response_model=SuccessResponse[dict])
def remove_from_wishlist(
    *, db: Session = Depends(get_db),
    product_id: UUID,
    current_user: User = Depends(require_customer),
) -> dict:
    wishlist_crud.remove(db, customer_id=current_user.id, product_id=product_id)
    return {"success": True, "data": {"message": "Removed from wishlist"}}


# ─── Orders ───────────────────────────────────────────────────────────────────

@router.post("/orders", response_model=SuccessResponse[OrderResponse], status_code=status.HTTP_201_CREATED)
def create_order(
    *, db: Session = Depends(get_db),
    order_in: OrderCreateRequest,
    current_user: User = Depends(require_customer),
) -> dict:
    from app.models.user_model import CustomerProfile

    recipient_name  = order_in.recipient_name
    recipient_phone = order_in.recipient_phone

    if not recipient_name or not recipient_phone:
        profile = db.query(CustomerProfile).filter(
            CustomerProfile.user_id == current_user.id
        ).first()
        if profile:
            if not recipient_name:
                recipient_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip()
            if not recipient_phone:
                # Blueprint §14: phone_number (not phone)
                recipient_phone = current_user.phone_number or ""

    if not recipient_name:
        raise ValidationException(
            "recipient_name is required. Add your name to your profile."
        )
    if not recipient_phone:
        raise ValidationException(
            "recipient_phone is required. Add your phone number to your profile."
        )

    items = [item.model_dump() for item in order_in.items]
    order = product_service.checkout_and_pay(
        db,
        current_user=current_user,
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
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    orders = product_order_crud.get_customer_orders(
        db, customer_id=current_user.id,
        skip=pagination["skip"], limit=pagination["limit"],
    )
    order_list = [
        {
            "id":             o.id,
            "order_status":   _enum_val(o.order_status),
            "payment_status": _enum_val(o.payment_status),
            "total_amount":   o.total_amount,
            "total_items":    len(o.items),
            "created_at":     o.created_at,
        }
        for o in orders
    ]
    return {"success": True, "data": order_list}


@router.get("/orders/vendor/my", response_model=SuccessResponse[List[OrderResponse]])
def get_vendor_orders(
    *, db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
    pagination: dict = Depends(get_pagination_params),
    order_status: Optional[str] = Query(None),
) -> dict:
    """
    [BUG-3 FIX] Uses business_id for order lookup.
    Businesses without a vendor profile can still view their orders.
    """
    business = _get_business_or_404(db, current_user)
    normalised_status = order_status.lower().strip() if order_status else None
    orders = product_order_crud.get_business_orders(
        db,
        business_id=business.id,
        status=normalised_status,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": orders}


@router.get("/orders/tracking/{tracking_number}", response_model=SuccessResponse[OrderResponse])
def track_order(
    *, db: Session = Depends(get_db),
    tracking_number: str,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    order = product_order_crud.get_by_tracking_number(db, tracking_number=tracking_number)
    if not order:
        raise NotFoundException("Order")
    if order.customer_id != current_user.id:
        raise PermissionDeniedException()
    return {"success": True, "data": order}


@router.get("/orders/{order_id}", response_model=SuccessResponse[OrderResponse])
def get_order_details(
    *, db: Session = Depends(get_db),
    order_id: UUID,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    order = product_order_crud.get(db, id=order_id)
    if not order:
        raise NotFoundException("Order")

    # Blueprint §14: role (not user_type)
    role_val = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)

    if role_val == "customer":
        if order.customer_id != current_user.id:
            raise PermissionDeniedException()
    elif role_val == "business":
        # [BUG-3 FIX] Check via business_id, not vendor_id
        business = _get_business_or_404(db, current_user)
        if not any(item.product and item.product.business_id == business.id for item in order.items):
            raise PermissionDeniedException()

    return {"success": True, "data": order}


@router.patch("/orders/{order_id}/status", response_model=SuccessResponse[OrderResponse])
def update_order_status(
    *, db: Session = Depends(get_db),
    order_id: UUID,
    status_in: OrderStatusUpdateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    order = product_order_crud.get(db, id=order_id)
    if not order:
        raise NotFoundException("Order")

    # [BUG-3 FIX] Verify business owns at least one item in this order
    business = _get_business_or_404(db, current_user)
    if not any(item.product and item.product.business_id == business.id for item in order.items):
        raise PermissionDeniedException()

    order = product_order_crud.update_order_status(
        db, order_id=order_id,
        new_status=status_in.status,
        tracking_number=status_in.tracking_number,
        estimated_delivery=status_in.estimated_delivery,
    )
    return {"success": True, "data": order}


@router.post("/orders/{order_id}/cancel", response_model=SuccessResponse[OrderResponse])
def cancel_order(
    *, db: Session = Depends(get_db),
    order_id: UUID,
    current_user: User = Depends(require_customer),
) -> dict:
    order = product_order_crud.cancel_order(
        db, order_id=order_id, customer_id=current_user.id
    )
    return {"success": True, "data": order}


@router.post(
    "/orders/{order_id}/return",
    response_model=SuccessResponse[ReturnResponse],
    status_code=status.HTTP_201_CREATED,
)
def request_return(
    *, db: Session = Depends(get_db),
    order_id: UUID,
    return_in: ReturnRequest,
    current_user: User = Depends(require_customer),
) -> dict:
    return_record = product_order_crud.create_return_request(
        db,
        order_id=order_id,
        customer_id=current_user.id,
        reason=return_in.reason,
        item_ids=return_in.items,
        photos=return_in.photos,
    )
    return {"success": True, "data": return_record}


# ─── PARAMETERISED PATHS — must come LAST (after all static path segments) ────

@router.patch("/{product_id}", response_model=SuccessResponse[ProductResponse])
def update_product(
    *, db: Session = Depends(get_db),
    product_id: UUID,
    product_in: ProductUpdateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    product = product_crud.get(db, id=product_id)
    if not product:
        raise NotFoundException("Product")

    # [BUG-3 FIX] Ownership check via business_id
    business = _get_business_or_404(db, current_user)
    _assert_product_owned_by_business(business, product)

    update_data = product_in.model_dump(exclude_unset=True)
    product = product_crud.update(db, db_obj=product, obj_in=update_data)
    return {"success": True, "data": product}


@router.patch("/{product_id}/inventory", response_model=SuccessResponse[ProductResponse])
def update_inventory(
    *, db: Session = Depends(get_db),
    product_id: UUID,
    inventory_in: InventoryUpdateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    product = product_crud.get(db, id=product_id)
    if not product:
        raise NotFoundException("Product")

    # [BUG-3 FIX] Ownership check via business_id
    business = _get_business_or_404(db, current_user)
    _assert_product_owned_by_business(business, product)

    product = product_crud.update(
        db, db_obj=product, obj_in=inventory_in.model_dump(exclude_unset=True)
    )
    return {"success": True, "data": product}


@router.delete("/{product_id}", response_model=SuccessResponse[dict])
def delete_product(
    *, db: Session = Depends(get_db),
    product_id: UUID,
    current_user: User = Depends(require_business),
) -> dict:
    """
    Soft-delete product.
    Blueprint §2: "Archived/deleted products do NOT count toward the limit."
    Sets is_deleted = True so the product is EXCLUDED from limit queries:
      WHERE business_id = :bid AND is_deleted = FALSE AND is_archived = FALSE
    """
    product = product_crud.get(db, id=product_id)
    if not product:
        raise NotFoundException("Product")

    # [BUG-3 FIX] Ownership check via business_id
    business = _get_business_or_404(db, current_user)
    _assert_product_owned_by_business(business, product)

    # Blueprint §2: is_deleted = True excludes from limit count
    product.is_active  = False
    product.is_deleted = True
    db.commit()
    return {"success": True, "data": {"message": "Product deleted"}}


@router.post("/{product_id}/variants", response_model=SuccessResponse[VariantResponse], status_code=status.HTTP_201_CREATED)
def create_variant(
    *, db: Session = Depends(get_db),
    product_id: UUID,
    variant_in: VariantCreateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    product = product_crud.get(db, id=product_id)
    if not product:
        raise NotFoundException("Product")

    # [BUG-3 FIX] Ownership check via business_id
    business = _get_business_or_404(db, current_user)
    _assert_product_owned_by_business(business, product)

    duplicate = product_variant_crud.get_by_attributes(
        db, product_id=product_id, attributes=variant_in.attributes
    )
    if duplicate:
        raise ValidationException(
            f"A variant with attributes {variant_in.attributes} already exists."
        )

    variant_data = variant_in.model_dump()
    variant_data["product_id"] = product_id
    variant = product_variant_crud.create_from_dict(db, obj_in=variant_data)
    return {"success": True, "data": variant}


@router.get("/{product_id}/variants", response_model=SuccessResponse[List[VariantResponse]])
def get_product_variants(
    *, db: Session = Depends(get_db), product_id: UUID
) -> dict:
    variants = product_variant_crud.get_by_product(db, product_id=product_id)
    return {"success": True, "data": variants}


@router.patch("/variants/{variant_id}", response_model=SuccessResponse[VariantResponse])
def update_variant(
    *, db: Session = Depends(get_db),
    variant_id: UUID,
    variant_in: VariantUpdateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    variant = product_variant_crud.get(db, id=variant_id)
    if not variant:
        raise NotFoundException("Variant")

    # [BUG-3 FIX] Ownership check via business_id
    business = _get_business_or_404(db, current_user)
    product  = product_crud.get(db, id=variant.product_id)
    if not product:
        raise NotFoundException("Product")
    _assert_product_owned_by_business(business, product)

    if variant_in.attributes is not None:
        duplicate = product_variant_crud.get_by_attributes(
            db, product_id=variant.product_id,
            attributes=variant_in.attributes, exclude_id=variant_id,
        )
        if duplicate:
            raise ValidationException(
                f"Another variant with attributes {variant_in.attributes} already exists."
            )

    update_data = variant_in.model_dump(exclude_unset=True)
    variant = product_variant_crud.update(db, db_obj=variant, obj_in=update_data)
    return {"success": True, "data": variant}


@router.delete("/variants/{variant_id}", response_model=SuccessResponse[dict])
def delete_variant(
    *, db: Session = Depends(get_db),
    variant_id: UUID,
    current_user: User = Depends(require_business),
) -> dict:
    variant = product_variant_crud.get(db, id=variant_id)
    if not variant:
        raise NotFoundException("Variant")

    # [BUG-3 FIX] Ownership check via business_id
    business = _get_business_or_404(db, current_user)
    product  = product_crud.get(db, id=variant.product_id)
    if not product:
        raise NotFoundException("Product")
    _assert_product_owned_by_business(business, product)

    variant.is_active = False
    db.commit()
    return {"success": True, "data": {"message": "Variant removed"}}


# GET /{product_id} is a wildcard — MUST be last
@router.get("/{product_id}", response_model=SuccessResponse[dict])
def get_product_details(
    *, db: Session = Depends(get_db), product_id: UUID
) -> dict:
    product_data = product_service.get_product_details(db, product_id=product_id)
    return {"success": True, "data": product_data}