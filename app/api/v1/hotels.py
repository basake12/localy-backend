from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional, Annotated
from uuid import UUID
from datetime import date

from app.core.database import get_db
from app.dependencies import (
    get_current_active_user,
    require_customer,
    require_business,
    get_pagination_params,
    get_current_user_optional
)
from app.schemas.common_schema import SuccessResponse, PaginatedResponse
from app.schemas.hotels_schema import (
    HotelCreateRequest,
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
    HotelSearchFilters
)
from app.services.hotel_service import hotel_service
from app.crud.hotels_crud import hotel_crud, room_type_crud, hotel_booking_crud
from app.crud.business_crud import business_crud
from app.models.user_model import User
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException
)
from app.models.hotels_model import HotelService
from datetime import datetime

router = APIRouter()


# ============================================
# HOTEL SEARCH & DISCOVERY (PUBLIC)
# ============================================

@router.get("/", response_model=SuccessResponse[List[dict]])
def list_hotels(
        *,
        db: Session = Depends(get_db),
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        star_rating: Optional[int] = Query(None, ge=1, le=5),
        current_user: Annotated[Optional[User], Depends(get_current_user_optional)] = None
) -> dict:
    """
    List all active hotels with optional filters

    - Public endpoint (no auth required)
    - Filter by star rating
    - Paginated results
    """
    results = hotel_service.search_hotels(
        db,
        location=None,
        radius_km=None,
        check_in=None,
        check_out=None,
        guests=None,
        star_rating=star_rating,
        facilities=None,
        min_price=None,
        max_price=None,
        skip=skip,
        limit=limit
    )

    return {
        "success": True,
        "data": results
    }


@router.post("/search", response_model=SuccessResponse[List[dict]])
def search_hotels(
        *,
        db: Session = Depends(get_db),
        search_params: HotelSearchFilters,
        pagination: dict = Depends(get_pagination_params),
        current_user: Annotated[Optional[User], Depends(get_current_user_optional)] = None
) -> dict:
    """
    Search hotels with filters

    - Location-based search with radius
    - Filter by star rating, facilities, price
    - Check availability for specific dates
    - Public endpoint (no auth required)
    """
    location = None
    if search_params.location:
        location = (
            search_params.location.latitude,
            search_params.location.longitude
        )

    results = hotel_service.search_hotels(
        db,
        location=location,
        radius_km=search_params.radius_km or 10.0,
        check_in=search_params.check_in_date,
        check_out=search_params.check_out_date,
        guests=search_params.guests or 1,
        star_rating=search_params.star_rating,
        facilities=search_params.facilities,
        min_price=search_params.min_price,
        max_price=search_params.max_price,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": results
    }


@router.get("/{hotel_id}", response_model=SuccessResponse[dict])
def get_hotel_details(
        *,
        db: Session = Depends(get_db),
        hotel_id: UUID
) -> dict:
    """
    Get hotel details with room types

    - Public endpoint
    - Returns full hotel information
    """
    hotel = hotel_crud.get_with_room_types(db, hotel_id=hotel_id)
    if not hotel:
        raise NotFoundException("Hotel")

    return {
        "success": True,
        "data": {
            "hotel": hotel,
            "business": hotel.business,
            "room_types": hotel.room_types
        }
    }


@router.post("/{hotel_id}/check-availability", response_model=SuccessResponse[List[RoomTypeResponse]])
def check_room_availability(
        *,
        db: Session = Depends(get_db),
        hotel_id: UUID,
        search_data: BookingSearchRequest
) -> dict:
    """
    Check room availability for specific dates

    - Returns available room types
    - Shows number of available rooms per type
    """
    hotel = hotel_crud.get(db, id=hotel_id)
    if not hotel:
        raise NotFoundException("Hotel")

    available_rooms = room_type_crud.get_available_room_types(
        db,
        hotel_id=hotel_id,
        check_in=search_data.check_in_date,
        check_out=search_data.check_out_date,
        number_of_rooms=search_data.number_of_rooms,
        number_of_guests=search_data.number_of_guests
    )

    return {
        "success": True,
        "data": available_rooms
    }


# ============================================
# HOTEL MANAGEMENT (BUSINESS ONLY)
# ============================================

