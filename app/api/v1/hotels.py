"""
app/api/v1/hotels.py

Hotels API routes with integrated payment processing.

Per Blueprint Section 6.1:
- Radius-based search (no LGA dependency — HARD RULE)
- Real-time availability checking
- Instant booking with ₦100 platform fee
- Wallet payment with PIN verification
- Instant refunds on cancellation

BUG FIXES IN THIS VERSION:
────────────────────────────
BUG-02 FIX (hotels.py — AttributeError: User has no attribute 'user_type'):
  get_booking_services() accessed current_user.user_type on lines 974 and 977.
  The User model (and Blueprint DB schema) uses the column 'role', not
  'user_type'.  Accessing a non-existent attribute raised AttributeError on
  every call to GET /bookings/{booking_id}/services.
  Fix: changed to current_user.role throughout.

BUG-03 FIX (hotels.py — 'local_government' LGA field in get_hotel_details):
  get_hotel_details() projected "local_government": biz.local_government into
  the response dict.  Blueprint Section 2.2 HARD RULE: "No LGA column in any
  table.  Remove immediately if discovered in legacy code."
  Fix: field removed from the projection dict.

BUG-08 FIX (hotels.py — rooms hardcoded to 1 in search route):
  The POST /search route called hotel_service.search_hotels(... rooms=1 ...)
  unconditionally.  HotelSearchFilters had no number_of_rooms field (fixed in
  hotels_schema.py).  Now threads search_params.number_of_rooms through so
  availability filtering respects the caller's actual room requirement.

BUG-10 FIX (hotels.py — dead code _sanitize_add_ons):
  _sanitize_add_ons() was defined but never called from the router.
  Sanitization is handled inside hotel_service.py (after BUG-01 fix there).
  Dead code in a payments module is a maintenance risk; removed.

BUG-11 FIX (hotels.py — PUT /my/hotel and PATCH /my/hotel use HotelCreateRequest):
  Both update endpoints used HotelCreateRequest which has total_rooms as a
  required field.  This forced every PATCH caller to always supply total_rooms,
  making partial updates impossible.  Both endpoints now use HotelUpdateRequest
  (all fields Optional), defined in hotels_schema.py.

BUG-12 FIX (hotels.py — BookingStatusEnum compared to string literals):
  create_service_request() used:
      if booking.status not in ["confirmed", "checked_in"]:
  booking.status is a BookingStatusEnum instance.  This happens to work
  because BookingStatusEnum extends str, but it is fragile — any refactor of
  the enum values would silently break the comparison without a test failure.
  Fix: use the enum members directly.

ORIGINAL FIXES (carried forward from prior version):
1. GET / and POST /search: hotel_service returns List[Dict]; no normalisation helper needed.
2. GET /{hotel_id}: ORM objects manually projected to plain dict.
3. POST /{hotel_id}/room-types: Postman body field corrected.
4. POST /{hotel_id}/bookings: customer_transaction encoded via jsonable_encoder.
5. POST /bookings/{booking_id}/cancel: booking encoded the same way.
6. create_hotel business.category enum comparison extracts .value before comparing.
"""
from fastapi import APIRouter, Depends, Query, status as http_status
from fastapi.encoders import jsonable_encoder
from geoalchemy2.elements import WKBElement
from geoalchemy2.shape import to_shape
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import joinedload as _jl
from typing import List, Optional, Annotated
from uuid import UUID
from datetime import date, datetime, timezone
from decimal import Decimal

from app.core.database import get_async_db
from app.dependencies import (
    get_current_active_user,
    require_customer,
    require_business,
    get_pagination_params,
    get_current_user_optional,
)
from app.schemas.common_schema import SuccessResponse
from app.schemas.hotels_schema import (
    HotelCreateRequest,
    HotelUpdateRequest,       # BUG-11 FIX: imported
    HotelResponse,
    RoomTypeCreateRequest,
    RoomTypeResponse,
    RoomTypeUpdateRequest,
    BookingSearchRequest,
    BookingCreateRequest,
    BookingResponse,
    BookingListResponse,
    ServiceRequestCreate,
    ServiceRequestResponse,
    HotelSearchFilters,
    BookingPaymentResponse,
)
from app.services.hotel_service import hotel_service
from app.crud.hotels_crud import hotel_crud, room_type_crud, hotel_booking_crud
from app.crud.business_crud import business_crud
from app.models.user_model import User
from app.models.hotels_model import (
    # BUG-13 (model-level): HotelInStayRequest replaces the old HotelService
    # alias.  The import alias "as HotelServiceModel" is no longer needed —
    # the model was renamed to HotelInStayRequest in hotels_model.py.
    HotelInStayRequest,
    HotelBooking,
    RoomType,
    BookingStatusEnum,
)
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)

