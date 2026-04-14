from decimal import Decimal
from pydantic import BaseModel

from fastapi import APIRouter, Depends, Query, status, Body
from sqlalchemy.orm import Session, joinedload
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
    MenuItemUpdateRequest,
    MenuItemResponse,
    MenuResponse,
    ReservationCreateRequest,
    ReservationResponse,
    FoodOrderCreateRequest,
    FoodOrderResponse,
    FoodOrderListResponse,
    RestaurantSearchFilters,
    CookingServiceCreateRequest,
    CookingServiceResponse,
    CookingBookingCreateRequest,
    CookingBookingResponse,
)
from app.services.food_service import food_service
from app.crud.food_crud import (
    restaurant_crud,
    menu_category_crud,
    menu_item_crud,
    table_reservation_crud,
    food_order_crud,
    cooking_service_crud,
    cooking_booking_crud,
)
from app.models.user_model import User
from app.models.business_model import Business
from app.models.food_model import FoodOrder, TableReservation, CookingService
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)

router = APIRouter()


def _get_business(db: Session, user_id) -> Optional[Business]:
    """
    Sync helper — replaces business_crud.get_by_user_id() throughout this router.
    business_crud (CRUDBusiness) extends AsyncCRUDBase; all its methods are
    coroutines and cannot be called from sync endpoints without await.
    """
    return db.query(Business).filter(Business.user_id == user_id).first()


# ============================================================
# PUBLIC: RESTAURANT LIST (Radius-based ONLY — NO LGA)
# ============================================================

