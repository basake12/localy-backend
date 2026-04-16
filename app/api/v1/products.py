"""
app/api/v1/products.py

FIXES vs previous version:
  1.  [HARD RULE §2] FREE PLAN 20-PRODUCT LIMIT ENFORCED at POST /
      BEFORE any DB write.

      Blueprint §2 / §6.4 full implementation:
        - Check: SELECT COUNT(*) FROM products
                 WHERE business_id = :bid
                   AND is_deleted = FALSE AND is_archived = FALSE
        - If count >= 20 AND tier == 'free' AND no admin override:
            HTTP 403 with exact body:
            {
              "error": "product_limit_reached",
              "message": "Free plan allows up to 20 active product listings.",
              "upgrade_url": "/plans/upgrade",
              "current_count": N,
              "limit": 20
            }
        - Admin override: business.product_limit_override = TRUE
                          business.product_limit_override_value = N
        - Starter / Pro / Enterprise: unlimited (not checked)
        - Archived / deleted products do NOT count toward the limit

  2.  Soft delete now sets is_deleted = True (not just is_active = False).
      Blueprint §2: "Archived/deleted products do NOT count toward the limit."
      is_deleted = True correctly excludes the product from the limit query.

  3.  current_user.user_type → current_user.role.value × 2. Blueprint §14.

  4.  current_user.phone → current_user.phone_number. Blueprint §14.

  5.  product creation now sets business_id from business lookup in addition
      to vendor_id. The free plan limit query uses business_id directly.
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


def _get_vendor_or_404(db: Session, user: User):
    business = _get_business_or_404(db, user)
    vendor = product_vendor_crud.get_by_business_id(db, business_id=business.id)
    if not vendor:
        raise NotFoundException("Store not found. Create a store first.")
    return vendor


def _assert_owns_product(vendor, product):
    if product.vendor_id != vendor.id:
        raise PermissionDeniedException("You don't own this product")


def _enum_val(v) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _build_wishlist_item_dict(wishlist_item) -> dict:
    p = wishlist_item.product
    return {
        "id":          wishlist_item.id,
        "product_id":  wishlist_item.product_id,
        "created_at":  wishlist_item.created_at,
        "product": {
            "id":             p.id,
            "name":           p.name,
            "category":       p.category,
            "brand":          p.brand,
            "base_price":     p.base_price,
            "sale_price":     p.sale_price,
            "images":         p.images or [],
            "average_rating": p.average_rating,
            "in_stock":       p.stock_quantity > 0,
            "vendor_id":      p.vendor_id,
            "vendor_name":    p.vendor.store_name if p.vendor else None,
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
    """
    # Only enforce on Free plan
    tier = business.subscription_tier
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
            return  # Override enabled with no value = unlimited

    # Count active (non-deleted, non-archived) products
    # Blueprint §2 IMPLEMENTATION SPEC — exact query:
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
                "error":        "product_limit_reached",
                "message":      "Free plan allows up to 20 active product listings.",
                "upgrade_url":  "/plans/upgrade",
                "current_count": active_count,
                "limit":         limit,
            },
        )


# ─── STATIC PATHS ─────────────────────────────────────────────────────────────

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
        radius_km=search_params.radius_km or 5.0,
        sort_by=search_params.sort_by or "created_at",
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": results}


# ─── Vendor ───────────────────────────────────────────────────────────────────

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
    current_user: User = Depends(require_business),
) -> dict:
    vendor = _get_vendor_or_404(db, current_user)
    update_data = vendor_in.model_dump(exclude_unset=True)
    vendor = product_vendor_crud.update(db, db_obj=vendor, obj_in=update_data)
    return {"success": True, "data": vendor}


# ─── Products ─────────────────────────────────────────────────────────────────