# PostGIS WKBElement → {latitude, longitude} safety encoder.
# hotel_service already projects lat/lng as plain floats, so this is only a
# safety net for any ORM object that slips through (e.g. a BookingResponse
# that loads the hotel relationship).
_GEO_ENCODER = {
    WKBElement: lambda v: {"latitude": to_shape(v).y, "longitude": to_shape(v).x},
    Decimal: float,
}

# BUG-10 FIX: Removed dead _sanitize_add_ons() function.
# It was defined here but never called from the router.  Sanitization of
# add_ons (Pydantic→dict, Decimal→float) is handled inside hotel_service.py
# after the BUG-01 fix there.  Dead code in a payments module is removed.

router = APIRouter()


def _booking_to_dict(b) -> dict:
    """Serialize HotelBooking ORM → plain dict safe for jsonable_encoder."""
    return {
        "id": b.id,
        "hotel_id": b.hotel_id,
        "room_type_id": b.room_type_id,
        "customer_id": b.customer_id,
        "check_in_date": b.check_in_date,
        "check_out_date": b.check_out_date,
        "number_of_rooms": b.number_of_rooms,
        "number_of_guests": b.number_of_guests,
        "base_price": float(b.base_price),
        "add_ons_price": float(b.add_ons_price),
        "total_price": float(b.total_price),
        "add_ons": b.add_ons or [],
        "special_requests": b.special_requests,
        "status": b.status,
        "payment_status": b.payment_status,
        "check_in_form_completed": b.check_in_form_completed,
        "id_uploaded": b.id_uploaded,
        "created_at": b.created_at,
    }


# ============================================
# HOTEL SEARCH & DISCOVERY (PUBLIC)
# ============================================

