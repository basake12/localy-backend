from decimal import Decimal
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from uuid import UUID
from datetime import date

from app.core.database import get_async_db
from app.dependencies import (
    get_current_active_user,
    require_customer,
    require_business,
    get_pagination_params
)
from app.schemas.common_schema import SuccessResponse, PaginatedResponse
from app.schemas.tickets_schema import (
    TicketEventCreateRequest,
    TicketEventUpdateRequest,
    TicketEventResponse,
    TicketEventListResponse,
    TicketTierCreateRequest,
    TicketTierResponse,
    TicketBookingCreateRequest,
    TicketBookingResponse,
    TicketBookingListResponse,
    TicketEventSearchFilters,
    CheckInRequest,
)
from app.crud.tickets_crud import (
    ticket_event_crud,
    ticket_tier_crud,
    ticket_booking_crud
)
from app.crud.business_crud import business_crud
from app.models.user_model import User
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException
)
from app.models.tickets_model import TicketBooking, TicketEvent
from sqlalchemy import func, select
from app.models.tickets_model import EventCategoryEnum

router = APIRouter()


# ============================================================
# EVENT SEARCH & DISCOVERY  (PUBLIC)
# ============================================================

@router.post(
    "/search",
    response_model=SuccessResponse[List[TicketEventListResponse]]
)
async def search_events(
        *,
        db: AsyncSession = Depends(get_async_db),
        search_params: TicketEventSearchFilters,
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """
    Search events and transport tickets.

    - Public endpoint — no auth required.
    - Location filtered via PostGIS ST_DWithin radius only.
    - No LGA filtering (Blueprint §3.1: radius-based, no LGA dependency).
    """
    location = None
    if search_params.location:
        location = (
            search_params.location.latitude,
            search_params.location.longitude
        )

    results = await ticket_event_crud.search_events(
        db,
        query_text=search_params.query,
        event_type=search_params.event_type,
        category=search_params.category,
        # lga_id intentionally omitted — Blueprint §3.1: radius-only discovery
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


@router.get(
    "/{event_id}",
    response_model=SuccessResponse[TicketEventResponse]
)
async def get_event_details(
        *,
        db: AsyncSession = Depends(get_async_db),
        event_id: UUID
) -> dict:
    """Get full event details including tiers. Public endpoint."""
    event = await ticket_event_crud.get(db, id=event_id)
    if not event or not event.is_active:
        raise NotFoundException("Event")

    return {"success": True, "data": event}


@router.get(
    "/{event_id}/tiers",
    response_model=SuccessResponse[List[TicketTierResponse]]
)
async def get_event_tiers(
        *,
        db: AsyncSession = Depends(get_async_db),
        event_id: UUID
) -> dict:
    """Get active ticket tiers for an event. Public."""
    tiers = await ticket_tier_crud.get_by_event(db, event_id=event_id)
    return {"success": True, "data": tiers}


# ============================================================
# TICKET PURCHASING  (CUSTOMER)
# ============================================================

@router.post(
    "/bookings",
    response_model=SuccessResponse[TicketBookingResponse],
    status_code=status.HTTP_201_CREATED
)
async def purchase_tickets(
        *,
        db: AsyncSession = Depends(get_async_db),
        booking_data: TicketBookingCreateRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Purchase tickets for an event.

    - Validates sales period, tier availability, and quantity limits.
    - Inventory deduction is race-condition-safe (SELECT FOR UPDATE).
    - Platform fee: ₦50 per ticket (Blueprint §4.4).
    - Returns booking with QR URL (generated async by Celery task post-commit).
    """
    booking = await ticket_booking_crud.create_booking(
        db,
        event_id=booking_data.event_id,
        tier_id=booking_data.tier_id,
        customer_id=current_user.id,
        quantity=booking_data.quantity,
        attendee_name=booking_data.attendee_name,
        attendee_email=booking_data.attendee_email,
        attendee_phone=booking_data.attendee_phone,
        additional_attendees=[a.model_dump() for a in booking_data.additional_attendees],
        special_requests=booking_data.special_requests
    )

    return {"success": True, "data": booking}


@router.get(
    "/bookings/my",
    response_model=SuccessResponse[List[TicketBookingListResponse]]
)
async def get_my_bookings(
        *,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(require_customer),
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get current customer's ticket bookings."""
    bookings = await ticket_booking_crud.get_customer_bookings(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )
    return {"success": True, "data": bookings}


@router.post(
    "/bookings/{booking_id}/cancel",
    response_model=SuccessResponse[TicketBookingResponse]
)
async def cancel_booking(
        *,
        db: AsyncSession = Depends(get_async_db),
        booking_id: UUID,
        reason: Optional[str] = Query(None, max_length=500),
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Cancel a ticket booking.

    - 24-hour cancellation window enforced before event start.
    - Capacity is restored on cancellation.
    - Refund credited to customer wallet instantly (Blueprint §4.1.2).
    """
    booking = await ticket_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    if booking.customer_id != current_user.id:
        raise PermissionDeniedException()

    event = await ticket_event_crud.get(db, id=booking.event_id)
    event_date = event.event_date or event.departure_date
    if event_date:
        event_dt = datetime.combine(event_date, datetime.min.time()).replace(
            tzinfo=timezone.utc
        )
        if datetime.now(timezone.utc) > event_dt - timedelta(hours=24):
            raise ValidationException("Cannot cancel within 24 hours of the event")

    booking = await ticket_booking_crud.cancel_booking(
        db,
        booking_id=booking_id,
        reason=reason
    )

    return {"success": True, "data": booking}


# ============================================================
# CHECK-IN  (BUSINESS / DOOR STAFF)
# ============================================================

@router.post(
    "/checkin",
    response_model=SuccessResponse[TicketBookingResponse]
)
async def check_in_ticket(
        *,
        db: AsyncSession = Depends(get_async_db),
        checkin_data: CheckInRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Check in a ticket by scanning the QR booking reference.

    - QR code encodes booking_reference (opaque string, not UUID).
    - Prevents UUID enumeration attacks.
    - Race condition protected via SELECT FOR UPDATE in CRUD layer.
    """
    booking = await ticket_booking_crud.get_by_reference(
        db, booking_reference=checkin_data.booking_reference
    )
    if not booking:
        raise NotFoundException("Booking")

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    event = await ticket_event_crud.get(db, id=booking.event_id)
    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    booking = await ticket_booking_crud.check_in_ticket(
        db, booking_reference=checkin_data.booking_reference
    )

    return {"success": True, "data": booking}


# ============================================================
# EVENT MANAGEMENT  (BUSINESS)
# ============================================================

@router.post(
    "/events",
    response_model=SuccessResponse[TicketEventResponse],
    status_code=status.HTTP_201_CREATED
)
async def create_event(
        *,
        db: AsyncSession = Depends(get_async_db),
        event_data: TicketEventCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Create a new event or transport schedule."""
    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business profile")

    event_dict = event_data.model_dump(
        exclude={"venue_location", "origin_location", "destination_location"}
    )
    event_dict["business_id"] = business.id
    event_dict["available_capacity"] = event_data.total_capacity

    event = await ticket_event_crud.create(db, obj_in=event_dict)
    return {"success": True, "data": event}


@router.patch(
    "/events/{event_id}",
    response_model=SuccessResponse[TicketEventResponse]
)
async def update_event(
        *,
        db: AsyncSession = Depends(get_async_db),
        event_id: UUID,
        update_data: TicketEventUpdateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Update event details."""
    event = await ticket_event_crud.get(db, id=event_id)
    if not event:
        raise NotFoundException("Event")

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    event = await ticket_event_crud.update(
        db, db_obj=event, obj_in=update_data.model_dump(exclude_none=True)
    )
    return {"success": True, "data": event}


@router.post(
    "/events/{event_id}/tiers",
    response_model=SuccessResponse[TicketTierResponse],
    status_code=status.HTTP_201_CREATED
)
async def add_tier(
        *,
        db: AsyncSession = Depends(get_async_db),
        event_id: UUID,
        tier_data: TicketTierCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Add a ticket tier to an event."""
    event = await ticket_event_crud.get(db, id=event_id)
    if not event:
        raise NotFoundException("Event")

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    tier_dict = tier_data.model_dump()
    tier_dict["event_id"] = event_id
    tier_dict["available_quantity"] = tier_data.total_quantity

    tier = await ticket_tier_crud.create(db, obj_in=tier_dict)
    return {"success": True, "data": tier}


@router.get(
    "/events/{event_id}/bookings",
    response_model=SuccessResponse[List[TicketBookingResponse]]
)
async def get_event_bookings(
        *,
        db: AsyncSession = Depends(get_async_db),
        event_id: UUID,
        current_user: User = Depends(require_business),
        pagination: dict = Depends(get_pagination_params),
        status: Optional[str] = Query(None)
) -> dict:
    """Get all bookings for a specific event (business only)."""
    event = await ticket_event_crud.get(db, id=event_id)
    if not event:
        raise NotFoundException("Event")

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    bookings = await ticket_booking_crud.get_event_bookings(
        db,
        event_id=event_id,
        status=status,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {"success": True, "data": bookings}


@router.get(
    "/events/{event_id}/stats",
    response_model=SuccessResponse[dict]
)
async def get_event_stats(
        *,
        db: AsyncSession = Depends(get_async_db),
        event_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Get live stats for a specific event."""
    event = await ticket_event_crud.get(db, id=event_id)
    if not event:
        raise NotFoundException("Event")

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    total = await db.scalar(
        select(func.count(TicketBooking.id))
        .where(TicketBooking.event_id == event_id)
    )
    confirmed = await db.scalar(
        select(func.count(TicketBooking.id))
        .where(TicketBooking.event_id == event_id, TicketBooking.status == "confirmed")
    )
    checked_in = await db.scalar(
        select(func.count(TicketBooking.id))
        .where(TicketBooking.event_id == event_id, TicketBooking.status == "checked_in")
    )

    return {
        "success": True,
        "data": {
            "total_capacity": event.total_capacity,
            "available_capacity": event.available_capacity,
            "tickets_sold": event.total_tickets_sold,
            "total_revenue": float(event.total_revenue),
            "total_bookings": total,
            "confirmed_bookings": confirmed,
            "checked_in_count": checked_in,
            "average_rating": float(event.average_rating),
        }
    }


@router.get(
    "/my-events",
    response_model=SuccessResponse[List[TicketEventListResponse]]
)
async def get_my_events(
        *,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(require_business),
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get all events owned by the authenticated business."""
    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business profile")

    events = await ticket_event_crud.get_by_business_id(
        db,
        business_id=business.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )
    return {"success": True, "data": events}