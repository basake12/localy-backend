from fastapi import APIRouter, Depends, Query, status, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from app.core.database import get_db
from app.dependencies import (
    get_current_active_user,
    require_customer,
    require_business,
    get_pagination_params
)
from app.schemas.common import SuccessResponse
from app.schemas.products import (
    VendorCreateRequest,
    VendorResponse,
    ProductCreateRequest,
    ProductUpdateRequest,
    ProductResponse,
    ProductListResponse,
    VariantCreateRequest,
    VariantResponse,
    CartItemAddRequest,
    CartItemUpdateRequest,
    CartItemResponse,
    CartResponse,
    OrderCreateRequest,
    OrderResponse,
    OrderListResponse,
    ProductSearchFilters
)
from app.services.product_service import product_service
from app.crud.products import (
    product_vendor_crud,
    product_crud,
    product_variant_crud,
    cart_crud,
    product_order_crud
)
from app.crud.business import business_crud
from app.models.user import User
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException
)

router = APIRouter()


# ============================================
# PRODUCT SEARCH & DISCOVERY (PUBLIC)
# ============================================

@router.post("/search", response_model=SuccessResponse[List[dict]])
def search_products(
        *,
        db: Session = Depends(get_db),
        search_params: ProductSearchFilters,
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """
    Search products with filters

    - Public endpoint (no auth required)
    - Location-based search
    - Category, brand, price filters
    - Sort by price, popularity, rating
    """
    location = None
    if search_params.location:
        location = (
            search_params.location.latitude,
            search_params.location.longitude
        )

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
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": results
    }


@router.get("/{product_id}", response_model=SuccessResponse[dict])
def get_product_details(
        *,
        db: Session = Depends(get_db),
        product_id: UUID
) -> dict:
    """
    Get product details

    - Public endpoint
    - Returns product with variants and vendor info
    - Increments view count
    """
    product_data = product_service.get_product_details(db, product_id=product_id)

    return {
        "success": True,
        "data": product_data
    }


@router.get("/categories/list", response_model=SuccessResponse[List[str]])
def get_categories(
        *,
        db: Session = Depends(get_db)
) -> dict:
    """Get all product categories"""
    from sqlalchemy import distinct

    categories = db.query(distinct(Product.category)).filter(
        Product.is_active == True
    ).all()

    return {
        "success": True,
        "data": [cat[0] for cat in categories if cat[0]]
    }


@router.get("/brands/list", response_model=SuccessResponse[List[str]])
def get_brands(
        *,
        db: Session = Depends(get_db),
        category: Optional[str] = Query(None)
) -> dict:
    """Get all brands, optionally filtered by category"""
    from sqlalchemy import distinct
    from app.models.products import Product

    query = db.query(distinct(Product.brand)).filter(
        Product.is_active == True,
        Product.brand.isnot(None)
    )

    if category:
        query = query.filter(Product.category == category)

    brands = query.all()

    return {
        "success": True,
        "data": [brand[0] for brand in brands if brand[0]]
    }


# ============================================
# VENDOR MANAGEMENT (BUSINESS ONLY)
# ============================================

