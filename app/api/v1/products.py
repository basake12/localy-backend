from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID, uuid4

# FIX: Import uuid4 explicitly (was UUID() which is not callable)
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
    WishlistItemResponse,
    WishlistResponse,
    ProductSearchFilters,
    VendorAnalyticsSummary,
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
from app.crud.business_crud import business_crud
from app.models.products_model import Product
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
    """Shared helper — get vendor for current business user."""
    business = business_crud.get_by_user_id(db, user_id=user.id)
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
    # FIX: Added missing Product import (was NameError in original)
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
    """
    Search products with filters. Public endpoint.
    FIX: response_model changed from List[dict] to List[ProductListResponse].
    """
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
        radius_km=search_params.radius_km or 10.0,
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
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
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


# FIX: Added PATCH /vendors/me — store settings had no update endpoint
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

    # FIX: Use uuid4() — UUID() is the class constructor, not callable as a factory
    import re
    slug_base = re.sub(r'[^\w\s-]', '', product_data['name'].lower())
    slug_base = re.sub(r'[-\s]+', '-', slug_base)
    product_data["slug"] = f"{slug_base}-{str(uuid4())[:8]}"

    product = product_crud.create_from_dict(db, obj_in=product_data)
    vendor.total_products += 1
    db.commit()
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
    """Get current customer's cart with totals."""
    cart_data = product_service.calculate_cart_total(db, customer_id=current_user.id)
    return {
        "success": True,
        "data": {
            "items": [item["cart_item"] for item in cart_data["items"]],
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
    """Add item to cart. Returns updated cart item."""
    product = product_crud.get(db, id=cart_item_in.product_id)
    if not product or not product.is_active:
        raise NotFoundException("Product")

    if not product_crud.check_stock(
            db, product_id=cart_item_in.product_id,
            variant_id=cart_item_in.variant_id,
            quantity=cart_item_in.quantity
    ):
        raise ValidationException("Insufficient stock")

    cart_item = cart_crud.add_to_cart(
        db, customer_id=current_user.id,
        product_id=cart_item_in.product_id,
        variant_id=cart_item_in.variant_id,
        quantity=cart_item_in.quantity
    )
    return {"success": True, "data": cart_item}


# FIX: DELETE /cart/clear MUST be registered BEFORE DELETE /cart/{cart_item_id}
# Otherwise 'clear' is matched as a UUID cart_item_id → 422 error every time
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
    """Update cart item quantity."""
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

    cart_item = cart_crud.update_quantity(
        db, cart_item_id=cart_item_id, quantity=update_data.quantity
    )
    return {"success": True, "data": cart_item}


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
    """Get customer's wishlist."""
    items = wishlist_crud.get_by_customer(db, customer_id=current_user.id)
    return {"success": True, "data": {"items": items, "total": len(items)}}


# ── Orders (static paths) ────────────────────────────────────

@router.post("/orders", response_model=SuccessResponse[OrderResponse], status_code=status.HTTP_201_CREATED)
def create_order(
        *, db: Session = Depends(get_db),
        order_in: OrderCreateRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Checkout and create order. Processes wallet payment.
    FIX: Safe payment flow — stock only reduced after wallet check passes.
    """
    items = [item.model_dump() for item in order_in.items]
    order = product_service.checkout_and_pay(
        db, current_user=current_user,
        items=items,
        shipping_address=order_in.shipping_address,
        recipient_name=order_in.recipient_name,
        recipient_phone=order_in.recipient_phone,
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
    order_list = [
        {
            "id": o.id, "order_status": o.order_status,
            "payment_status": o.payment_status, "total_amount": o.total_amount,
            "total_items": len(o.items), "created_at": o.created_at,
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
    vendor = _get_vendor_or_404(db, current_user)
    orders = product_order_crud.get_vendor_orders(
        db, vendor_id=vendor.id,
        status=order_status,
        skip=pagination["skip"], limit=pagination["limit"]
    )
    return {"success": True, "data": orders}


# ────────────────────────────────────────────────────────────
# BLOCK 2 — PARAMETERISED PATHS (wildcards — MUST come last)
# ────────────────────────────────────────────────────────────

# FIX: Changed PUT to PATCH — ProductUpdateRequest is partial, PUT requires all fields
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


# FIX: Added PATCH and DELETE for variants (were completely missing)
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


# ── Orders (parameterised) ────────────────────────────────────

@router.get("/orders/{order_id}", response_model=SuccessResponse[OrderResponse])
def get_order_details(
        *, db: Session = Depends(get_db),
        order_id: UUID,
        current_user: User = Depends(get_current_active_user)
) -> dict:
    """Get full order details. Accessible by customer who placed it or vendor with items in it."""
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


# FIX: Generic status update (replaces mark-packed only — now covers full lifecycle)
@router.patch("/orders/{order_id}/status", response_model=SuccessResponse[OrderResponse])
def update_order_status(
        *, db: Session = Depends(get_db),
        order_id: UUID,
        status_in: OrderStatusUpdateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Update order status. Vendor can move orders through:
    processing → packed → shipped → delivered
    """
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


# FIX: Added cancel endpoint — customers couldn't cancel orders before
@router.post("/orders/{order_id}/cancel", response_model=SuccessResponse[OrderResponse])
def cancel_order(
        *, db: Session = Depends(get_db),
        order_id: UUID,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Cancel order and restore stock. Only valid for pending/processing orders.
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


# FIX: GET /{product_id} is a wildcard — MUST be registered LAST
@router.get("/{product_id}", response_model=SuccessResponse[dict])
def get_product_details(
        *, db: Session = Depends(get_db), product_id: UUID
) -> dict:
    """
    Get full product detail with variants and vendor info. Public endpoint.
    FIX: This wildcard route is now registered last so it doesn't shadow any
    static paths like /categories/list, /cart/my, /orders/my etc.
    """
    product_data = product_service.get_product_details(db, product_id=product_id)
    return {"success": True, "data": product_data}