@router.get("/restaurants", response_model=SuccessResponse[dict])
def list_restaurants(
        *,
        db: Session = Depends(get_db),
        q: Optional[str] = Query(None),
        category: Optional[str] = Query(None),
        offers_delivery: Optional[bool] = Query(None),
        latitude: Optional[float] = Query(None),
        longitude: Optional[float] = Query(None),
        radius_km: float = Query(5.0, ge=1, le=50),
        pagination: dict = Depends(get_pagination_params),
) -> dict:
    """
    List / search restaurants — the primary browse endpoint.

    BLUEPRINT v2.0 COMPLIANCE:
    - Radius-based search ONLY (default 5 km)
    - NO LGA filtering
    - Location required for geo-filtered results

    Flutter: ApiEndpoints.restaurantsList → GET /food/restaurants
    """
    location = None
    if latitude is not None and longitude is not None:
        location = (latitude, longitude)

    results = food_service.search_restaurants(
        db,
        query_text=q,
        cuisine_type=category,
        location=location,
        radius_km=radius_km,
        offers_delivery=offers_delivery,
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
    Advanced search with location radius and dietary filters.

    BLUEPRINT v2.0 COMPLIANCE:
    - Radius-based ONLY
    - Supports dietary filters: halal, vegetarian, vegan, gluten-free
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
        radius_km=search_params.radius_km or 5.0,
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
    """Create restaurant for a food-category business"""
    business = _get_business(db, current_user.id)
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
    business = _get_business(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant:
        raise NotFoundException("Restaurant")
    return {"success": True, "data": restaurant}


@router.patch("/my", response_model=SuccessResponse[RestaurantResponse])
def update_my_restaurant(
        *,
        db: Session = Depends(get_db),
        updates: dict = Body(...),
        current_user: User = Depends(require_business),
) -> dict:
    """Update restaurant settings (real-time wait, virtual tour, etc.)"""
    business = _get_business(db, current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant:
        raise NotFoundException("Restaurant")

    restaurant = restaurant_crud.update(db, db_obj=restaurant, obj_in=updates)
    return {"success": True, "data": restaurant}


# ============================================================
# MENU MANAGEMENT (BUSINESS)
# ============================================================

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
    business = _get_business(db, current_user.id)
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
    """Create a menu category"""
    business = _get_business(db, current_user.id)
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
    """List all categories for the authenticated restaurant"""
    business = _get_business(db, current_user.id)
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
    """Create a new menu item"""
    category = menu_category_crud.get(db, id=item_in.category_id)
    if not category:
        raise NotFoundException("Menu category")

    business = _get_business(db, current_user.id)
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
        item_in: MenuItemUpdateRequest = Body(...),
        current_user: User = Depends(require_business),
) -> dict:
    """
    Partial update a menu item (availability toggle, price, name, halal, etc.).
    Flutter Dashboard: manage_menu_screen edit + toggle
    """
    item = menu_item_crud.get(db, id=item_id)
    if not item:
        raise NotFoundException("Menu item")

    category = menu_category_crud.get(db, id=item.category_id)
    business = _get_business(db, current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant or category.restaurant_id != restaurant.id:
        raise PermissionDeniedException()

    update_data = item_in.model_dump(exclude_none=True)
    item = menu_item_crud.update(db, db_obj=item, obj_in=update_data)
    return {"success": True, "data": item}


@router.delete("/menu/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_menu_item(
        *,
        db: Session = Depends(get_db),
        item_id: UUID,
        current_user: User = Depends(require_business),
) -> None:
    """Delete a menu item"""
    item = menu_item_crud.get(db, id=item_id)
    if not item:
        raise NotFoundException("Menu item")

    category = menu_category_crud.get(db, id=item.category_id)
    business = _get_business(db, current_user.id)
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

    BLUEPRINT v2.0 COMPLIANCE:
    - Platform fee ₦50 included in total
    - Supports scheduled delivery
    - Supports group orders

    Flutter: ApiEndpoints.foodOrders → POST /food/orders
    """
    delivery_location = None
    if order_in.delivery_location:
        delivery_location = (
            order_in.delivery_location.latitude,
            order_in.delivery_location.longitude,
        )

    # Resolve customer_name / customer_phone — explicitly query profile
    # since lazy-loaded relationships may not be available in sync context.
    # Final fallbacks ensure NOT NULL DB constraint is never violated.
    customer_name = order_in.customer_name
    customer_phone = order_in.customer_phone
    if not customer_name or not customer_phone:
        from app.models.user_model import CustomerProfile
        profile = db.query(CustomerProfile).filter(
            CustomerProfile.user_id == current_user.id
        ).first()
        if profile:
            if not customer_name:
                customer_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip()
            if not customer_phone:
                customer_phone = current_user.phone or ""
    customer_name = customer_name or "Customer"
    customer_phone = customer_phone or current_user.phone or ""

    order = food_service.create_order_and_pay(
        db,
        current_user=current_user,
        restaurant_id=order_in.restaurant_id,
        order_type=order_in.order_type,
        items=[item.model_dump() for item in order_in.items],
        customer_name=customer_name,
        customer_phone=customer_phone,
        delivery_address=order_in.delivery_address,
        delivery_location=delivery_location,
        delivery_instructions=order_in.delivery_instructions,
        scheduled_delivery_time=order_in.scheduled_delivery_time,
        group_order_id=order_in.group_order_id,
        is_group_order_host=order_in.is_group_order_host,
        special_instructions=order_in.special_instructions,
        payment_method=order_in.payment_method,
        tip=order_in.tip,
        promo_code=order_in.promo_code,
    )

    # Eager-load items for response
    db.refresh(order)
    order_with_items = db.query(FoodOrder).options(
        joinedload(FoodOrder.items)
    ).filter(FoodOrder.id == order.id).first()

    return {"success": True, "data": order_with_items}


@router.get("/orders/my", response_model=SuccessResponse[List[FoodOrderResponse]])
def get_my_orders(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_customer),
        pagination: dict = Depends(get_pagination_params),
) -> dict:
    """Get customer's food order history"""
    orders = food_order_crud.get_customer_orders(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": orders}


@router.get("/orders/{order_id}", response_model=SuccessResponse[FoodOrderResponse])
def get_order_details(
        *,
        db: Session = Depends(get_db),
        order_id: UUID,
        current_user: User = Depends(get_current_active_user),
) -> dict:
    """Get order details"""
    order = db.query(FoodOrder).options(
        joinedload(FoodOrder.items)
    ).filter(FoodOrder.id == order_id).first()

    if not order:
        raise NotFoundException("Order")

    # Check ownership (customer or business owner)
    business = _get_business(db, current_user.id)
    is_owner = order.customer_id == current_user.id
    is_restaurant_owner = (
            business
            and restaurant_crud.get_by_business_id(db, business_id=business.id)
            and restaurant_crud.get_by_business_id(db, business_id=business.id).id
            == order.restaurant_id
    )

    if not (is_owner or is_restaurant_owner):
        raise PermissionDeniedException()

    return {"success": True, "data": order}


class _CancelOrderRequest(BaseModel):
    reason: Optional[str] = None

@router.post("/orders/{order_id}/cancel", response_model=SuccessResponse[FoodOrderResponse])
def cancel_order(
        *,
        db: Session = Depends(get_db),
        order_id: UUID,
        payload: Optional[_CancelOrderRequest] = Body(default=None),
        current_user: User = Depends(require_customer),
) -> dict:
    """Cancel an order (customer only, within allowed statuses).
    Body is optional — POST with no body, empty {}, or {"reason": "..."}."""
    order = food_order_crud.cancel_order(
        db, order_id=order_id, customer_id=current_user.id,
        reason=payload.reason if payload else None
    )
    return {"success": True, "data": order}


# ============================================================
# FOOD ORDERS — BUSINESS
# ============================================================

@router.get("/my/orders", response_model=SuccessResponse[List[FoodOrderResponse]])
def get_restaurant_orders(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business),
        status: Optional[str] = Query(None),
        pagination: dict = Depends(get_pagination_params),
) -> dict:
    """Get orders for the authenticated restaurant"""
    business = _get_business(db, current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant:
        raise NotFoundException("Restaurant")

    orders = food_order_crud.get_restaurant_orders(
        db,
        restaurant_id=restaurant.id,
        status=status,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": orders}


@router.patch("/orders/{order_id}/status", response_model=SuccessResponse[FoodOrderResponse])
def update_order_status(
        *,
        db: Session = Depends(get_db),
        order_id: UUID,
        new_status: str = Body(..., embed=True),
        current_user: User = Depends(require_business),
) -> dict:
    """Update order status (business only)"""
    business = _get_business(db, current_user.id)
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
# TABLE RESERVATIONS — CUSTOMER
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
    """
    Create a table reservation.

    BLUEPRINT v2.0 COMPLIANCE:
    - Supports deposit option
    """
    # Resolve customer_name / customer_phone from profile if not supplied
    reservation_data = reservation_in.model_dump()
    if not reservation_data.get("customer_name") or not reservation_data.get("customer_phone"):
        profile = getattr(current_user, "customer_profile", None)
        if profile:
            if not reservation_data.get("customer_name"):
                reservation_data["customer_name"] = f"{profile.first_name or ''} {profile.last_name or ''}".strip() or "Customer"
            if not reservation_data.get("customer_phone"):
                reservation_data["customer_phone"] = current_user.phone or ""
        else:
            reservation_data["customer_name"] = reservation_data.get("customer_name") or "Customer"
            reservation_data["customer_phone"] = reservation_data.get("customer_phone") or current_user.phone or ""

    reservation = food_service.make_reservation(
        db,
        current_user=current_user,
        **reservation_data,
    )
    return {"success": True, "data": reservation}


@router.get("/reservations/my", response_model=SuccessResponse[List[ReservationResponse]])
def get_my_reservations(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_customer),
        pagination: dict = Depends(get_pagination_params),
) -> dict:
    """Get customer's reservations"""
    reservations = db.query(TableReservation).filter(
        TableReservation.customer_id == current_user.id
    ).order_by(
        TableReservation.reservation_date.desc()
    ).offset(pagination["skip"]).limit(pagination["limit"]).all()

    return {"success": True, "data": reservations}


# ============================================================
# TABLE RESERVATIONS — BUSINESS
# ============================================================

@router.get("/my/reservations", response_model=SuccessResponse[List[ReservationResponse]])
def get_restaurant_reservations(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business),
        status: Optional[str] = Query(None),
        pagination: dict = Depends(get_pagination_params),
) -> dict:
    """Get reservations for the authenticated restaurant"""
    business = _get_business(db, current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant:
        raise NotFoundException("Restaurant")

    reservations = table_reservation_crud.get_restaurant_reservations(
        db,
        restaurant_id=restaurant.id,
        status=status,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": reservations}


# ============================================================
# COOKING SERVICES (Catering, Private Chef, Classes)
# ============================================================

@router.post(
    "/cooking-services",
    response_model=SuccessResponse[CookingServiceResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_cooking_service(
        *,
        db: Session = Depends(get_db),
        service_in: CookingServiceCreateRequest,
        current_user: User = Depends(require_business),
) -> dict:
    """Create a cooking service offering"""
    business = _get_business(db, current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant:
        raise NotFoundException("Restaurant")

    data = service_in.model_dump()
    data["restaurant_id"] = restaurant.id
    service = cooking_service_crud.create_from_dict(db, obj_in=data)
    return {"success": True, "data": service}


@router.get("/cooking-services", response_model=SuccessResponse[List[CookingServiceResponse]])
def list_cooking_services(
        *,
        db: Session = Depends(get_db),
        service_type: Optional[str] = Query(None),
        pagination: dict = Depends(get_pagination_params),
) -> dict:
    """List available cooking services"""
    query = db.query(CookingService).filter(CookingService.is_active == True)

    if service_type:
        query = query.filter(CookingService.service_type == service_type)

    services = query.offset(pagination["skip"]).limit(pagination["limit"]).all()
    return {"success": True, "data": services}


@router.post(
    "/cooking-services/{service_id}/bookings",
    response_model=SuccessResponse[CookingBookingResponse],
    status_code=status.HTTP_201_CREATED,
)
def book_cooking_service(
        *,
        db: Session = Depends(get_db),
        service_id: UUID,
        booking_in: CookingBookingCreateRequest,
        current_user: User = Depends(require_customer),
) -> dict:
    """
    Book a cooking service.

    BLUEPRINT v2.0 COMPLIANCE:
    - Platform fee ₦100 on bookings
    """
    event_location = None
    if booking_in.event_location:
        event_location = (
            booking_in.event_location.latitude,
            booking_in.event_location.longitude,
        )

    booking = food_service.create_cooking_booking(
        db,
        current_user=current_user,
        service_id=service_id,
        event_date=booking_in.event_date,
        event_time=booking_in.event_time,
        number_of_guests=booking_in.number_of_guests,
        event_address=booking_in.event_address,
        event_location=event_location,
        menu_requirements=booking_in.menu_requirements,
        dietary_restrictions=booking_in.dietary_restrictions,
        special_requests=booking_in.special_requests,
    )
    return {"success": True, "data": booking}


# ============================================================
# ANALYTICS (BUSINESS)
# ============================================================

@router.get("/my/analytics", response_model=SuccessResponse[dict])
def get_food_analytics(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business),
        period: str = Query("30d", pattern="^(7d|30d|90d)$"),
) -> dict:
    """
    Food-specific revenue analytics.
    Flutter Dashboard: food_analytics_screen
    """
    from datetime import timedelta
    from sqlalchemy import func
    from app.models.food_model import FoodOrderItem

    business = _get_business(db, current_user.id)
    restaurant = restaurant_crud.get_by_business_id(db, business_id=business.id)
    if not restaurant:
        raise NotFoundException("Restaurant")

    days = int(period.replace("d", ""))
    since = datetime.utcnow() - timedelta(days=days)

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

    # Order type breakdown
    order_type_breakdown = (
        db.query(
            FoodOrder.order_type,
            func.count(FoodOrder.id).label("count"),
        )
        .filter(
            FoodOrder.restaurant_id == restaurant.id,
            FoodOrder.created_at >= since,
        )
        .group_by(FoodOrder.order_type)
        .all()
    )

    return {
        "success": True,
        "data": {
            "revenue_chart": [
                {"date": str(r.date), "amount": float(r.amount)}
                for r in daily_revenue
            ],
            "top_items": [
                {"name": r.item_name, "orders": r.total_orders}
                for r in top_items_raw
            ],
            "order_type_breakdown": [
                {"type": r.order_type, "count": r.count}
                for r in order_type_breakdown
            ],
        },
    }