@router.get("/", response_model=SuccessResponse[List[dict]])
async def list_hotels(
    *,
    db: AsyncSession = Depends(get_async_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    latitude: Optional[float] = Query(None, description="User's latitude"),
    longitude: Optional[float] = Query(None, description="User's longitude"),
    radius_km: float = Query(5.0, ge=1.0, le=50.0, description="Search radius in km"),
    star_rating: Optional[int] = Query(None, ge=1, le=5),
    current_user: Annotated[Optional[User], Depends(get_current_user_optional)] = None,
) -> dict:
    """
    List active hotels using radius-based search.

    Per Blueprint Section 4: Location is radius-based (default 5 km).
    Results ranked by subscription tier (Enterprise > Pro > Starter > Free).

    - Public endpoint (no auth required)
    - Requires latitude + longitude for location filtering

    hotel_service.search_hotels() returns List[Dict] — plain Python dicts
    with all ORM objects already projected.  Pass directly to jsonable_encoder.
    """
    location = None
    if latitude is not None and longitude is not None:
        location = (latitude, longitude)

    results = await hotel_service.search_hotels(
        db,
        location=location,
        radius_km=radius_km,
        check_in=None,
        check_out=None,
        guests=1,
        rooms=1,
        star_rating=star_rating,
        facilities=None,
        min_price=None,
        max_price=None,
        skip=skip,
        limit=limit,
    )
    return {"success": True, "data": jsonable_encoder(results, custom_encoder=_GEO_ENCODER)}


@router.post("/search", response_model=SuccessResponse[List[dict]])
async def search_hotels(
    *,
    db: AsyncSession = Depends(get_async_db),
    search_params: HotelSearchFilters,
    pagination: dict = Depends(get_pagination_params),
    current_user: Annotated[Optional[User], Depends(get_current_user_optional)] = None,
) -> dict:
    """
    Search hotels with full filter set.

    Per Blueprint: Radius-based search using lat/lng coordinates.
    Public endpoint (no auth required).

    BUG-08 FIX: search_params.number_of_rooms is now threaded through to the
    service call.  Previously rooms=1 was hardcoded, so availability checks
    always assumed 1 room regardless of how many the caller wanted.
    """
    location = None
    if search_params.location:
        location = (search_params.location.latitude, search_params.location.longitude)

    results = await hotel_service.search_hotels(
        db,
        location=location,
        radius_km=search_params.radius_km or 5.0,
        check_in=search_params.check_in_date,
        check_out=search_params.check_out_date,
        guests=search_params.guests or 1,
        # BUG-08 FIX: was hardcoded to 1 — now uses the caller's actual value.
        rooms=search_params.number_of_rooms or 1,
        star_rating=search_params.star_rating,
        facilities=search_params.facilities,
        min_price=search_params.min_price,
        max_price=search_params.max_price,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": jsonable_encoder(results, custom_encoder=_GEO_ENCODER)}


@router.get("/{hotel_id}", response_model=SuccessResponse[dict])
async def get_hotel_details(
    *,
    db: AsyncSession = Depends(get_async_db),
    hotel_id: UUID,
) -> dict:
    """
    Get hotel details with room types and inherited business info — public.

    Business fields (name, address, phone, etc.) are surfaced here because
    the Hotel model stores only hotel-specific data.  The Business profile
    (set during onboarding) is the source of truth for identity / location
    per Blueprint §2.1.

    BUG-03 FIX: 'local_government' removed from the projection dict.
    Blueprint Section 2.2 HARD RULE: no LGA field anywhere in the codebase.
    """
    hotel = await hotel_crud.get_with_room_types(db, hotel_id=hotel_id)
    if not hotel:
        raise NotFoundException("Hotel")

    biz = hotel.business
    business_dict = None
    if biz:
        business_dict = {
            "id": biz.id,
            "business_name": biz.business_name,
            "category": biz.category,
            "subcategory": biz.subcategory,
            "description": biz.description,
            "address": biz.registered_address,
            "city": biz.city,
            # BUG-03 FIX: "local_government" removed — LGA HARD RULE violation.
            "state": biz.state,
            "latitude": biz.latitude,
            "longitude": biz.longitude,
            "business_phone": biz.business_phone,
            "business_email": biz.business_email,
            "website": biz.website,
            "instagram": biz.instagram,
            "whatsapp": biz.whatsapp,
            "logo": biz.logo,
            "banner_image": biz.banner_image,
            "average_rating": float(biz.average_rating) if biz.average_rating else 0.0,
            "total_reviews": biz.total_reviews,
            "verification_badge": biz.verification_badge,
            "subscription_tier": biz.subscription_tier,
            "is_verified": biz.is_verified,
            "is_featured": biz.is_featured,
        }

    room_types_list = [
        {
            "id": rt.id,
            "hotel_id": rt.hotel_id,
            "name": rt.name,
            "description": rt.description,
            "bed_configuration": rt.bed_configuration,
            "max_occupancy": rt.max_occupancy,
            "size_sqm": float(rt.size_sqm) if rt.size_sqm else None,
            "floor_range": rt.floor_range,
            "view_type": rt.view_type,
            "amenities": rt.amenities or [],
            "base_price_per_night": float(rt.base_price_per_night),
            "images": rt.images or [],
            "total_rooms": rt.total_rooms,
            "created_at": rt.created_at,
        }
        for rt in (hotel.room_types or [])
    ]

    data = {
        "id": hotel.id,
        "business_id": hotel.business_id,
        "star_rating": hotel.star_rating,
        "total_rooms": hotel.total_rooms,
        "check_in_time": hotel.check_in_time,
        "check_out_time": hotel.check_out_time,
        "facilities": hotel.facilities or [],
        "policies": hotel.policies,
        "cancellation_policy": hotel.cancellation_policy,
        "created_at": hotel.created_at,
        "business": business_dict,
        "room_types": room_types_list,
    }

    return {"success": True, "data": jsonable_encoder(data, custom_encoder=_GEO_ENCODER)}


@router.post("/{hotel_id}/check-availability", response_model=SuccessResponse[List[RoomTypeResponse]])
async def check_room_availability(
    *,
    db: AsyncSession = Depends(get_async_db),
    hotel_id: UUID,
    search_data: BookingSearchRequest,
) -> dict:
    """Check room availability for specific dates."""
    hotel = await hotel_crud.get(db, id=hotel_id)
    if not hotel:
        raise NotFoundException("Hotel")

    available_rooms = await room_type_crud.get_available_room_types(
        db,
        hotel_id=hotel_id,
        check_in=search_data.check_in_date,
        check_out=search_data.check_out_date,
        number_of_rooms=search_data.number_of_rooms,
        number_of_guests=search_data.number_of_guests,
    )
    return {"success": True, "data": available_rooms}


# ============================================
# HOTEL MANAGEMENT (BUSINESS ONLY)
# ============================================

@router.post("/", response_model=SuccessResponse[HotelResponse], status_code=http_status.HTTP_201_CREATED)
async def create_hotel(
    *,
    db: AsyncSession = Depends(get_async_db),
    hotel_in: HotelCreateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    """
    Create hotel profile for the authenticated business.

    Only hotel-specific fields are required (star_rating, total_rooms,
    facilities, policies, check_in/out times).  Name, address, city, state,
    and coordinates are inherited from the business registration record.

    Restricted to businesses in the 'lodges' category.
    """
    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    # category may be a BusinessCategoryEnum instance or a plain string
    biz_category = (
        business.category.value
        if hasattr(business.category, "value")
        else str(business.category)
    ).lower()

    if biz_category != "lodges":
        raise ValidationException("Only lodges-category businesses can create hotels")

    existing_hotel = await hotel_crud.get_by_business_id(db, business_id=business.id)
    if existing_hotel:
        raise ValidationException("Hotel already exists for this business")

    hotel_data = hotel_in.model_dump()
    hotel_data["business_id"] = business.id

    hotel = await hotel_crud.create_from_dict(db, obj_in=hotel_data)
    await db.commit()
    await db.refresh(hotel)
    return {"success": True, "data": hotel}


@router.get("/my/hotel", response_model=SuccessResponse[HotelResponse])
async def get_my_hotel(
    *,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_business),
) -> dict:
    """Get the authenticated business's hotel profile."""
    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    hotel = await hotel_crud.get_by_business_id(db, business_id=business.id)
    if not hotel:
        raise NotFoundException("Hotel")

    return {"success": True, "data": hotel}


@router.put("/my/hotel", response_model=SuccessResponse[HotelResponse])
async def update_my_hotel(
    *,
    db: AsyncSession = Depends(get_async_db),
    update_in: HotelUpdateRequest,   # BUG-11 FIX: was HotelCreateRequest
    current_user: User = Depends(require_business),
) -> dict:
    """
    Update the authenticated business's hotel profile.

    BUG-11 FIX: Changed from HotelCreateRequest to HotelUpdateRequest.
    HotelCreateRequest has total_rooms as a required field, which forced
    callers to always supply it even for an update that only changes
    star_rating or policies.  HotelUpdateRequest makes all fields Optional.

    Updatable: star_rating, total_rooms, check_in_time, check_out_time,
    facilities, policies, cancellation_policy.
    """
    from sqlalchemy.orm.attributes import flag_modified

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    hotel = await hotel_crud.get_by_business_id(db, business_id=business.id)
    if not hotel:
        raise NotFoundException("Hotel")

    # exclude_unset=True: only update fields the caller explicitly sent.
    update_data = update_in.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(hotel, field, value)
        # JSONB / list columns (e.g. facilities) are mutable types —
        # SQLAlchemy cannot detect in-place changes via setattr alone.
        if isinstance(value, (list, dict)):
            flag_modified(hotel, field)

    db.add(hotel)
    await db.commit()
    await db.refresh(hotel)
    return {"success": True, "data": hotel}


@router.patch("/my/hotel", response_model=SuccessResponse[HotelResponse])
async def patch_my_hotel(
    *,
    db: AsyncSession = Depends(get_async_db),
    update_in: HotelUpdateRequest,   # BUG-11 FIX: was HotelCreateRequest
    current_user: User = Depends(require_business),
) -> dict:
    """
    Partially update the authenticated business's hotel profile.

    BUG-11 FIX: Changed from HotelCreateRequest to HotelUpdateRequest.
    Only the fields included in the request body are applied; omitted fields
    are left unchanged.  This is the PATCH semantic.

    Example — update star_rating only:
        PATCH /api/v1/hotels/my/hotel
        {"star_rating": 5}
    """
    from sqlalchemy.orm.attributes import flag_modified

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    hotel = await hotel_crud.get_by_business_id(db, business_id=business.id)
    if not hotel:
        raise NotFoundException("Hotel")

    update_data = update_in.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(hotel, field, value)
        if isinstance(value, (list, dict)):
            flag_modified(hotel, field)

    db.add(hotel)
    await db.commit()
    await db.refresh(hotel)
    return {"success": True, "data": hotel}


# ============================================
# ROOM TYPE MANAGEMENT (BUSINESS ONLY)
# ============================================

@router.post(
    "/{hotel_id}/room-types",
    response_model=SuccessResponse[RoomTypeResponse],
    status_code=http_status.HTTP_201_CREATED,
)
async def create_room_type(
    *,
    db: AsyncSession = Depends(get_async_db),
    hotel_id: UUID,
    room_type_in: RoomTypeCreateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    """
    Create a new room type for the hotel.

    Required body fields: name, max_occupancy, base_price_per_night, total_rooms.
    Optional: description, bed_configuration, size_sqm, floor_range,
              view_type, amenities, images.
    """
    hotel = await hotel_crud.get(db, id=hotel_id)
    if not hotel:
        raise NotFoundException("Hotel")

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or hotel.business_id != business.id:
        raise PermissionDeniedException("You don't own this hotel")

    room_type_data = room_type_in.model_dump()
    room_type_data["hotel_id"] = hotel_id
    room_type = await room_type_crud.create_from_dict(db, obj_in=room_type_data)
    await db.commit()
    await db.refresh(room_type)
    return {"success": True, "data": room_type}


@router.get("/{hotel_id}/room-types", response_model=SuccessResponse[List[RoomTypeResponse]])
async def get_room_types(
    *,
    db: AsyncSession = Depends(get_async_db),
    hotel_id: UUID,
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    """Get all room types for a hotel — public."""
    hotel = await hotel_crud.get(db, id=hotel_id)
    if not hotel:
        raise NotFoundException("Hotel")

    room_types = await room_type_crud.get_by_hotel(
        db,
        hotel_id=hotel_id,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": room_types}


@router.put("/{hotel_id}/room-types/{room_type_id}", response_model=SuccessResponse[RoomTypeResponse])
async def update_room_type(
    *,
    db: AsyncSession = Depends(get_async_db),
    hotel_id: UUID,
    room_type_id: UUID,
    room_type_in: RoomTypeUpdateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    """Update room type details — hotel owner only."""
    hotel = await hotel_crud.get(db, id=hotel_id)
    if not hotel:
        raise NotFoundException("Hotel")

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or hotel.business_id != business.id:
        raise PermissionDeniedException("You don't own this hotel")

    room_type = await room_type_crud.get(db, id=room_type_id)
    if not room_type or room_type.hotel_id != hotel_id:
        raise NotFoundException("Room type")

    updated = await room_type_crud.update(db, db_obj=room_type, obj_in=room_type_in)
    await db.commit()
    await db.refresh(updated)
    return {"success": True, "data": updated}


@router.delete("/{hotel_id}/room-types/{room_type_id}", response_model=SuccessResponse[dict])
async def delete_room_type(
    *,
    db: AsyncSession = Depends(get_async_db),
    hotel_id: UUID,
    room_type_id: UUID,
    current_user: User = Depends(require_business),
) -> dict:
    """
    Delete a room type — hotel owner only.

    Blocked if any active bookings (PENDING, CONFIRMED, or CHECKED_IN) exist
    for this room type.  Cancel or complete those bookings first.
    """
    hotel = await hotel_crud.get(db, id=hotel_id)
    if not hotel:
        raise NotFoundException("Hotel")

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or hotel.business_id != business.id:
        raise PermissionDeniedException("You don't own this hotel")

    room_type = await room_type_crud.get(db, id=room_type_id)
    if not room_type or room_type.hotel_id != hotel_id:
        raise NotFoundException("Room type")

    active_count_result = await db.execute(
        select(func.count(HotelBooking.id)).where(
            HotelBooking.room_type_id == room_type_id,
            HotelBooking.status.in_([
                BookingStatusEnum.PENDING,
                BookingStatusEnum.CONFIRMED,
                BookingStatusEnum.CHECKED_IN,
            ])
        )
    )
    active_count = active_count_result.scalar() or 0
    if active_count > 0:
        raise ValidationException(
            f"Cannot delete room type: {active_count} active booking(s) exist. "
            "Cancel or complete those bookings first."
        )

    await room_type_crud.delete(db, id=room_type_id)
    await db.commit()
    return {"success": True, "data": {"message": "Room type deleted successfully"}}


# ============================================
# BOOKING MANAGEMENT (CUSTOMER)
# ============================================

@router.post(
    "/{hotel_id}/bookings",
    response_model=SuccessResponse[dict],
    status_code=http_status.HTTP_201_CREATED,
)
async def create_booking(
    *,
    db: AsyncSession = Depends(get_async_db),
    hotel_id: UUID,
    booking_in: BookingCreateRequest,
    current_user: User = Depends(require_customer),
) -> dict:
    """
    Create a hotel booking with instant payment processing.

    Per Blueprint Section 6.1:
    - ₦100 platform fee (deducted before business credit)
    - Wallet payment (PIN verified at frontend before this call)
    - Instant confirmation on success
    - Atomic transaction (booking + payment together)

    Returns booking details + transaction breakdown.
    """
    hotel = await hotel_crud.get(db, id=hotel_id)
    if not hotel:
        raise NotFoundException("Hotel")

    booking, customer_txn, business_txn, revenue = await hotel_service.create_booking_and_pay(
        db,
        customer_id=current_user.id,
        hotel_id=hotel_id,
        room_type_id=booking_in.room_type_id,
        check_in=booking_in.check_in_date,
        check_out=booking_in.check_out_date,
        number_of_rooms=booking_in.number_of_rooms,
        number_of_guests=booking_in.number_of_guests,
        add_ons=booking_in.add_ons,
        special_requests=booking_in.special_requests,
    )

    data = {
        "booking": jsonable_encoder(booking, custom_encoder=_GEO_ENCODER),
        "customer_transaction": jsonable_encoder(customer_txn, custom_encoder=_GEO_ENCODER),
        "business_revenue": float(business_txn.amount),
        "platform_fee": float(revenue.platform_fee),
        "total_paid": float(customer_txn.amount),
    }

    return {"success": True, "data": data}


@router.get("/bookings/my", response_model=SuccessResponse[List[BookingListResponse]])
async def get_my_bookings(
    *,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_customer),
    pagination: dict = Depends(get_pagination_params),
    booking_status: Optional[str] = Query(None, description="Filter by booking status", alias="status"),
) -> dict:
    """Get the authenticated customer's hotel bookings."""
    bookings = await hotel_booking_crud.get_customer_bookings(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
        status=booking_status,
    )

    booking_list = []
    for b in bookings:
        # get_with_room_types eagerly loads hotel.business via joinedload —
        # avoids MissingGreenlet that occurs when lazy-loading in async context.
        hotel = await hotel_crud.get_with_room_types(db, hotel_id=b.hotel_id)
        room_type = await room_type_crud.get(db, id=b.room_type_id)

        hotel_name = "Unknown"
        if hotel and hotel.business:
            hotel_name = hotel.business.business_name

        booking_list.append({
            "id": b.id,
            "hotel_name": hotel_name,
            "room_type_name": room_type.name if room_type else "Unknown",
            "check_in_date": b.check_in_date,
            "check_out_date": b.check_out_date,
            "number_of_rooms": b.number_of_rooms,
            "total_price": b.total_price,
            "status": b.status,
            "payment_status": b.payment_status,
            "created_at": b.created_at,
        })

    return {"success": True, "data": booking_list}


@router.get("/bookings/{booking_id}", response_model=SuccessResponse[dict])
async def get_booking_details(
    *,
    db: AsyncSession = Depends(get_async_db),
    booking_id: UUID,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """
    Get full booking details — customer (own booking) or hotel owner.

    response_model is dict (not BookingResponse) to avoid two issues:
    1. ResponseValidationError — BookingResponse.room_type requires the
       relationship to be eagerly loaded; a plain .get() has no joinedload.
    2. add_ons is JSONB (list of plain dicts) but BookingResponse expects
       List[AddOnItem] — validation fails on existing DB records where dict
       keys may not exactly match the Pydantic model.
    """
    result = await db.execute(
        select(HotelBooking)
        .options(_jl(HotelBooking.room_type))
        .where(HotelBooking.id == booking_id)
    )
    booking = result.scalars().first()
    if not booking:
        raise NotFoundException("Booking")

    # BUG-02 FIX: current_user.role — NOT current_user.user_type.
    # The User model uses the 'role' column (Blueprint DB schema, Section 14).
    # user_type does not exist on the User ORM object; accessing it raises
    # AttributeError on every call to this endpoint.
    if current_user.role == "customer":
        if booking.customer_id != current_user.id:
            raise PermissionDeniedException()
    elif current_user.role == "business":
        business = await business_crud.get_by_user_id(db, user_id=current_user.id)
        hotel = await hotel_crud.get(db, id=booking.hotel_id)
        if not business or hotel.business_id != business.id:
            raise PermissionDeniedException()

    rt = booking.room_type
    room_type_dict = None
    if rt:
        room_type_dict = {
            "id": rt.id,
            "hotel_id": rt.hotel_id,
            "name": rt.name,
            "description": rt.description,
            "bed_configuration": rt.bed_configuration,
            "max_occupancy": rt.max_occupancy,
            "size_sqm": float(rt.size_sqm) if rt.size_sqm else None,
            "floor_range": rt.floor_range,
            "view_type": rt.view_type,
            "amenities": rt.amenities or [],
            "base_price_per_night": float(rt.base_price_per_night),
            "images": rt.images or [],
            "total_rooms": rt.total_rooms,
            "created_at": rt.created_at,
        }

    data = {
        "id": booking.id,
        "hotel_id": booking.hotel_id,
        "room_type_id": booking.room_type_id,
        "customer_id": booking.customer_id,
        "check_in_date": booking.check_in_date,
        "check_out_date": booking.check_out_date,
        "number_of_rooms": booking.number_of_rooms,
        "number_of_guests": booking.number_of_guests,
        "base_price": float(booking.base_price),
        "add_ons_price": float(booking.add_ons_price),
        "total_price": float(booking.total_price),
        "add_ons": booking.add_ons or [],
        "special_requests": booking.special_requests,
        "status": booking.status,
        "payment_status": booking.payment_status,
        "check_in_form_completed": booking.check_in_form_completed,
        "id_uploaded": booking.id_uploaded,
        "created_at": booking.created_at,
        "room_type": room_type_dict,
    }

    return {"success": True, "data": jsonable_encoder(data, custom_encoder=_GEO_ENCODER)}


@router.post("/bookings/{booking_id}/cancel", response_model=SuccessResponse[dict])
async def cancel_booking(
    *,
    db: AsyncSession = Depends(get_async_db),
    booking_id: UUID,
    reason: Optional[str] = None,
    current_user: User = Depends(require_customer),
) -> dict:
    """
    Cancel a booking with instant refund to wallet.

    Per Blueprint Section 5.1:
    - Refunds go back to wallet within 24 hours of cancellation approval
    - Platform fee: refundable only if cancellation is due to verified
      platform error
    """
    booking, customer_refund, business_debit, reversed_revenue = (
        await hotel_service.cancel_booking_and_refund(
            db,
            booking_id=booking_id,
            customer_id=current_user.id,
            reason=reason,
        )
    )

    data = {
        "booking": jsonable_encoder(booking, custom_encoder=_GEO_ENCODER),
        "refund_amount": float(customer_refund.amount),
        "message": "Booking cancelled and refund processed to your wallet",
    }

    return {"success": True, "data": data}


# ============================================
# BUSINESS BOOKING MANAGEMENT
# ============================================

@router.get("/my/bookings", response_model=SuccessResponse[List[dict]])
async def get_hotel_bookings(
    *,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_business),
    pagination: dict = Depends(get_pagination_params),
    booking_status: Optional[str] = Query(None, alias="status"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
) -> dict:
    """Get all bookings for the authenticated business's hotel."""
    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    hotel = await hotel_crud.get_by_business_id(db, business_id=business.id)
    if not hotel:
        raise NotFoundException("Hotel")

    bookings = await hotel_booking_crud.get_hotel_bookings(
        db,
        hotel_id=hotel.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
        status=booking_status,
        date_from=date_from,
        date_to=date_to,
    )
    return {
        "success": True,
        "data": jsonable_encoder(
            [_booking_to_dict(b) for b in bookings],
            custom_encoder=_GEO_ENCODER
        )
    }


@router.post("/bookings/{booking_id}/confirm", response_model=SuccessResponse[dict])
async def confirm_booking(
    *,
    db: AsyncSession = Depends(get_async_db),
    booking_id: UUID,
    current_user: User = Depends(require_business),
) -> dict:
    """Confirm a booking — hotel owner only."""
    booking = await hotel_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    hotel = await hotel_crud.get(db, id=booking.hotel_id)
    if not business or hotel.business_id != business.id:
        raise PermissionDeniedException()

    booking = await hotel_booking_crud.confirm_booking(db, booking_id=booking_id)
    await db.commit()
    await db.refresh(booking)
    return {
        "success": True,
        "data": jsonable_encoder(_booking_to_dict(booking), custom_encoder=_GEO_ENCODER)
    }


# ============================================
# IN-STAY SERVICE REQUESTS
# ============================================

@router.post(
    "/bookings/{booking_id}/services",
    response_model=SuccessResponse[ServiceRequestResponse],
    status_code=http_status.HTTP_201_CREATED,
)
async def create_service_request(
    *,
    db: AsyncSession = Depends(get_async_db),
    booking_id: UUID,
    service_in: ServiceRequestCreate,
    current_user: User = Depends(require_customer),
) -> dict:
    """Create an in-stay service request (room service, housekeeping, etc.)."""
    booking = await hotel_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    if booking.customer_id != current_user.id:
        raise PermissionDeniedException()

    # BUG-12 FIX: Use BookingStatusEnum members instead of string literals.
    # booking.status is a BookingStatusEnum instance.  Comparing it to plain
    # strings ("confirmed", "checked_in") works only because BookingStatusEnum
    # extends str — a fragile reliance.  Using enum members is explicit,
    # refactor-safe, and correctly expresses intent.
    if booking.status not in [BookingStatusEnum.CONFIRMED, BookingStatusEnum.CHECKED_IN]:
        raise ValidationException(
            "Can only request services for confirmed or checked-in bookings"
        )

    service = HotelInStayRequest(
        booking_id=booking_id,
        service_type=service_in.service_type,
        description=service_in.description,
        requested_at=datetime.now(timezone.utc),
    )
    db.add(service)
    await db.commit()
    await db.refresh(service)
    return {"success": True, "data": service}


@router.get(
    "/bookings/{booking_id}/services",
    response_model=SuccessResponse[List[ServiceRequestResponse]],
)
async def get_booking_services(
    *,
    db: AsyncSession = Depends(get_async_db),
    booking_id: UUID,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Get service requests for a booking."""
    booking = await hotel_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    # BUG-02 FIX: current_user.role — NOT current_user.user_type.
    # The User model column is 'role' (Blueprint DB schema, Section 14).
    # 'user_type' does not exist on the ORM object; accessing it raised
    # AttributeError on every call to this endpoint.
    if current_user.role == "customer":
        if booking.customer_id != current_user.id:
            raise PermissionDeniedException()
    elif current_user.role == "business":
        business = await business_crud.get_by_user_id(db, user_id=current_user.id)
        hotel = await hotel_crud.get(db, id=booking.hotel_id)
        if not business or hotel.business_id != business.id:
            raise PermissionDeniedException()

    result = await db.execute(
        select(HotelInStayRequest).where(HotelInStayRequest.booking_id == booking_id)
    )
    services = result.scalars().all()
    return {"success": True, "data": services}