@router.post("/vendors", response_model=SuccessResponse[VendorResponse], status_code=status.HTTP_201_CREATED)
def create_vendor(
        *,
        db: Session = Depends(get_db),
        vendor_in: VendorCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Create product vendor/store

    - Only for business accounts
    - Business must be in 'products' category
    """
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    if business.category != "products":
        raise ValidationException("Only products category businesses can create stores")

    # Check if vendor already exists
    existing_vendor = product_vendor_crud.get_by_business_id(db, business_id=business.id)
    if existing_vendor:
        raise ValidationException("Store already exists for this business")

    # Create vendor
    vendor_data = vendor_in.model_dump()
    vendor_data["business_id"] = business.id

    vendor = product_vendor_crud.create_from_dict(db, obj_in=vendor_data)

    return {
        "success": True,
        "data": vendor
    }


@router.get("/vendors/my", response_model=SuccessResponse[VendorResponse])
def get_my_vendor(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business)
) -> dict:
    """Get current business's vendor/store"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    vendor = product_vendor_crud.get_by_business_id(db, business_id=business.id)
    if not vendor:
        raise NotFoundException("Store")

    return {
        "success": True,
        "data": vendor
    }


# ============================================
# PRODUCT MANAGEMENT (VENDOR ONLY)
# ============================================

@router.post("/", response_model=SuccessResponse[ProductResponse], status_code=status.HTTP_201_CREATED)
def create_product(
        *,
        db: Session = Depends(get_db),
        product_in: ProductCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Create a new product

    - Only for vendor/business accounts
    """
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    vendor = product_vendor_crud.get_by_business_id(db, business_id=business.id)
    if not vendor:
        raise NotFoundException("Store not found. Create a store first.")

    # Create product
    product_data = product_in.model_dump()
    product_data["vendor_id"] = vendor.id

    # Generate slug from name
    import re
    slug = re.sub(r'[^\w\s-]', '', product_data['name'].lower())
    slug = re.sub(r'[-\s]+', '-', slug)
    product_data["slug"] = f"{slug}-{str(UUID())[:8]}"

    product = product_crud.create_from_dict(db, obj_in=product_data)

    # Update vendor stats
    vendor.total_products += 1
    db.commit()

    return {
        "success": True,
        "data": product
    }


@router.get("/my/products", response_model=SuccessResponse[List[ProductResponse]])
def get_my_products(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business),
        pagination: dict = Depends(get_pagination_params),
        active_only: bool = Query(True)
) -> dict:
    """Get current vendor's products"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    vendor = product_vendor_crud.get_by_business_id(db, business_id=business.id)
    if not vendor:
        raise NotFoundException("Store")

    products = product_crud.get_by_vendor(
        db,
        vendor_id=vendor.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
        active_only=active_only
    )

    return {
        "success": True,
        "data": products
    }


@router.put("/{product_id}", response_model=SuccessResponse[ProductResponse])
def update_product(
        *,
        db: Session = Depends(get_db),
        product_id: UUID,
        product_in: ProductUpdateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Update product"""
    product = product_crud.get(db, id=product_id)
    if not product:
        raise NotFoundException("Product")

    # Verify ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    vendor = product_vendor_crud.get_by_business_id(db, business_id=business.id)

    if not vendor or product.vendor_id != vendor.id:
        raise PermissionDeniedException("You don't own this product")

    # Update
    update_data = product_in.model_dump(exclude_unset=True)
    product = product_crud.update(db, db_obj=product, obj_in=update_data)

    return {
        "success": True,
        "data": product
    }


@router.delete("/{product_id}", response_model=SuccessResponse[dict])
def delete_product(
        *,
        db: Session = Depends(get_db),
        product_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Delete product (soft delete - sets is_active to False)"""
    product = product_crud.get(db, id=product_id)
    if not product:
        raise NotFoundException("Product")

    # Verify ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    vendor = product_vendor_crud.get_by_business_id(db, business_id=business.id)

    if not vendor or product.vendor_id != vendor.id:
        raise PermissionDeniedException("You don't own this product")

    # Soft delete
    product.is_active = False
    db.commit()

    return {
        "success": True,
        "data": {"message": "Product deleted successfully"}
    }


# ============================================
# PRODUCT VARIANTS (VENDOR ONLY)
# ============================================

@router.post("/{product_id}/variants", response_model=SuccessResponse[VariantResponse],
             status_code=status.HTTP_201_CREATED)
def create_variant(
        *,
        db: Session = Depends(get_db),
        product_id: UUID,
        variant_in: VariantCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Create product variant"""
    product = product_crud.get(db, id=product_id)
    if not product:
        raise NotFoundException("Product")

    # Verify ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    vendor = product_vendor_crud.get_by_business_id(db, business_id=business.id)

    if not vendor or product.vendor_id != vendor.id:
        raise PermissionDeniedException()

    # Create variant
    variant_data = variant_in.model_dump()
    variant_data["product_id"] = product_id

    variant = product_variant_crud.create_from_dict(db, obj_in=variant_data)

    return {
        "success": True,
        "data": variant
    }


@router.get("/{product_id}/variants", response_model=SuccessResponse[List[VariantResponse]])
def get_product_variants(
        *,
        db: Session = Depends(get_db),
        product_id: UUID
) -> dict:
    """Get all variants for a product"""
    variants = product_variant_crud.get_by_product(db, product_id=product_id)

    return {
        "success": True,
        "data": variants
    }


# ============================================
# SHOPPING CART (CUSTOMER ONLY)
# ============================================

@router.get("/cart/my", response_model=SuccessResponse[CartResponse])
def get_my_cart(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_customer)
) -> dict:
    """Get current user's shopping cart"""
    cart_data = product_service.calculate_cart_total(
        db,
        customer_id=current_user.id
    )

    return {
        "success": True,
        "data": {
            "items": [item["cart_item"] for item in cart_data["items"]],
            "subtotal": cart_data["subtotal"],
            "total_items": cart_data["total_items"]
        }
    }


@router.post("/cart", response_model=SuccessResponse[CartItemResponse], status_code=status.HTTP_201_CREATED)
def add_to_cart(
        *,
        db: Session = Depends(get_db),
        cart_item_in: CartItemAddRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """Add item to cart"""
    # Validate product exists
    product = product_crud.get(db, id=cart_item_in.product_id)
    if not product or not product.is_active:
        raise NotFoundException("Product")

    # Check stock
    if not product_crud.check_stock(
            db,
            product_id=cart_item_in.product_id,
            variant_id=cart_item_in.variant_id,
            quantity=cart_item_in.quantity
    ):
        raise ValidationException("Insufficient stock")

    cart_item = cart_crud.add_to_cart(
        db,
        customer_id=current_user.id,
        product_id=cart_item_in.product_id,
        variant_id=cart_item_in.variant_id,
        quantity=cart_item_in.quantity
    )

    return {
        "success": True,
        "data": cart_item
    }


@router.put("/cart/{cart_item_id}", response_model=SuccessResponse[CartItemResponse])
def update_cart_item(
        *,
        db: Session = Depends(get_db),
        cart_item_id: UUID,
        update_data: CartItemUpdateRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """Update cart item quantity"""
    cart_item = cart_crud.get(db, id=cart_item_id)
    if not cart_item:
        raise NotFoundException("Cart item")

    # Verify ownership
    if cart_item.customer_id != current_user.id:
        raise PermissionDeniedException()

    # Check stock
    if not product_crud.check_stock(
            db,
            product_id=cart_item.product_id,
            variant_id=cart_item.variant_id,
            quantity=update_data.quantity
    ):
        raise ValidationException("Insufficient stock")

    cart_item = cart_crud.update_quantity(
        db,
        cart_item_id=cart_item_id,
        quantity=update_data.quantity
    )

    return {
        "success": True,
        "data": cart_item
    }


@router.delete("/cart/{cart_item_id}", response_model=SuccessResponse[dict])
def remove_from_cart(
        *,
        db: Session = Depends(get_db),
        cart_item_id: UUID,
        current_user: User = Depends(require_customer)
) -> dict:
    """Remove item from cart"""
    cart_item = cart_crud.get(db, id=cart_item_id)
    if not cart_item:
        raise NotFoundException("Cart item")

    # Verify ownership
    if cart_item.customer_id != current_user.id:
        raise PermissionDeniedException()

    cart_crud.delete(db, id=cart_item_id)

    return {
        "success": True,
        "data": {"message": "Item removed from cart"}
    }


@router.delete("/cart/clear", response_model=SuccessResponse[dict])
def clear_cart(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_customer)
) -> dict:
    """Clear entire cart"""
    cart_crud.clear_cart(db, customer_id=current_user.id)

    return {
        "success": True,
        "data": {"message": "Cart cleared"}
    }


# ============================================
# CHECKOUT & ORDERS (CUSTOMER)
# ============================================

@router.post("/orders", response_model=SuccessResponse[OrderResponse], status_code=status.HTTP_201_CREATED)
def create_order(
        *,
        db: Session = Depends(get_db),
        order_in: OrderCreateRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Create order and process payment

    - Validates stock availability
    - Creates order with items
    - Processes wallet payment
    - Reduces inventory
    - Clears cart after successful order
    """
    # Convert items to dict format
    items = [item.model_dump() for item in order_in.items]

    order = product_service.checkout_and_pay(
        db,
        current_user=current_user,
        items=items,
        shipping_address=order_in.shipping_address,
        recipient_name=order_in.recipient_name,
        recipient_phone=order_in.recipient_phone,
        payment_method=order_in.payment_method,
        notes=order_in.notes
    )

    # Clear cart after successful order
    cart_crud.clear_cart(db, customer_id=current_user.id)

    return {
        "success": True,
        "data": order
    }


@router.get("/orders/my", response_model=SuccessResponse[List[OrderListResponse]])
def get_my_orders(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_customer),
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get current customer's orders"""
    orders = product_order_crud.get_customer_orders(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    # Transform to list response
    order_list = []
    for order in orders:
        order_list.append({
            "id": order.id,
            "order_status": order.order_status,
            "payment_status": order.payment_status,
            "total_amount": order.total_amount,
            "total_items": len(order.items),
            "created_at": order.created_at
        })

    return {
        "success": True,
        "data": order_list
    }


@router.get("/orders/{order_id}", response_model=SuccessResponse[OrderResponse])
def get_order_details(
        *,
        db: Session = Depends(get_db),
        order_id: UUID,
        current_user: User = Depends(get_current_active_user)
) -> dict:
    """Get order details"""
    order = product_order_crud.get(db, id=order_id)
    if not order:
        raise NotFoundException("Order")

    # Verify permission (customer or vendor)
    if current_user.user_type == "customer":
        if order.customer_id != current_user.id:
            raise PermissionDeniedException()
    elif current_user.user_type == "business":
        # Check if any order item belongs to this vendor
        business = business_crud.get_by_user_id(db, user_id=current_user.id)
        vendor = product_vendor_crud.get_by_business_id(db, business_id=business.id)

        has_access = any(item.vendor_id == vendor.id for item in order.items)
        if not has_access:
            raise PermissionDeniedException()

    return {
        "success": True,
        "data": order
    }


# ============================================
# VENDOR ORDER MANAGEMENT
# ============================================

@router.get("/orders/vendor/my", response_model=SuccessResponse[List[OrderResponse]])
def get_vendor_orders(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business),
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get orders containing vendor's products"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    vendor = product_vendor_crud.get_by_business_id(db, business_id=business.id)
    if not vendor:
        raise NotFoundException("Store")

    orders = product_order_crud.get_vendor_orders(
        db,
        vendor_id=vendor.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": orders
    }


@router.post("/orders/{order_id}/mark-packed", response_model=SuccessResponse[OrderResponse])
def mark_order_packed(
        *,
        db: Session = Depends(get_db),
        order_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Mark order as packed (vendor action)"""
    order = product_order_crud.get(db, id=order_id)
    if not order:
        raise NotFoundException("Order")

    # Verify vendor ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    vendor = product_vendor_crud.get_by_business_id(db, business_id=business.id)

    has_access = any(item.vendor_id == vendor.id for item in order.items)
    if not has_access:
        raise PermissionDeniedException()

    if order.order_status != "processing":
        raise ValidationException("Can only pack orders in processing status")

    order.order_status = "packed"
    db.commit()
    db.refresh(order)

    return {
        "success": True,
        "data": order
    }