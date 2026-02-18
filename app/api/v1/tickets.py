from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
from datetime import datetime, date

from app.core.database import get_db
from app.dependencies import (
    get_current_active_user,
    require_customer,
    require_business,
    get_pagination_params
)
from app.schemas.common import SuccessResponse
from app.schemas.tickets import (
    TicketEventCreateRequest,
    TicketEventResponse,
    TicketEventListResponse,
    TicketTierCreateRequest,
    TicketTierResponse,
    TicketBookingCreateRequest,
    TicketBookingResponse,
    TicketBookingListResponse,
    TicketEventSearchFilters
)
from app.services.ticket_service import ticket_service
from app.crud.tickets import (
    ticket_event_crud,
    ticket_tier_crud,
    ticket_booking_crud
)
from app.crud.business import business_crud
from app.models.user import User
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException
)
from app.models.tickets import TicketBooking
from sqlalchemy import func
from app.models.tickets import EventCategoryEnum

router = APIRouter()


# ============================================
# EVENT SEARCH & DISCOVERY (PUBLIC)
# ============================================

@router.post("/search", response_model=SuccessResponse[List[dict]])
def search_events(
        *,
        db: Session = Depends(get_db),
        search_params: TicketEventSearchFilters,
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """
    Search events and transport tickets

    - Public endpoint
    - Location-based search for events
    - Route-based search for transport
    - Filter by date, category, type
    """
    location = None
    if search_params.location:
        location = (
            search_params.location.latitude,
            search_params.location.longitude
        )

    results = ticket_service.search_events(
        db,
        query_text=search_params.query,
        event_type=search_params.event_type,
        category=search_params.category,
        location=location,
        radius_km=search_params.radius_km or 50.0,
        event_date_from=search_params.event_date_from,
        event_date_to=search_params.event_date_to,
        origin_city=search_params.origin_city,
        destination_city=search_params.destination_city,
        departure_date=search_params.departure_date,
        transport_type=search_params.transport_type,
        available_only=search_params.available_only,
        is_featured=search_params.is_featured,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": results
    }


@router.get("/{event_id}", response_model=SuccessResponse[dict])
def get_event_details(
        *,
        db: Session = Depends(get_db),
        event_id: UUID
) -> dict:
    """
    Get event/transport details

    - Public endpoint
    - Returns event with ticket tiers
    """
    event_data = ticket_service.get_event_details(db, event_id=event_id)

    return {
        "success": True,
        "data": event_data
    }


@router.get("/categories/list", response_model=SuccessResponse[List[str]])
def get_event_categories(
        *,
        db: Session = Depends(get_db)
) -> dict:
    """Get all event categories"""


    categories = [cat.value for cat in EventCategoryEnum]

    return {
        "success": True,
        "data": categories
    }


# ============================================
# EVENT MANAGEMENT (BUSINESS ONLY)
# ============================================

@router.post("/events", response_model=SuccessResponse[TicketEventResponse], status_code=status.HTTP_201_CREATED)
def create_event(
        *,
        db: Session = Depends(get_db),
        event_in: TicketEventCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Create event or transport schedule

    - Only for business accounts
    - Business must be in 'tickets' category
    """
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    if business.category != "tickets":
        raise ValidationException("Only tickets category businesses can create events")

    # Create event
    event_data = event_in.model_dump()
    event_data["business_id"] = business.id
    event_data["available_capacity"] = event_in.total_capacity

    # Handle locations
    if event_in.venue_location:
        from geoalchemy2.elements import WKTElement
        event_data["venue_location"] = WKTElement(
            f"POINT({event_in.venue_location.longitude} {event_in.venue_location.latitude})",
            srid=4326
        )

    if event_in.origin_location:
        from geoalchemy2.elements import WKTElement
        event_data["origin_location"] = WKTElement(
            f"POINT({event_in.origin_location.longitude} {event_in.origin_location.latitude})",
            srid=4326
        )

    if event_in.destination_location:
        from geoalchemy2.elements import WKTElement
        event_data["destination_location"] = WKTElement(
            f"POINT({event_in.destination_location.longitude} {event_in.destination_location.latitude})",
            srid=4326
        )

    event = ticket_event_crud.create_from_dict(db, obj_in=event_data)

    return {
        "success": True,
        "data": event
    }


@router.get("/events/my", response_model=SuccessResponse[List[TicketEventResponse]])
def get_my_events(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business),
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get current business's events"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    events = ticket_event_crud.get_by_business_id(
        db,
        business_id=business.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": events
    }


@router.put("/events/{event_id}", response_model=SuccessResponse[TicketEventResponse])
def update_event(
        *,
        db: Session = Depends(get_db),
        event_id: UUID,
        is_active: Optional[bool] = None,
        status: Optional[str] = None,
        current_user: User = Depends(require_business)
) -> dict:
    """Update event"""
    event = ticket_event_crud.get(db, id=event_id)
    if not event:
        raise NotFoundException("Event")

    # Verify ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    # Update
    update_data = {}
    if is_active is not None:
        update_data["is_active"] = is_active
    if status is not None:
        update_data["status"] = status

    event = ticket_event_crud.update(db, db_obj=event, obj_in=update_data)

    return {
        "success": True,
        "data": event
    }


# ============================================
# TICKET TIER MANAGEMENT (BUSINESS ONLY)
# ============================================

@router.post("/events/{event_id}/tiers", response_model=SuccessResponse[TicketTierResponse],
             status_code=status.HTTP_201_CREATED)
def create_ticket_tier(
        *,
        db: Session = Depends(get_db),
        event_id: UUID,
        tier_in: TicketTierCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Create ticket tier"""
    event = ticket_event_crud.get(db, id=event_id)
    if not event:
        raise NotFoundException("Event")

    # Verify ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    # Create tier
    tier_data = tier_in.model_dump()
    tier_data["event_id"] = event_id
    tier_data["available_quantity"] = tier_in.total_quantity

    tier = ticket_tier_crud.create_from_dict(db, obj_in=tier_data)

    return {
        "success": True,
        "data": tier
    }


@router.get("/events/{event_id}/tiers", response_model=SuccessResponse[List[TicketTierResponse]])
def get_event_tiers(
        *,
        db: Session = Depends(get_db),
        event_id: UUID
) -> dict:
    """Get ticket tiers for event"""
    tiers = ticket_tier_crud.get_by_event(db, event_id=event_id)

    return {
        "success": True,
        "data": tiers
    }


@router.put("/tiers/{tier_id}", response_model=SuccessResponse[TicketTierResponse])
def update_ticket_tier(
        *,
        db: Session = Depends(get_db),
        tier_id: UUID,
        is_active: Optional[bool] = None,
        price: Optional[Decimal] = None,
        current_user: User = Depends(require_business)
) -> dict:
    """Update ticket tier"""
    tier = ticket_tier_crud.get(db, id=tier_id)
    if not tier:
        raise NotFoundException("Ticket tier")

    # Verify ownership
    event = ticket_event_crud.get(db, id=tier.event_id)
    business = business_crud.get_by_user_id(db, user_id=current_user.id)

    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    # Update
    update_data = {}
    if is_active is not None:
        update_data["is_active"] = is_active
    if price is not None:
        update_data["price"] = price

    tier = ticket_tier_crud.update(db, db_obj=tier, obj_in=update_data)

    return {
        "success": True,
        "data": tier
    }


# ============================================
# TICKET BOOKING (CUSTOMER)
# ============================================

@router.post("/bookings/calculate-price", response_model=SuccessResponse[dict])
def calculate_booking_price(
        *,
        db: Session = Depends(get_db),
        tier_id: UUID,
        quantity: int = Query(..., gt=0)
) -> dict:
    """
    Calculate booking price before purchase

    - Shows price breakdown
    - Public endpoint
    """
    price_breakdown = ticket_service.calculate_booking_price(
        db,
        tier_id=tier_id,
        quantity=quantity
    )

    return {
        "success": True,
        "data": price_breakdown
    }


@router.post("/bookings", response_model=SuccessResponse[TicketBookingResponse], status_code=status.HTTP_201_CREATED)
def create_booking(
        *,
        db: Session = Depends(get_db),
        booking_in: TicketBookingCreateRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Book tickets

    - Only for customer accounts
    - Checks availability
    - Processes payment
    - Generates booking reference & QR code
    """
    booking = ticket_service.book_and_pay(
        db,
        current_user=current_user,
        event_id=booking_in.event_id,
        tier_id=booking_in.tier_id,
        quantity=booking_in.quantity,
        attendee_name=booking_in.attendee_name,
        attendee_email=booking_in.attendee_email,
        attendee_phone=booking_in.attendee_phone,
        additional_attendees=booking_in.additional_attendees,
        special_requests=booking_in.special_requests,
        payment_method=booking_in.payment_method
    )

    return {
        "success": True,
        "data": booking
    }


@router.get("/bookings/my", response_model=SuccessResponse[List[TicketBookingListResponse]])
def get_my_bookings(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_customer),
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get current customer's bookings"""
    bookings = ticket_booking_crud.get_customer_bookings(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    # Transform to list response
    booking_list = []
    for booking in bookings:
        event = ticket_event_crud.get(db, id=booking.event_id)
        tier = ticket_tier_crud.get(db, id=booking.tier_id)

        booking_list.append({
            "id": booking.id,
            "booking_reference": booking.booking_reference,
            "event_name": event.name,
            "tier_name": tier.name,
            "quantity": booking.quantity,
            "total_amount": booking.total_amount,
            "status": booking.status,
            "event_date": event.event_date or event.departure_date,
            "created_at": booking.created_at
        })

    return {
        "success": True,
        "data": booking_list
    }


@router.get("/bookings/{booking_id}", response_model=SuccessResponse[TicketBookingResponse])
def get_booking_details(
        *,
        db: Session = Depends(get_db),
        booking_id: UUID,
        current_user: User = Depends(get_current_active_user)
) -> dict:
    """Get booking details"""
    booking = ticket_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    # Verify permission
    if current_user.user_type == "customer":
        if booking.customer_id != current_user.id:
            raise PermissionDeniedException()
    elif current_user.user_type == "business":
        event = ticket_event_crud.get(db, id=booking.event_id)
        business = business_crud.get_by_user_id(db, user_id=current_user.id)

        if not business or event.business_id != business.id:
            raise PermissionDeniedException()

    return {
        "success": True,
        "data": booking
    }


@router.get("/bookings/reference/{booking_reference}", response_model=SuccessResponse[TicketBookingResponse])
def get_booking_by_reference(
        *,
        db: Session = Depends(get_db),
        booking_reference: str
) -> dict:
    """
    Get booking by reference code

    - Public endpoint for ticket verification
    """
    booking = db.query(TicketBooking).filter(
        TicketBooking.booking_reference == booking_reference
    ).first()

    if not booking:
        raise NotFoundException("Booking")

    return {
        "success": True,
        "data": booking
    }


@router.post("/bookings/{booking_id}/cancel", response_model=SuccessResponse[TicketBookingResponse])
def cancel_booking(
        *,
        db: Session = Depends(get_db),
        booking_id: UUID,
        reason: Optional[str] = None,
        current_user: User = Depends(require_customer)
) -> dict:
    """Cancel ticket booking"""
    booking = ticket_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    # Verify ownership
    if booking.customer_id != current_user.id:
        raise PermissionDeniedException()

    if booking.status in ["checked_in", "cancelled"]:
        raise ValidationException("Cannot cancel checked-in or already cancelled booking")

    # Check event date (allow cancellation up to 24 hours before)
    event = ticket_event_crud.get(db, id=booking.event_id)
    event_date = event.event_date or event.departure_date

    if event_date:
        from datetime import datetime, timedelta
        event_datetime = datetime.combine(event_date, datetime.min.time())
        if datetime.now() > event_datetime - timedelta(hours=24):
            raise ValidationException("Cannot cancel within 24 hours of event")

    # Cancel booking
    booking.status = "cancelled"
    booking.cancelled_at = datetime.utcnow()
    booking.cancellation_reason = reason

    # Restore capacity
    tier = ticket_tier_crud.get(db, id=booking.tier_id)
    tier.available_quantity += booking.quantity
    event.available_capacity += booking.quantity

    # TODO: Process refund based on cancellation policy

    db.commit()
    db.refresh(booking)

    return {
        "success": True,
        "data": booking
    }


# ============================================
# EVENT BOOKING MANAGEMENT (BUSINESS)
# ============================================

@router.get("/events/{event_id}/bookings", response_model=SuccessResponse[List[TicketBookingResponse]])
def get_event_bookings(
        *,
        db: Session = Depends(get_db),
        event_id: UUID,
        current_user: User = Depends(require_business),
        pagination: dict = Depends(get_pagination_params),
        status: Optional[str] = Query(None)
) -> dict:
    """Get event bookings (business)"""
    event = ticket_event_crud.get(db, id=event_id)
    if not event:
        raise NotFoundException("Event")

    # Verify ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    bookings = ticket_booking_crud.get_event_bookings(
        db,
        event_id=event_id,
        status=status,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": bookings
    }


@router.post("/bookings/{booking_id}/check-in", response_model=SuccessResponse[TicketBookingResponse])
def check_in_booking(
        *,
        db: Session = Depends(get_db),
        booking_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Check in a ticket (business/event staff)

    - Scans QR code or enters booking reference
    - Marks ticket as used
    """
    booking = ticket_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    # Verify ownership
    event = ticket_event_crud.get(db, id=booking.event_id)
    business = business_crud.get_by_user_id(db, user_id=current_user.id)

    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    # Check in
    booking = ticket_booking_crud.check_in_ticket(db, booking_id=booking_id)

    return {
        "success": True,
        "data": booking
    }


# ============================================
# STATISTICS (BUSINESS)
# ============================================

@router.get("/events/{event_id}/stats", response_model=SuccessResponse[dict])
def get_event_stats(
        *,
        db: Session = Depends(get_db),
        event_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Get event statistics"""
    event = ticket_event_crud.get(db, id=event_id)
    if not event:
        raise NotFoundException("Event")

    # Verify ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    # Get booking stats


    total_bookings = db.query(func.count(TicketBooking.id)).filter(
        TicketBooking.event_id == event_id
    ).scalar()

    confirmed_bookings = db.query(func.count(TicketBooking.id)).filter(
        TicketBooking.event_id == event_id,
        TicketBooking.status == "confirmed"
    ).scalar()

    checked_in = db.query(func.count(TicketBooking.id)).filter(
        TicketBooking.event_id == event_id,
        TicketBooking.status == "checked_in"
    ).scalar()

    return {
        "success": True,
        "data": {
            "total_capacity": event.total_capacity,
            "available_capacity": event.available_capacity,
            "tickets_sold": event.total_tickets_sold,
            "total_revenue": float(event.total_revenue),
            "total_bookings": total_bookings,
            "confirmed_bookings": confirmed_bookings,
            "checked_in_count": checked_in,
            "average_rating": float(event.average_rating)
        }
    }