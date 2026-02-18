from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
from datetime import date, datetime

from app.core.database import get_db
from app.dependencies import (
    get_current_active_user,
    require_customer,
    require_business,
    get_pagination_params
)
from app.schemas.common import SuccessResponse
from app.schemas.food import (
    RestaurantCreateRequest,
    RestaurantResponse,
    MenuCategoryCreateRequest,
    MenuCategoryResponse,
    MenuItemCreateRequest,
    MenuItemResponse,
    MenuResponse,
    ReservationCreateRequest,
    ReservationResponse,
    FoodOrderCreateRequest,
    FoodOrderResponse,
    FoodOrderListResponse,
    RestaurantSearchFilters
)
from app.services.food_service import food_service
from app.crud.food import (
    restaurant_crud,
    menu_category_crud,
    menu_item_crud,
    table_reservation_crud,
    food_order_crud
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
# MENU MANAGEMENT - TOP-LEVEL ALIASES
# ============================================

# NOTE: POST /food/menu was returning 404 because the actual routes are
# /food/menu/categories and /food/menu/items. This endpoint explains that
# clearly instead of a silent 404.
@router.post("/menu", include_in_schema=True)
def menu_root_redirect() -> dict:
    """
    POST /food/menu is not a valid endpoint.

    Use the specific sub-routes:
    - POST /food/menu/categories  — create a menu category (business only)
    - POST /food/menu/items       — create a menu item (business only)
    """
    from fastapi import HTTPException
    raise HTTPException(
        status_code=404,
        detail={
            "message": "Use /food/menu/categories or /food/menu/items",
            "valid_endpoints": [
                "POST /api/v1/food/menu/categories",
                "POST /api/v1/food/menu/items"
            ]
        }
    )


# ============================================
# RESTAURANT SEARCH & DISCOVERY (PUBLIC)
# ============================================

@router.post("/restaurants/search", response_model=SuccessResponse[List[dict]])
def search_restaurants(
        *,
        db: Session = Depends(get_db),
        search_params: RestaurantSearchFilters,
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """
    Search restaurants

    - Public endpoint
    - Location-based search
    - Filter by cuisine, price range, ratings
    """
    location = None
    if search_params.location:
        location = (
            search_params.location.latitude,
            search_params.location.longitude
        )

    results = food_service.search_restaurants(
        db,
        query_text=search_params.query,
        cuisine_type=search_params.cuisine_type,
        location=location,
        radius_km=search_params.radius_km or 10.0,
        price_range=search_params.price_range,
        offers_delivery=search_params.offers_delivery,
        min_rating=search_params.min_rating,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": results
    }


@router.get("/restaurants/{restaurant_id}", response_model=SuccessResponse[dict])
def get_restaurant_details(
        *,
        db: Session = Depends(get_db),
        restaurant_id: UUID
) -> dict:
    """
    Get restaurant details with menu

    - Public endpoint
    - Returns full menu
    """
    restaurant_data = food_service.get_restaurant_details(
        db,
        restaurant_id=restaurant_id
    )

    return {
        "success": True,
        "data": restaurant_data
    }


@router.get("/restaurants/{restaurant_id}/menu", response_model=SuccessResponse[MenuResponse])
def get_restaurant_menu(
        *,
        db: Session = Depends(get_db),
        restaurant_id: UUID
) -> dict:
    """Get restaurant menu"""
    menu = menu_item_crud.get_restaurant_menu(db, restaurant_id=restaurant_id)

    return {
        "success": True,
        "data": {"categories": menu}
    }


# ============================================
# RESTAURANT MANAGEMENT (BUSINESS ONLY)
# ============================================

@router.post("/restaurants", response_model=SuccessResponse[RestaurantResponse], status_code=status.HTTP_201_CREATED)
def create_restaurant(
        *,
        db: Session = Depends(get_db),
        restaurant_in: RestaurantCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Create restaurant

    - Only for business accounts
    - Business must be in 'food' category
    """
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    if business.category != "food":
        raise ValidationException("Only food category businesses can create restaurants")

    # Check if restaurant already exists
    existing_restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if existing_restaurant:
        raise ValidationException("Restaurant already exists for this business")

    # Create restaurant
    restaurant_data = restaurant_in.model_dump()
    restaurant_data["business_id"] = business.id

    restaurant = restaurant_crud.create_from_dict(db, obj_in=restaurant_data)

    return {
        "success": True,
        "data": restaurant
    }


@router.get("/restaurants/my/restaurant", response_model=SuccessResponse[RestaurantResponse])
def get_my_restaurant(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business)
) -> dict:
    """Get current business's restaurant"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant:
        raise NotFoundException("Restaurant")

    return {
        "success": True,
        "data": restaurant
    }


# ============================================
# MENU MANAGEMENT (BUSINESS ONLY)
# ============================================

@router.post("/menu/categories", response_model=SuccessResponse[MenuCategoryResponse],
             status_code=status.HTTP_201_CREATED)
def create_menu_category(
        *,
        db: Session = Depends(get_db),
        category_in: MenuCategoryCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Create menu category"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)

    if not restaurant:
        raise NotFoundException("Restaurant")

    category_data = category_in.model_dump()
    category_data["restaurant_id"] = restaurant.id

    category = menu_category_crud.create_from_dict(db, obj_in=category_data)

    return {
        "success": True,
        "data": category
    }


@router.post("/menu/items", response_model=SuccessResponse[MenuItemResponse], status_code=status.HTTP_201_CREATED)
def create_menu_item(
        *,
        db: Session = Depends(get_db),
        item_in: MenuItemCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Create menu item"""
    # Verify category ownership
    category = menu_category_crud.get(db, id=item_in.category_id)
    if not category:
        raise NotFoundException("Menu category")

    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)

    if not restaurant or category.restaurant_id != restaurant.id:
        raise PermissionDeniedException()

    item_data = item_in.model_dump()
    item = menu_item_crud.create_from_dict(db, obj_in=item_data)

    return {
        "success": True,
        "data": item
    }


@router.get("/menu/my/categories", response_model=SuccessResponse[List[MenuCategoryResponse]])
def get_my_menu_categories(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business)
) -> dict:
    """Get current restaurant's menu categories"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)

    if not restaurant:
        raise NotFoundException("Restaurant")

    categories = menu_category_crud.get_by_restaurant(
        db,
        restaurant_id=restaurant.id,
        active_only=False
    )

    return {
        "success": True,
        "data": categories
    }


@router.put("/menu/items/{item_id}", response_model=SuccessResponse[MenuItemResponse])
def update_menu_item(
        *,
        db: Session = Depends(get_db),
        item_id: UUID,
        is_available: Optional[bool] = None,
        price: Optional[Decimal] = None,
        current_user: User = Depends(require_business)
) -> dict:
    """Update menu item"""
    item = menu_item_crud.get(db, id=item_id)
    if not item:
        raise NotFoundException("Menu item")

    # Verify ownership
    category = menu_category_crud.get(db, id=item.category_id)
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)

    if not restaurant or category.restaurant_id != restaurant.id:
        raise PermissionDeniedException()

    # Update
    update_data = {}
    if is_available is not None:
        update_data["is_available"] = is_available
    if price is not None:
        update_data["price"] = price

    item = menu_item_crud.update(db, db_obj=item, obj_in=update_data)

    return {
        "success": True,
        "data": item
    }


# ============================================
# TABLE RESERVATIONS (CUSTOMER)
# ============================================

@router.post("/reservations", response_model=SuccessResponse[ReservationResponse], status_code=status.HTTP_201_CREATED)
def create_reservation(
        *,
        db: Session = Depends(get_db),
        reservation_in: ReservationCreateRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Create table reservation

    - Only for customer accounts
    - Checks restaurant capacity
    """
    reservation = food_service.make_reservation(
        db,
        current_user=current_user,
        restaurant_id=reservation_in.restaurant_id,
        reservation_date=reservation_in.reservation_date,
        reservation_time=reservation_in.reservation_time,
        number_of_guests=reservation_in.number_of_guests,
        customer_name=reservation_in.customer_name,
        customer_phone=reservation_in.customer_phone,
        customer_email=reservation_in.customer_email,
        seating_preference=reservation_in.seating_preference,
        special_requests=reservation_in.special_requests,
        occasion=reservation_in.occasion
    )

    return {
        "success": True,
        "data": reservation
    }


@router.get("/reservations/my", response_model=SuccessResponse[List[ReservationResponse]])
def get_my_reservations(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_customer),
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get current customer's reservations"""
    reservations = table_reservation_crud.get_customer_reservations(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": reservations
    }


@router.post("/reservations/{reservation_id}/cancel", response_model=SuccessResponse[ReservationResponse])
def cancel_reservation(
        *,
        db: Session = Depends(get_db),
        reservation_id: UUID,
        reason: Optional[str] = None,
        current_user: User = Depends(require_customer)
) -> dict:
    """Cancel reservation"""
    reservation = table_reservation_crud.get(db, id=reservation_id)
    if not reservation:
        raise NotFoundException("Reservation")

    if reservation.customer_id != current_user.id:
        raise PermissionDeniedException()

    if reservation.status in ["completed", "cancelled"]:
        raise ValidationException("Cannot cancel completed or already cancelled reservation")

    reservation.status = "cancelled"
    reservation.cancelled_at = datetime.utcnow()
    reservation.cancellation_reason = reason

    db.commit()
    db.refresh(reservation)

    return {
        "success": True,
        "data": reservation
    }


# ============================================
# FOOD ORDERS (CUSTOMER)
# ============================================

@router.post("/orders", response_model=SuccessResponse[FoodOrderResponse], status_code=status.HTTP_201_CREATED)
def create_food_order(
        *,
        db: Session = Depends(get_db),
        order_in: FoodOrderCreateRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Create food order

    - Only for customer accounts
    - Validates menu items
    - Processes payment
    - Creates delivery if needed
    """
    # Prepare location
    delivery_location = None
    if order_in.delivery_location:
        delivery_location = (
            order_in.delivery_location.latitude,
            order_in.delivery_location.longitude
        )

    # Convert items
    items = [item.model_dump() for item in order_in.items]

    order = food_service.create_order_and_pay(
        db,
        current_user=current_user,
        restaurant_id=order_in.restaurant_id,
        order_type=order_in.order_type,
        items=items,
        customer_name=order_in.customer_name,
        customer_phone=order_in.customer_phone,
        delivery_address=order_in.delivery_address,
        delivery_location=delivery_location,
        delivery_instructions=order_in.delivery_instructions,
        special_instructions=order_in.special_instructions,
        payment_method=order_in.payment_method,
        tip=order_in.tip
    )

    return {
        "success": True,
        "data": order
    }


@router.get("/orders/my", response_model=SuccessResponse[List[FoodOrderListResponse]])
def get_my_food_orders(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_customer),
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get current customer's food orders"""
    orders = food_order_crud.get_customer_orders(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    # Transform to list response
    order_list = []
    for order in orders:
        restaurant = restaurant_crud.get(db, id=order.restaurant_id)
        business = business_crud.get(db, id=restaurant.business_id)

        order_list.append({
            "id": order.id,
            "restaurant_name": business.business_name,
            "order_type": order.order_type,
            "total_amount": order.total_amount,
            "order_status": order.order_status,
            "created_at": order.created_at
        })

    return {
        "success": True,
        "data": order_list
    }


@router.get("/orders/{order_id}", response_model=SuccessResponse[FoodOrderResponse])
def get_food_order_details(
        *,
        db: Session = Depends(get_db),
        order_id: UUID,
        current_user: User = Depends(get_current_active_user)
) -> dict:
    """Get food order details"""
    order = food_order_crud.get(db, id=order_id)
    if not order:
        raise NotFoundException("Order")

    # Verify permission
    if current_user.user_type == "customer":
        if order.customer_id != current_user.id:
            raise PermissionDeniedException()
    elif current_user.user_type == "business":
        business = business_crud.get_by_user_id(db, user_id=current_user.id)
        restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)

        if not restaurant or order.restaurant_id != restaurant.id:
            raise PermissionDeniedException()

    return {
        "success": True,
        "data": order
    }


# ============================================
# RESTAURANT ORDER MANAGEMENT (BUSINESS)
# ============================================

@router.get("/orders/restaurant/my", response_model=SuccessResponse[List[FoodOrderResponse]])
def get_restaurant_orders(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business),
        pagination: dict = Depends(get_pagination_params),
        status: Optional[str] = Query(None)
) -> dict:
    """Get restaurant's orders"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)

    if not restaurant:
        raise NotFoundException("Restaurant")

    orders = food_order_crud.get_restaurant_orders(
        db,
        restaurant_id=restaurant.id,
        status=status,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": orders
    }


@router.post("/orders/{order_id}/confirm", response_model=SuccessResponse[FoodOrderResponse])
def confirm_food_order(
        *,
        db: Session = Depends(get_db),
        order_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Confirm food order (restaurant action)"""
    order = food_order_crud.get(db, id=order_id)
    if not order:
        raise NotFoundException("Order")

    # Verify ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)

    if not restaurant or order.restaurant_id != restaurant.id:
        raise PermissionDeniedException()

    order = food_order_crud.update_order_status(
        db,
        order_id=order_id,
        new_status="confirmed"
    )

    return {
        "success": True,
        "data": order
    }


@router.post("/orders/{order_id}/preparing", response_model=SuccessResponse[FoodOrderResponse])
def mark_order_preparing(
        *,
        db: Session = Depends(get_db),
        order_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Mark order as preparing"""
    order = food_order_crud.get(db, id=order_id)
    if not order:
        raise NotFoundException("Order")

    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)

    if not restaurant or order.restaurant_id != restaurant.id:
        raise PermissionDeniedException()

    order = food_order_crud.update_order_status(db, order_id=order_id, new_status="preparing")

    return {
        "success": True,
        "data": order
    }


@router.post("/orders/{order_id}/ready", response_model=SuccessResponse[FoodOrderResponse])
def mark_order_ready(
        *,
        db: Session = Depends(get_db),
        order_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Mark order as ready for pickup/delivery"""
    order = food_order_crud.get(db, id=order_id)
    if not order:
        raise NotFoundException("Order")

    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)

    if not restaurant or order.restaurant_id != restaurant.id:
        raise PermissionDeniedException()

    order = food_order_crud.update_order_status(db, order_id=order_id, new_status="ready")

    # TODO: Notify rider if delivery order

    return {
        "success": True,
        "data": order
    }


# ============================================
# RESTAURANT RESERVATIONS MANAGEMENT (BUSINESS)
# ============================================

@router.get("/reservations/restaurant/my", response_model=SuccessResponse[List[ReservationResponse]])
def get_restaurant_reservations(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business),
        pagination: dict = Depends(get_pagination_params),
        reservation_date: Optional[date] = Query(None),
        status: Optional[str] = Query(None)
) -> dict:
    """Get restaurant's reservations"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)

    if not restaurant:
        raise NotFoundException("Restaurant")

    reservations = table_reservation_crud.get_restaurant_reservations(
        db,
        restaurant_id=restaurant.id,
        reservation_date=reservation_date,
        status=status,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": reservations
    }


@router.post("/reservations/{reservation_id}/confirm", response_model=SuccessResponse[ReservationResponse])
def confirm_reservation(
        *,
        db: Session = Depends(get_db),
        reservation_id: UUID,
        table_number: Optional[str] = None,
        current_user: User = Depends(require_business)
) -> dict:
    """Confirm reservation (restaurant action)"""
    reservation = table_reservation_crud.get(db, id=reservation_id)
    if not reservation:
        raise NotFoundException("Reservation")

    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)

    if not restaurant or reservation.restaurant_id != restaurant.id:
        raise PermissionDeniedException()

    reservation.status = "confirmed"
    reservation.confirmed_at = datetime.utcnow()
    if table_number:
        reservation.table_number = table_number

    db.commit()
    db.refresh(reservation)

    return {
        "success": True,
        "data": reservation
    }