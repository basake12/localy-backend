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
from app.schemas.services import (
    ServiceProviderCreateRequest,
    ServiceProviderResponse,
    ServiceCreateRequest,
    ServiceUpdateRequest,
    ServiceResponse,
    ServiceListResponse,
    AvailabilityCreateRequest,
    AvailabilityResponse,
    BookingCheckAvailabilityRequest,
    BookingCreateRequest,
    BookingResponse,
    BookingListResponse,
    DailyAvailability,
    ServiceSearchFilters
)
from app.services.service_service import service_service
from app.crud.services import (
    service_provider_crud,
    service_crud,
    service_availability_crud,
    service_booking_crud
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
# SERVICE SEARCH & DISCOVERY (PUBLIC)
# ============================================

@router.post("/search", response_model=SuccessResponse[List[dict]])
def search_services(
        *,
        db: Session = Depends(get_db),
        search_params: ServiceSearchFilters,
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """
    Search services with filters

    - Public endpoint (no auth required)
    - Location-based search
    - Category, price filters
    - Sort by price, popularity, rating
    """
    location = None
    if search_params.location:
        location = (
            search_params.location.latitude,
            search_params.location.longitude
        )

    results = service_service.search_services(
        db,
        query_text=search_params.query,
        category=search_params.category,
        subcategory=search_params.subcategory,
        min_price=search_params.min_price,
        max_price=search_params.max_price,
        location=location,
        radius_km=search_params.radius_km or 10.0,
        service_location_type=search_params.service_location_type,
        sort_by=search_params.sort_by or "created_at",
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": results
    }


@router.get("/{service_id}", response_model=SuccessResponse[dict])
def get_service_details(
        *,
        db: Session = Depends(get_db),
        service_id: UUID
) -> dict:
    """
    Get service details

    - Public endpoint
    - Returns service with provider info
    """
    service_data = service_service.get_service_details(db, service_id=service_id)

    return {
        "success": True,
        "data": service_data
    }


@router.get("/categories/list", response_model=SuccessResponse[List[str]])
def get_service_categories(
        *,
        db: Session = Depends(get_db)
) -> dict:
    """Get all service categories"""
    from sqlalchemy import distinct
    from app.models.services import Service

    categories = db.query(distinct(Service.category)).filter(
        Service.is_active == True
    ).all()

    return {
        "success": True,
        "data": [cat[0] for cat in categories if cat[0]]
    }


# ============================================
# SERVICE PROVIDER MANAGEMENT (BUSINESS ONLY)
# ============================================

@router.post("/providers", response_model=SuccessResponse[ServiceProviderResponse], status_code=status.HTTP_201_CREATED)
def create_service_provider(
        *,
        db: Session = Depends(get_db),
        provider_in: ServiceProviderCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Create service provider

    - Only for business accounts
    - Business must be in 'services' category
    """
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    if business.category != "services":
        raise ValidationException("Only services category businesses can create service providers")

    # Check if provider already exists
    existing_provider = service_provider_crud.get_by_business_id(db, business_id=business.id)
    if existing_provider:
        raise ValidationException("Service provider already exists for this business")

    # Create provider
    provider_data = provider_in.model_dump()
    provider_data["business_id"] = business.id

    # Handle location
    if provider_in.provider_location:
        from geoalchemy2.elements import WKTElement
        provider_data["provider_location"] = WKTElement(
            f"POINT({provider_in.provider_location.longitude} {provider_in.provider_location.latitude})",
            srid=4326
        )

    provider = service_provider_crud.create_from_dict(db, obj_in=provider_data)

    return {
        "success": True,
        "data": provider
    }


@router.get("/providers/my", response_model=SuccessResponse[ServiceProviderResponse])
def get_my_provider(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business)
) -> dict:
    """Get current business's service provider"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    provider = service_provider_crud.get_by_business_id(db, business_id=business.id)
    if not provider:
        raise NotFoundException("Service provider")

    return {
        "success": True,
        "data": provider
    }


# ============================================
# SERVICE MANAGEMENT (PROVIDER ONLY)
# ============================================

@router.post("/", response_model=SuccessResponse[ServiceResponse], status_code=status.HTTP_201_CREATED)
def create_service(
        *,
        db: Session = Depends(get_db),
        service_in: ServiceCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Create a new service

    - Only for service provider accounts
    """
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    provider = service_provider_crud.get_by_business_id(db, business_id=business.id)
    if not provider:
        raise NotFoundException("Service provider not found. Create provider first.")

    # Create service
    service_data = service_in.model_dump()
    service_data["provider_id"] = provider.id

    service = service_crud.create_from_dict(db, obj_in=service_data)

    return {
        "success": True,
        "data": service
    }


@router.get("/my/services", response_model=SuccessResponse[List[ServiceResponse]])
def get_my_services(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business),
        pagination: dict = Depends(get_pagination_params),
        active_only: bool = Query(True)
) -> dict:
    """Get current provider's services"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    provider = service_provider_crud.get_by_business_id(db, business_id=business.id)
    if not provider:
        raise NotFoundException("Service provider")

    services = service_crud.get_by_provider(
        db,
        provider_id=provider.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
        active_only=active_only
    )

    return {
        "success": True,
        "data": services
    }


@router.put("/{service_id}", response_model=SuccessResponse[ServiceResponse])
def update_service(
        *,
        db: Session = Depends(get_db),
        service_id: UUID,
        service_in: ServiceUpdateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """Update service"""
    service = service_crud.get(db, id=service_id)
    if not service:
        raise NotFoundException("Service")

    # Verify ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    provider = service_provider_crud.get_by_business_id(db, business_id=business.id)

    if not provider or service.provider_id != provider.id:
        raise PermissionDeniedException("You don't own this service")

    # Update
    update_data = service_in.model_dump(exclude_unset=True)
    service = service_crud.update(db, db_obj=service, obj_in=update_data)

    return {
        "success": True,
        "data": service
    }


@router.delete("/{service_id}", response_model=SuccessResponse[dict])
def delete_service(
        *,
        db: Session = Depends(get_db),
        service_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Delete service (soft delete)"""
    service = service_crud.get(db, id=service_id)
    if not service:
        raise NotFoundException("Service")

    # Verify ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    provider = service_provider_crud.get_by_business_id(db, business_id=business.id)

    if not provider or service.provider_id != provider.id:
        raise PermissionDeniedException()

    # Soft delete
    service.is_active = False
    db.commit()

    return {
        "success": True,
        "data": {"message": "Service deleted successfully"}
    }


# ============================================
# AVAILABILITY MANAGEMENT (PROVIDER ONLY)
# ============================================

@router.post("/availability", response_model=SuccessResponse[AvailabilityResponse], status_code=status.HTTP_201_CREATED)
def create_availability(
        *,
        db: Session = Depends(get_db),
        availability_in: AvailabilityCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Create availability slot

    - Sets working hours for specific days
    """
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    provider = service_provider_crud.get_by_business_id(db, business_id=business.id)

    if not provider:
        raise NotFoundException("Service provider")

    # Create availability
    availability_data = availability_in.model_dump()
    availability_data["provider_id"] = provider.id

    availability = service_availability_crud.create_from_dict(db, obj_in=availability_data)

    return {
        "success": True,
        "data": availability
    }


@router.get("/availability/my", response_model=SuccessResponse[List[AvailabilityResponse]])
def get_my_availability(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business)
) -> dict:
    """Get current provider's availability slots"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    provider = service_provider_crud.get_by_business_id(db, business_id=business.id)

    if not provider:
        raise NotFoundException("Service provider")

    availability = service_availability_crud.get_by_provider(db, provider_id=provider.id)

    return {
        "success": True,
        "data": availability
    }


@router.delete("/availability/{availability_id}", response_model=SuccessResponse[dict])
def delete_availability(
        *,
        db: Session = Depends(get_db),
        availability_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Delete availability slot"""
    availability = service_availability_crud.get(db, id=availability_id)
    if not availability:
        raise NotFoundException("Availability slot")

    # Verify ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    provider = service_provider_crud.get_by_business_id(db, business_id=business.id)

    if not provider or availability.provider_id != provider.id:
        raise PermissionDeniedException()

    service_availability_crud.delete(db, id=availability_id)

    return {
        "success": True,
        "data": {"message": "Availability slot deleted"}
    }


# ============================================
# BOOKING - CHECK AVAILABILITY (PUBLIC/CUSTOMER)
# ============================================

@router.post("/{service_id}/available-slots", response_model=SuccessResponse[DailyAvailability])
def get_available_slots(
        *,
        db: Session = Depends(get_db),
        service_id: UUID,
        booking_date: date = Query(...)
) -> dict:
    """
    Get available time slots for a service on specific date

    - Public endpoint
    - Shows available times and capacity
    """
    from datetime import datetime as dt

    slots = service_service.get_available_slots(
        db,
        service_id=service_id,
        booking_date=booking_date
    )

    day_name = dt.strptime(str(booking_date), "%Y-%m-%d").strftime("%A")

    return {
        "success": True,
        "data": {
            "date": booking_date,
            "day_name": day_name,
            "slots": slots
        }
    }


@router.post("/bookings/calculate-price", response_model=SuccessResponse[dict])
def calculate_booking_price(
        *,
        db: Session = Depends(get_db),
        service_id: UUID,
        selected_options: List[dict],
        service_location_type: str
) -> dict:
    """
    Calculate booking price before confirming

    - Shows price breakdown
    - Includes add-ons and travel fees
    """
    price_breakdown = service_service.calculate_booking_price(
        db,
        service_id=service_id,
        selected_options=selected_options,
        service_location_type=service_location_type
    )

    return {
        "success": True,
        "data": price_breakdown
    }


# ============================================
# BOOKING - CREATE (CUSTOMER)
# ============================================

@router.post("/bookings", response_model=SuccessResponse[BookingResponse], status_code=status.HTTP_201_CREATED)
def create_booking(
        *,
        db: Session = Depends(get_db),
        booking_in: BookingCreateRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Create service booking

    - Only for customer accounts
    - Checks availability
    - Processes wallet payment
    - Confirms booking
    """
    booking = service_service.book_and_pay(
        db,
        current_user=current_user,
        service_id=booking_in.service_id,
        booking_date=booking_in.booking_date,
        booking_time=booking_in.booking_time,
        number_of_people=booking_in.number_of_people,
        service_location_type=booking_in.service_location_type,
        service_address=booking_in.service_address,
        selected_options=booking_in.selected_options,
        special_requests=booking_in.special_requests,
        payment_method=booking_in.payment_method
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
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get current customer's bookings"""
    bookings = service_booking_crud.get_customer_bookings(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    # Transform to list response
    booking_list = []
    for booking in bookings:
        service = service_crud.get(db, id=booking.service_id)
        provider = service_provider_crud.get(db, id=booking.provider_id)
        business = business_crud.get(db, id=provider.business_id)

        booking_list.append({
            "id": booking.id,
            "service_name": service.name,
            "provider_name": business.business_name,
            "booking_date": booking.booking_date,
            "booking_time": booking.booking_time,
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
    booking = service_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    # Verify permission
    if current_user.user_type == "customer":
        if booking.customer_id != current_user.id:
            raise PermissionDeniedException()
    elif current_user.user_type == "business":
        business = business_crud.get_by_user_id(db, user_id=current_user.id)
        provider = service_provider_crud.get_by_business_id(db, business_id=business.id)

        if not provider or booking.provider_id != provider.id:
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
    booking = service_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    # Verify ownership
    if booking.customer_id != current_user.id:
        raise PermissionDeniedException()

    if booking.status in ["completed", "cancelled"]:
        raise ValidationException("Cannot cancel completed or already cancelled booking")

    # Cancel booking
    booking.status = "cancelled"
    booking.cancelled_at = datetime.utcnow()
    booking.cancellation_reason = reason

    # TODO: Process refund based on cancellation policy

    db.commit()
    db.refresh(booking)

    return {
        "success": True,
        "data": booking
    }


# ============================================
# PROVIDER BOOKING MANAGEMENT
# ============================================

@router.get("/bookings/provider/my", response_model=SuccessResponse[List[BookingResponse]])
def get_provider_bookings(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business),
        pagination: dict = Depends(get_pagination_params),
        date_from: Optional[date] = Query(None),
        date_to: Optional[date] = Query(None),
        booking_status: Optional[str] = Query(None)
) -> dict:
    """Get provider's bookings"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    provider = service_provider_crud.get_by_business_id(db, business_id=business.id)

    if not provider:
        raise NotFoundException("Service provider")

    bookings = service_booking_crud.get_provider_bookings(
        db,
        provider_id=provider.id,
        date_from=date_from,
        date_to=date_to,
        status=booking_status,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": bookings
    }


@router.post("/bookings/{booking_id}/start", response_model=SuccessResponse[BookingResponse])
def start_service(
        *,
        db: Session = Depends(get_db),
        booking_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Mark service as started (provider action)"""
    booking = service_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    # Verify ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    provider = service_provider_crud.get_by_business_id(db, business_id=business.id)

    if not provider or booking.provider_id != provider.id:
        raise PermissionDeniedException()

    if booking.status != "confirmed":
        raise ValidationException("Can only start confirmed bookings")

    booking.status = "in_progress"
    booking.started_at = datetime.utcnow()
    db.commit()
    db.refresh(booking)

    return {
        "success": True,
        "data": booking
    }


@router.post("/bookings/{booking_id}/complete", response_model=SuccessResponse[BookingResponse])
def complete_service(
        *,
        db: Session = Depends(get_db),
        booking_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Mark service as completed (provider action)"""
    booking = service_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    # Verify ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    provider = service_provider_crud.get_by_business_id(db, business_id=business.id)

    if not provider or booking.provider_id != provider.id:
        raise PermissionDeniedException()

    if booking.status != "in_progress":
        raise ValidationException("Can only complete in-progress bookings")

    booking.status = "completed"
    booking.completed_at = datetime.utcnow()

    # Update service stats
    service = service_crud.get(db, id=booking.service_id)
    service.bookings_count += 1

    # Update provider stats
    provider.total_bookings += 1

    db.commit()
    db.refresh(booking)

    return {
        "success": True,
        "data": booking
    }