@router.post("/", response_model=SuccessResponse[HotelResponse], status_code=status.HTTP_201_CREATED)
def create_hotel(
        *,
        db: Session = Depends(get_db),
        hotel_in: HotelCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Create hotel for current business

    - Only for business accounts
    - Business must be in 'lodges' category
    """
    # Get business
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    if business.category != "lodges":
        raise ValidationException("Only lodges category businesses can create hotels")

    # Check if hotel already exists
    existing_hotel = hotel_crud.get_by_business_id(db, business_id=business.id)
    if existing_hotel:
        raise ValidationException("Hotel already exists for this business")

    # Create hotel
    hotel_data = hotel_in.model_dump()
    hotel_data["business_id"] = business.id

    hotel = hotel_crud.create_from_dict(db, obj_in=hotel_data)

    return {
        "success": True,
        "data": hotel
    }


@router.get("/my/hotel", response_model=SuccessResponse[HotelResponse])
def get_my_hotel(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business)
) -> dict:
    """Get current business's hotel"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    hotel = hotel_crud.get_by_business_id(db, business_id=business.id)
    if not hotel:
        raise NotFoundException("Hotel")

    return {
        "success": True,
        "data": hotel
    }


# ============================================
# ROOM TYPE MANAGEMENT (BUSINESS ONLY)
# ============================================

@router.post(
    "/{hotel_id}/room-types",
    response_model=SuccessResponse[RoomTypeResponse],
    status_code=status.HTTP_201_CREATED
)
def create_room_type(
        *,
        db: Session = Depends(get_db),
        hotel_id: UUID,
        room_type_in: RoomTypeCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Create a new room type

    - Only hotel owner can create room types
    """
    # Verify hotel ownership
    hotel = hotel_crud.get(db, id=hotel_id)
    if not hotel:
        raise NotFoundException("Hotel")

    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or hotel.business_id != business.id:
        raise PermissionDeniedException("You don't own this hotel")

    # Create room type
    room_type_data = room_type_in.model_dump()
    room_type_data["hotel_id"] = hotel_id

    room_type = room_type_crud.create_from_dict(db, obj_in=room_type_data)

    return {
        "success": True,
        "data": room_type
    }


@router.get("/{hotel_id}/room-types", response_model=SuccessResponse[List[RoomTypeResponse]])
def get_room_types(
        *,
        db: Session = Depends(get_db),
        hotel_id: UUID,
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get all room types for a hotel"""
    hotel = hotel_crud.get(db, id=hotel_id)
    if not hotel:
        raise NotFoundException("Hotel")

    room_types = room_type_crud.get_by_hotel(
        db,
        hotel_id=hotel_id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": room_types
    }


@router.put(
    "/{hotel_id}/room-types/{room_type_id}",
    response_model=SuccessResponse[RoomTypeResponse]
)
def update_room_type(
        *,
        db: Session = Depends(get_db),
        hotel_id: UUID,
        room_type_id: UUID,
        room_type_in: RoomTypeUpdateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Update room type details"""
    hotel = hotel_crud.get(db, id=hotel_id)
    if not hotel:
        raise NotFoundException("Hotel")

    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or hotel.business_id != business.id:
        raise PermissionDeniedException("You don't own this hotel")

    room_type = room_type_crud.get(db, id=room_type_id)
    if not room_type or room_type.hotel_id != hotel_id:
        raise NotFoundException("Room type")

    updated = room_type_crud.update(db, db_obj=room_type, obj_in=room_type_in)

    return {
        "success": True,
        "data": updated
    }


@router.delete("/{hotel_id}/room-types/{room_type_id}", response_model=SuccessResponse[dict])
def delete_room_type(
        *,
        db: Session = Depends(get_db),
        hotel_id: UUID,
        room_type_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Delete a room type"""
    hotel = hotel_crud.get(db, id=hotel_id)
    if not hotel:
        raise NotFoundException("Hotel")

    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or hotel.business_id != business.id:
        raise PermissionDeniedException("You don't own this hotel")

    room_type = room_type_crud.get(db, id=room_type_id)
    if not room_type or room_type.hotel_id != hotel_id:
        raise NotFoundException("Room type")

    room_type_crud.delete(db, id=room_type_id)

    return {
        "success": True,
        "data": {"message": "Room type deleted successfully"}
    }


# ============================================
# BOOKING MANAGEMENT (CUSTOMER)
# ============================================

@router.post(
    "/{hotel_id}/bookings",
    response_model=SuccessResponse[BookingResponse],
    status_code=status.HTTP_201_CREATED
)
def create_booking(
        *,
        db: Session = Depends(get_db),
        hotel_id: UUID,
        booking_in: BookingCreateRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """Create a hotel booking"""
    hotel = hotel_crud.get(db, id=hotel_id)
    if not hotel:
        raise NotFoundException("Hotel")

    booking = hotel_service.create_booking(
        db,
        customer_id=current_user.id,
        hotel_id=hotel_id,
        room_type_id=booking_in.room_type_id,
        check_in=booking_in.check_in_date,
        check_out=booking_in.check_out_date,
        number_of_rooms=booking_in.number_of_rooms,
        number_of_guests=booking_in.number_of_guests,
        add_ons=booking_in.add_ons,
        special_requests=booking_in.special_requests
    )

    return {
        "success": True,
        "data": booking
    }


@router.get("/bookings/my", response_model=SuccessResponse[List[BookingListResponse]])
def get_my_bookings(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_customer),
        pagination: dict = Depends(get_pagination_params),
        status: Optional[str] = Query(None, description="Filter by status")
) -> dict:
    """Get current customer's bookings"""
    bookings = hotel_booking_crud.get_customer_bookings(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
        status=status
    )

    # Transform to list response
    booking_list = []
    for booking in bookings:
        room_type = room_type_crud.get(db, id=booking.room_type_id)
        hotel = hotel_crud.get(db, id=booking.hotel_id)
        business = business_crud.get(db, id=hotel.business_id)

        booking_list.append({
            "id": booking.id,
            "hotel_name": business.business_name,
            "room_type_name": room_type.name,
            "check_in_date": booking.check_in_date,
            "check_out_date": booking.check_out_date,
            "number_of_rooms": booking.number_of_rooms,
            "total_price": booking.total_price,
            "status": booking.status,
            "payment_status": booking.payment_status,
            "created_at": booking.created_at
        })

    return {
        "success": True,
        "data": booking_list
    }


@router.get("/bookings/{booking_id}", response_model=SuccessResponse[BookingResponse])
def get_booking_details(
        *,
        db: Session = Depends(get_db),
        booking_id: UUID,
        current_user: User = Depends(get_current_active_user)
) -> dict:
    """Get booking details"""
    booking = hotel_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    # Verify permission (customer or hotel owner)
    if current_user.user_type == "customer":
        if booking.customer_id != current_user.id:
            raise PermissionDeniedException()
    elif current_user.user_type == "business":
        business = business_crud.get_by_user_id(db, user_id=current_user.id)
        hotel = hotel_crud.get(db, id=booking.hotel_id)
        if not business or hotel.business_id != business.id:
            raise PermissionDeniedException()

    return {
        "success": True,
        "data": booking
    }


@router.post("/bookings/{booking_id}/cancel", response_model=SuccessResponse[BookingResponse])
def cancel_booking(
        *,
        db: Session = Depends(get_db),
        booking_id: UUID,
        reason: Optional[str] = None,
        current_user: User = Depends(require_customer)
) -> dict:
    """Cancel a booking"""
    booking = hotel_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    # Verify ownership
    if booking.customer_id != current_user.id:
        raise PermissionDeniedException()

    booking = hotel_booking_crud.cancel_booking(
        db,
        booking_id=booking_id,
        reason=reason
    )

    return {
        "success": True,
        "data": booking
    }


# ============================================
# BUSINESS BOOKING MANAGEMENT
# ============================================

@router.get("/my/bookings", response_model=SuccessResponse[List[BookingResponse]])
def get_hotel_bookings(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business),
        pagination: dict = Depends(get_pagination_params),
        status: Optional[str] = Query(None),
        date_from: Optional[date] = Query(None),
        date_to: Optional[date] = Query(None)
) -> dict:
    """Get bookings for current business's hotel"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    hotel = hotel_crud.get_by_business_id(db, business_id=business.id)
    if not hotel:
        raise NotFoundException("Hotel")

    bookings = hotel_booking_crud.get_hotel_bookings(
        db,
        hotel_id=hotel.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
        status=status,
        date_from=date_from,
        date_to=date_to
    )

    return {
        "success": True,
        "data": bookings
    }


@router.post("/bookings/{booking_id}/confirm", response_model=SuccessResponse[BookingResponse])
def confirm_booking(
        *,
        db: Session = Depends(get_db),
        booking_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Confirm a booking (hotel owner)"""
    booking = hotel_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    # Verify ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    hotel = hotel_crud.get(db, id=booking.hotel_id)
    if not business or hotel.business_id != business.id:
        raise PermissionDeniedException()

    booking = hotel_booking_crud.confirm_booking(db, booking_id=booking_id)

    return {
        "success": True,
        "data": booking
    }


# ============================================
# IN-STAY SERVICE REQUESTS
# ============================================

@router.post(
    "/bookings/{booking_id}/services",
    response_model=SuccessResponse[ServiceRequestResponse],
    status_code=status.HTTP_201_CREATED
)
def create_service_request(
        *,
        db: Session = Depends(get_db),
        booking_id: UUID,
        service_in: ServiceRequestCreate,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Create in-stay service request

    - Room service
    - Housekeeping
    - Maintenance
    - Wake-up call
    """

    booking = hotel_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    # Verify ownership
    if booking.customer_id != current_user.id:
        raise PermissionDeniedException()

    # Check booking status
    if booking.status not in ["confirmed", "checked_in"]:
        raise ValidationException("Can only request services for confirmed or checked-in bookings")

    # Create service request
    service = HotelService(
        booking_id=booking_id,
        service_type=service_in.service_type,
        description=service_in.description,
        requested_at=datetime.utcnow()
    )

    db.add(service)
    db.commit()
    db.refresh(service)

    return {
        "success": True,
        "data": service
    }


@router.get(
    "/bookings/{booking_id}/services",
    response_model=SuccessResponse[List[ServiceRequestResponse]]
)
def get_booking_services(
        *,
        db: Session = Depends(get_db),
        booking_id: UUID,
        current_user: User = Depends(get_current_active_user)
) -> dict:
    """Get service requests for a booking"""

    booking = hotel_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    # Verify permission
    if current_user.user_type == "customer":
        if booking.customer_id != current_user.id:
            raise PermissionDeniedException()
    elif current_user.user_type == "business":
        business = business_crud.get_by_user_id(db, user_id=current_user.id)
        hotel = hotel_crud.get(db, id=booking.hotel_id)
        if not business or hotel.business_id != business.id:
            raise PermissionDeniedException()

    services = db.query(HotelService).filter(
        HotelService.booking_id == booking_id
    ).all()

    return {
        "success": True,
        "data": services
    }