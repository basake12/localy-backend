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
    get_pagination_params,
)
from app.schemas.common_schema import SuccessResponse
from app.schemas.food_schema import (
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
    RestaurantSearchFilters,
)
from app.services.food_service import food_service
from app.crud.food_crud import (
    restaurant_crud,
    menu_category_crud,
    menu_item_crud,
    table_reservation_crud,
    food_order_crud,
)
from app.crud.business_crud import business_crud
from app.models.user_model import User
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)

router = APIRouter()


# ============================================================
# PUBLIC: RESTAURANT LIST  (Flutter: GET /food/restaurants)
# ============================================================

@router.get("/restaurants", response_model=SuccessResponse[List[dict]])
def list_restaurants(
    *,
    db: Session = Depends(get_db),
    lga_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    offers_delivery: Optional[bool] = Query(None),
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    """
    List / search restaurants — the primary browse endpoint.

    Flutter: ApiEndpoints.restaurantsList → GET /food/restaurants
    Supports: ?lga_id, ?q (text search), ?category (cuisine), ?offers_delivery
    """
    results = food_service.search_restaurants(
        db,
        query_text=q,
        cuisine_type=category,
        offers_delivery=offers_delivery,
        lga_id=lga_id,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": {"restaurants": results, "total": len(results)}}


# ============================================================
# PUBLIC: RESTAURANT DETAIL & MENU
# ============================================================

@router.get("/restaurants/{restaurant_id}", response_model=SuccessResponse[dict])
def get_restaurant_details(
    *,
    db: Session = Depends(get_db),
    restaurant_id: UUID,
) -> dict:
    """
    Full restaurant profile + embedded menu.
    Flutter: ApiEndpoints.restaurantById(id)
    """
    data = food_service.get_restaurant_details(db, restaurant_id=restaurant_id)
    return {"success": True, "data": data}


@router.get("/restaurants/{restaurant_id}/menu", response_model=SuccessResponse[MenuResponse])
def get_restaurant_menu(
    *,
    db: Session = Depends(get_db),
    restaurant_id: UUID,
) -> dict:
    """
    Menu categories + items only.
    Flutter: ApiEndpoints.restaurantMenu(id)
    """
    menu_raw = menu_item_crud.get_restaurant_menu(db, restaurant_id=restaurant_id)
    categories = [
        {
            "id": str(cat["category"].id),
            "name": cat["category"].name,
            "description": cat["category"].description,
            "display_order": cat["category"].display_order,
            "items": [
                food_service._serialize_menu_item(item) for item in cat["items"]
            ],
        }
        for cat in menu_raw
    ]
    return {"success": True, "data": {"categories": categories}}


# ============================================================
# PUBLIC: ADVANCED SEARCH
# ============================================================

@router.post("/restaurants/search", response_model=SuccessResponse[List[dict]])
def search_restaurants(
    *,
    db: Session = Depends(get_db),
    search_params: RestaurantSearchFilters,
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    """
    Advanced search with location radius and filters.
    Flutter can call this for the filter-sheet results.
    """
    location = None
    if search_params.location:
        location = (
            search_params.location.latitude,
            search_params.location.longitude,
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
        limit=pagination["limit"],
    )
    return {"success": True, "data": results}


# ============================================================
# BUSINESS: CREATE / GET MY RESTAURANT
# ============================================================

@router.post(
    "/restaurants",
    response_model=SuccessResponse[RestaurantResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_restaurant(
    *,
    db: Session = Depends(get_db),
    restaurant_in: RestaurantCreateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    """Create restaurant for a food-category business."""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")
    if business.category != "food":
        raise ValidationException("Only food-category businesses can create restaurants")
    if restaurant_crud.get_by_business_id(db, business_id=business.id):
        raise ValidationException("Restaurant already exists for this business")

    data = restaurant_in.model_dump()
    data["business_id"] = business.id
    restaurant = restaurant_crud.create_from_dict(db, obj_in=data)
    return {"success": True, "data": restaurant}


@router.get("/my", response_model=SuccessResponse[RestaurantResponse])
def get_my_restaurant(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
) -> dict:
    """
    Get current business's restaurant profile.
    Flutter: ApiEndpoints.myRestaurant
    """
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant:
        raise NotFoundException("Restaurant")
    return {"success": True, "data": restaurant}


# ============================================================
# MENU MANAGEMENT (BUSINESS)
# ============================================================

@router.post("/menu", include_in_schema=False)
def menu_root_redirect() -> dict:
    from fastapi import HTTPException
    raise HTTPException(
        status_code=404,
        detail={
            "message": "Use /food/menu/categories or /food/menu/items",
            "valid_endpoints": [
                "POST /api/v1/food/menu/categories",
                "POST /api/v1/food/menu/items",
            ],
        },
    )


@router.get("/my/menu", response_model=SuccessResponse[MenuResponse])
def get_my_menu(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
) -> dict:
    """
    Full menu for the authenticated restaurant owner.
    Flutter Dashboard: manage_menu_screen
    """
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant:
        raise NotFoundException("Restaurant")

    menu_raw = menu_item_crud.get_restaurant_menu(db, restaurant_id=restaurant.id)
    categories = [
        {
            "id": str(cat["category"].id),
            "name": cat["category"].name,
            "description": cat["category"].description,
            "display_order": cat["category"].display_order,
            "is_active": cat["category"].is_active,
            "items": [
                food_service._serialize_menu_item(item) for item in cat["items"]
            ],
        }
        for cat in menu_raw
    ]
    return {"success": True, "data": {"categories": categories}}


@router.post(
    "/menu/categories",
    response_model=SuccessResponse[MenuCategoryResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_menu_category(
    *,
    db: Session = Depends(get_db),
    category_in: MenuCategoryCreateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    """Create a menu category."""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant:
        raise NotFoundException("Restaurant")

    data = category_in.model_dump()
    data["restaurant_id"] = restaurant.id
    category = menu_category_crud.create_from_dict(db, obj_in=data)
    return {"success": True, "data": category}


@router.get("/menu/my/categories", response_model=SuccessResponse[List[MenuCategoryResponse]])
def get_my_menu_categories(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
) -> dict:
    """List all categories for the authenticated restaurant."""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant:
        raise NotFoundException("Restaurant")

    categories = menu_category_crud.get_by_restaurant(
        db, restaurant_id=restaurant.id, active_only=False
    )
    return {"success": True, "data": categories}


@router.post(
    "/menu/items",
    response_model=SuccessResponse[MenuItemResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_menu_item(
    *,
    db: Session = Depends(get_db),
    item_in: MenuItemCreateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    """Create a new menu item."""
    category = menu_category_crud.get(db, id=item_in.category_id)
    if not category:
        raise NotFoundException("Menu category")

    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant or category.restaurant_id != restaurant.id:
        raise PermissionDeniedException()

    item = menu_item_crud.create_from_dict(db, obj_in=item_in.model_dump())
    return {"success": True, "data": item}


@router.patch("/menu/items/{item_id}", response_model=SuccessResponse[MenuItemResponse])
def update_menu_item(
    *,
    db: Session = Depends(get_db),
    item_id: UUID,
    is_available: Optional[bool] = None,
    price: Optional[Decimal] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    discount_price: Optional[Decimal] = None,
    current_user: User = Depends(require_business),
) -> dict:
    """
    Partial update a menu item (availability toggle, price, name, etc.).
    Flutter Dashboard: manage_menu_screen edit + toggle
    """
    item = menu_item_crud.get(db, id=item_id)
    if not item:
        raise NotFoundException("Menu item")

    category = menu_category_crud.get(db, id=item.category_id)
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant or category.restaurant_id != restaurant.id:
        raise PermissionDeniedException()

    update_data = {}
    if is_available is not None:
        update_data["is_available"] = is_available
    if price is not None:
        update_data["price"] = price
    if name is not None:
        update_data["name"] = name
    if description is not None:
        update_data["description"] = description
    if discount_price is not None:
        update_data["discount_price"] = discount_price

    item = menu_item_crud.update(db, db_obj=item, obj_in=update_data)
    return {"success": True, "data": item}


@router.delete("/menu/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_menu_item(
    *,
    db: Session = Depends(get_db),
    item_id: UUID,
    current_user: User = Depends(require_business),
) -> None:
    """
    Delete a menu item.
    Flutter Dashboard: manage_menu_screen delete button
    """
    item = menu_item_crud.get(db, id=item_id)
    if not item:
        raise NotFoundException("Menu item")

    category = menu_category_crud.get(db, id=item.category_id)
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant or category.restaurant_id != restaurant.id:
        raise PermissionDeniedException()

    menu_item_crud.remove(db, id=item_id)


# ============================================================
# FOOD ORDERS — CUSTOMER
# ============================================================

@router.post(
    "/orders",
    response_model=SuccessResponse[FoodOrderResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_food_order(
    *,
    db: Session = Depends(get_db),
    order_in: FoodOrderCreateRequest,
    current_user: User = Depends(require_customer),
) -> dict:
    """
    Place a food order.
    Flutter: ApiEndpoints.createFoodOrder(restaurantId)
    """
    delivery_location = None
    if order_in.delivery_location:
        delivery_location = (
            order_in.delivery_location.latitude,
            order_in.delivery_location.longitude,
        )

    order = food_service.create_order_and_pay(
        db,
        current_user=current_user,
        restaurant_id=order_in.restaurant_id,
        order_type=order_in.order_type,
        items=[item.model_dump() for item in order_in.items],
        customer_name=order_in.customer_name,
        customer_phone=order_in.customer_phone,
        delivery_address=order_in.delivery_address,
        delivery_location=delivery_location,
        delivery_instructions=order_in.delivery_instructions,
        special_instructions=order_in.special_instructions,
        payment_method=order_in.payment_method,
        tip=order_in.tip,
        promo_code=getattr(order_in, "promo_code", None),
    )
    return {"success": True, "data": order}


@router.get("/orders/my", response_model=SuccessResponse[List[FoodOrderListResponse]])
def get_my_food_orders(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    """
    Customer's order history.
    Flutter: ApiEndpoints.myFoodOrders
    """
    orders = food_order_crud.get_customer_orders(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    order_list = []
    for order in orders:
        restaurant = restaurant_crud.get(db, id=order.restaurant_id)
        business = business_crud.get(db, id=restaurant.business_id) if restaurant else None
        order_list.append({
            "id": order.id,
            "restaurant_name": business.business_name if business else "Unknown",
            "order_type": order.order_type,
            "total_amount": order.total_amount,
            "order_status": order.order_status,
            "created_at": order.created_at,
        })
    return {"success": True, "data": order_list}


@router.get("/orders/{order_id}", response_model=SuccessResponse[FoodOrderResponse])
def get_food_order_details(
    *,
    db: Session = Depends(get_db),
    order_id: UUID,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """
    Single order detail — used by tracking screen.
    Flutter: ApiEndpoints.foodOrderById(id)
    """
    order = food_order_crud.get(db, id=order_id)
    if not order:
        raise NotFoundException("Order")

    if current_user.user_type == "customer":
        if order.customer_id != current_user.id:
            raise PermissionDeniedException()
    elif current_user.user_type == "business":
        business = business_crud.get_by_user_id(db, user_id=current_user.id)
        restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
        if not restaurant or order.restaurant_id != restaurant.id:
            raise PermissionDeniedException()

    return {"success": True, "data": order}


@router.post("/orders/{order_id}/cancel", response_model=SuccessResponse[FoodOrderResponse])
def cancel_food_order(
    *,
    db: Session = Depends(get_db),
    order_id: UUID,
    reason: Optional[str] = None,
    current_user: User = Depends(require_customer),
) -> dict:
    """
    Cancel a food order (customer action).
    Flutter: ApiEndpoints.cancelFoodOrder(id)
    """
    order = food_order_crud.get(db, id=order_id)
    if not order:
        raise NotFoundException("Order")
    if order.customer_id != current_user.id:
        raise PermissionDeniedException()
    if order.order_status not in ["pending", "confirmed"]:
        raise ValidationException(
            "Orders can only be cancelled when pending or confirmed"
        )

    order.order_status = "cancelled"
    order.cancellation_reason = reason
    order.cancelled_at = datetime.utcnow()
    db.commit()
    db.refresh(order)
    return {"success": True, "data": order}


# ============================================================
# FOOD ORDERS — BUSINESS (KDS + status transitions)
# ============================================================

@router.get("/my/orders", response_model=SuccessResponse[List[FoodOrderResponse]])
def get_restaurant_orders(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
    pagination: dict = Depends(get_pagination_params),
    order_status: Optional[str] = Query(None, alias="status"),
) -> dict:
    """
    Restaurant's incoming orders, optionally filtered by status.
    Flutter Dashboard: active_orders_screen (polls this with ?status=pending,preparing)
    Also: ApiEndpoints.myRestaurantOrders
    """
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant:
        raise NotFoundException("Restaurant")

    orders = food_order_crud.get_restaurant_orders(
        db,
        restaurant_id=restaurant.id,
        status=order_status,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": orders}


@router.patch(
    "/my/orders/{order_id}/status",
    response_model=SuccessResponse[FoodOrderResponse],
)
def update_order_status(
    *,
    db: Session = Depends(get_db),
    order_id: UUID,
    new_status: str = Query(..., alias="status"),
    current_user: User = Depends(require_business),
) -> dict:
    """
    KDS status transition: pending → confirmed → preparing → ready → delivered.
    Flutter Dashboard: active_orders_screen action buttons
    """
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant:
        raise NotFoundException("Restaurant")

    order = food_service.update_order_status(
        db,
        order_id=order_id,
        new_status=new_status,
        restaurant_id=restaurant.id,
    )
    return {"success": True, "data": order}


# ============================================================
# TABLE RESERVATIONS
# ============================================================

@router.post(
    "/reservations",
    response_model=SuccessResponse[ReservationResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_reservation(
    *,
    db: Session = Depends(get_db),
    reservation_in: ReservationCreateRequest,
    current_user: User = Depends(require_customer),
) -> dict:
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
        occasion=reservation_in.occasion,
    )
    return {"success": True, "data": reservation}


@router.get("/reservations/my", response_model=SuccessResponse[List[ReservationResponse]])
def get_my_reservations(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    reservations = table_reservation_crud.get_customer_reservations(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": reservations}


@router.post(
    "/reservations/{reservation_id}/cancel",
    response_model=SuccessResponse[ReservationResponse],
)
def cancel_reservation(
    *,
    db: Session = Depends(get_db),
    reservation_id: UUID,
    reason: Optional[str] = None,
    current_user: User = Depends(require_customer),
) -> dict:
    reservation = table_reservation_crud.get(db, id=reservation_id)
    if not reservation:
        raise NotFoundException("Reservation")
    if reservation.customer_id != current_user.id:
        raise PermissionDeniedException()
    if reservation.status in ["completed", "cancelled"]:
        raise ValidationException("Cannot cancel a completed or already cancelled reservation")

    reservation.status = "cancelled"
    reservation.cancelled_at = datetime.utcnow()
    reservation.cancellation_reason = reason
    db.commit()
    db.refresh(reservation)
    return {"success": True, "data": reservation}


@router.get(
    "/reservations/restaurant/my",
    response_model=SuccessResponse[List[ReservationResponse]],
)
def get_restaurant_reservations(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
    pagination: dict = Depends(get_pagination_params),
    reservation_date: Optional[date] = Query(None),
    res_status: Optional[str] = Query(None, alias="status"),
) -> dict:
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant:
        raise NotFoundException("Restaurant")

    reservations = table_reservation_crud.get_restaurant_reservations(
        db,
        restaurant_id=restaurant.id,
        reservation_date=reservation_date,
        status=res_status,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": reservations}


@router.post(
    "/reservations/{reservation_id}/confirm",
    response_model=SuccessResponse[ReservationResponse],
)
def confirm_reservation(
    *,
    db: Session = Depends(get_db),
    reservation_id: UUID,
    table_number: Optional[str] = None,
    current_user: User = Depends(require_business),
) -> dict:
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
    return {"success": True, "data": reservation}


# ============================================================
# PROMOTIONS  (business dashboard)
# ============================================================

@router.get("/my/promotions", response_model=SuccessResponse[List[dict]])
def get_my_promotions(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
) -> dict:
    """
    List promotions/coupons for the authenticated restaurant.
    Flutter Dashboard: promotions_screen
    """
    try:
        from app.models.coupon import Coupon
        from sqlalchemy import and_

        business = business_crud.get_by_user_id(db, user_id=current_user.id)
        restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
        if not restaurant:
            raise NotFoundException("Restaurant")

        coupons = (
            db.query(Coupon)
            .filter(Coupon.business_id == business.id)
            .order_by(Coupon.created_at.desc())
            .all()
        )
        return {
            "success": True,
            "data": [
                {
                    "id": str(c.id),
                    "title": c.title,
                    "code": c.code,
                    "discount": (
                        f"{c.discount_value}%"
                        if c.discount_type == "percentage"
                        else f"₦{c.discount_value:,.0f} off"
                    ),
                    "discount_type": c.discount_type,
                    "discount_value": float(c.discount_value),
                    "is_active": c.is_active,
                    "uses": c.usage_count or 0,
                    "expires": c.expires_at.isoformat() if c.expires_at else None,
                }
                for c in coupons
            ],
        }
    except ImportError:
        return {"success": True, "data": []}


@router.post(
    "/my/promotions",
    response_model=SuccessResponse[dict],
    status_code=status.HTTP_201_CREATED,
)
def create_promotion(
    *,
    db: Session = Depends(get_db),
    title: str,
    code: str,
    discount_type: str,
    discount_value: Decimal,
    expires_at: Optional[datetime] = None,
    current_user: User = Depends(require_business),
) -> dict:
    """Create a promotion/coupon. Flutter Dashboard: promotions_screen + button."""
    try:
        from app.models.coupon import Coupon

        business = business_crud.get_by_user_id(db, user_id=current_user.id)
        coupon = Coupon(
            business_id=business.id,
            title=title,
            code=code.upper(),
            discount_type=discount_type,
            discount_value=discount_value,
            expires_at=expires_at,
            is_active=True,
            usage_count=0,
        )
        db.add(coupon)
        db.commit()
        db.refresh(coupon)
        return {"success": True, "data": {"id": str(coupon.id), "code": coupon.code}}
    except ImportError:
        raise ValidationException("Coupons module not yet migrated")


@router.patch("/my/promotions/{promotion_id}", response_model=SuccessResponse[dict])
def toggle_promotion(
    *,
    db: Session = Depends(get_db),
    promotion_id: UUID,
    is_active: bool,
    current_user: User = Depends(require_business),
) -> dict:
    """Toggle promotion active/inactive. Flutter Dashboard: promotions_screen switch."""
    try:
        from app.models.coupon import Coupon

        business = business_crud.get_by_user_id(db, user_id=current_user.id)
        coupon = db.query(Coupon).filter(
            Coupon.id == promotion_id, Coupon.business_id == business.id
        ).first()
        if not coupon:
            raise NotFoundException("Promotion")

        coupon.is_active = is_active
        db.commit()
        return {"success": True, "data": {"id": str(coupon.id), "is_active": is_active}}
    except ImportError:
        raise ValidationException("Coupons module not yet migrated")


# ============================================================
# ANALYTICS  (business dashboard)
# ============================================================

@router.get("/my/analytics", response_model=SuccessResponse[dict])
def get_food_analytics(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
    period: str = Query("30d", regex="^(7d|30d|90d)$"),
) -> dict:
    """
    Food-specific revenue analytics.
    Flutter Dashboard: food_analytics_screen
    """
    from datetime import timedelta
    from sqlalchemy import func

    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant:
        raise NotFoundException("Restaurant")

    days = int(period.replace("d", ""))
    since = datetime.utcnow() - timedelta(days=days)

    from app.models.food_model import FoodOrder, FoodOrderItem, MenuItem

    # Revenue chart
    daily_revenue = (
        db.query(
            func.date(FoodOrder.created_at).label("date"),
            func.sum(FoodOrder.total_amount).label("amount"),
        )
        .filter(
            FoodOrder.restaurant_id == restaurant.id,
            FoodOrder.created_at >= since,
            FoodOrder.payment_status == "paid",
        )
        .group_by(func.date(FoodOrder.created_at))
        .order_by(func.date(FoodOrder.created_at))
        .all()
    )

    # Top menu items by orders
    top_items_raw = (
        db.query(
            FoodOrderItem.item_name,
            func.sum(FoodOrderItem.quantity).label("total_orders"),
        )
        .join(FoodOrder, FoodOrderItem.order_id == FoodOrder.id)
        .filter(
            FoodOrder.restaurant_id == restaurant.id,
            FoodOrder.created_at >= since,
        )
        .group_by(FoodOrderItem.item_name)
        .order_by(func.sum(FoodOrderItem.quantity).desc())
        .limit(10)
        .all()
    )

    max_orders = top_items_raw[0].total_orders if top_items_raw else 1

    # Summary stats
    total_orders = (
        db.query(func.count(FoodOrder.id))
        .filter(
            FoodOrder.restaurant_id == restaurant.id,
            FoodOrder.created_at >= since,
        )
        .scalar()
        or 0
    )

    total_revenue = (
        db.query(func.sum(FoodOrder.total_amount))
        .filter(
            FoodOrder.restaurant_id == restaurant.id,
            FoodOrder.created_at >= since,
            FoodOrder.payment_status == "paid",
        )
        .scalar()
        or 0
    )

    return {
        "success": True,
        "data": {
            "period": period,
            "total_orders": total_orders,
            "total_revenue": float(total_revenue),
            "avg_order_value": (
                float(total_revenue) / total_orders if total_orders else 0
            ),
            "revenue_chart": [
                {"date": str(row.date), "amount": float(row.amount)}
                for row in daily_revenue
            ],
            "top_items": [
                {
                    "name": row.item_name,
                    "orders": row.total_orders,
                    "percentage": row.total_orders / max_orders,
                }
                for row in top_items_raw
            ],
        },
    }