@router.post("/", response_model=SuccessResponse[ProductResponse], status_code=status.HTTP_201_CREATED)
def create_product(
    *, db: Session = Depends(get_db),
    product_in: ProductCreateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    """
    Create a new product listing.

    [HARD RULE §2] FREE PLAN LIMIT: enforced BEFORE any DB write.
    Free plan businesses are limited to 20 active (non-deleted, non-archived)
    products. Returns HTTP 403 with error code "product_limit_reached" if exceeded.
    Admin override via business.product_limit_override.
    Starter / Pro / Enterprise: unlimited.
    """
    vendor = _get_vendor_or_404(db, current_user)
    business = _get_business_or_404(db, current_user)

    # ── [HARD RULE §2] Enforce free plan limit BEFORE any DB write ────────────
    _enforce_free_plan_product_limit(db, business)

    product_data = product_in.model_dump()
    product_data["vendor_id"]   = vendor.id
    # Blueprint §14: business_id is the primary FK for limit queries
    product_data["business_id"] = business.id

    slug_base = re.sub(r"[^\w\s-]", "", product_data["name"].lower())
    slug_base = re.sub(r"[-\s]+", "-", slug_base)
    product_data["slug"] = f"{slug_base}-{str(uuid4())[:8]}"

    sku = product_data.get("sku")
    if sku:
        archived = db.query(Product).filter(
            Product.sku       == sku,
            Product.vendor_id == vendor.id,
            Product.is_active == False,
        ).first()
        if archived:
            for k, v in product_data.items():
                setattr(archived, k, v)
            archived.is_active  = True
            archived.is_deleted = False   # restore from soft-delete
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
    active_only: bool = Query(True),
) -> dict:
    vendor = _get_vendor_or_404(db, current_user)
    products = product_crud.get_by_vendor(
        db, vendor_id=vendor.id,
        skip=pagination["skip"], limit=pagination["limit"],
        active_only=active_only,
    )
    return {"success": True, "data": products}


@router.get("/my/inventory/low-stock", response_model=SuccessResponse[List[ProductResponse]])
def get_low_stock(
    *, db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
) -> dict:
    vendor = _get_vendor_or_404(db, current_user)
    products = product_crud.get_low_stock(db, vendor_id=vendor.id)
    return {"success": True, "data": products}


@router.get("/analytics/summary", response_model=SuccessResponse[VendorAnalyticsSummary])
def get_analytics_summary(
    *, db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
) -> dict:
    vendor = _get_vendor_or_404(db, current_user)
    analytics = product_crud.get_vendor_analytics(db, vendor_id=vendor.id)
    return {"success": True, "data": analytics}


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

    active_variants = [v for v in product.variants if v.is_active]
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
        raise ValidationException("Insufficient stock")

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
        raise ValidationException("Insufficient stock")

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
    return {"success": True, "data": {"message": "Item removed"}}


# ─── Wishlist ─────────────────────────────────────────────────────────────────

@router.get("/wishlist", response_model=SuccessResponse[WishlistResponse])
def get_wishlist(
    *, db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
) -> dict:
    items      = wishlist_crud.get_by_customer(db, customer_id=current_user.id)
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
            "recipient_name could not be resolved. Add your name to your profile."
        )
    if not recipient_phone:
        raise ValidationException(
            "recipient_phone could not be resolved. Add your phone to your profile."
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
    normalised_status = order_status.lower().strip() if order_status else None
    vendor = _get_vendor_or_404(db, current_user)
    orders = product_order_crud.get_vendor_orders(
        db, vendor_id=vendor.id,
        status=normalised_status,
        skip=pagination["skip"], limit=pagination["limit"],
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
        vendor = _get_vendor_or_404(db, current_user)
        if not any(item.vendor_id == vendor.id for item in order.items):
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

    vendor = _get_vendor_or_404(db, current_user)
    if not any(item.vendor_id == vendor.id for item in order.items):
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


# ─── PARAMETERISED PATHS — must come last ─────────────────────────────────────

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
    current_user: User = Depends(require_business),
) -> dict:
    product = product_crud.get(db, id=product_id)
    if not product:
        raise NotFoundException("Product")
    vendor = _get_vendor_or_404(db, current_user)
    _assert_owns_product(vendor, product)

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
    Sets is_deleted = True so the product is excluded from limit queries.
    """
    product = product_crud.get(db, id=product_id)
    if not product:
        raise NotFoundException("Product")
    vendor = _get_vendor_or_404(db, current_user)
    _assert_owns_product(vendor, product)

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
    vendor = _get_vendor_or_404(db, current_user)
    _assert_owns_product(vendor, product)

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
    vendor = _get_vendor_or_404(db, current_user)
    product = product_crud.get(db, id=variant.product_id)
    _assert_owns_product(vendor, product)

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
    vendor = _get_vendor_or_404(db, current_user)
    product = product_crud.get(db, id=variant.product_id)
    _assert_owns_product(vendor, product)

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