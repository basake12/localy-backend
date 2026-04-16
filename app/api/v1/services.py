# app/api/v1/services.py

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
from datetime import date, datetime as dt

from app.core.database import get_db
from app.dependencies import (
    get_current_active_user,
    require_customer,
    require_business,
    get_pagination_params,
)
from app.schemas.common_schema import SuccessResponse
from app.schemas.services_schema import (
    ServiceProviderCreateRequest,
    ServiceProviderResponse,
    ServiceCreateRequest,
    ServiceUpdateRequest,
    ServiceResponse,
    AvailabilityCreateRequest,
    AvailabilityResponse,
    BookingCreateRequest,
    BookingResponse,
    BookingListResponse,
    DailyAvailability,
    ServiceSearchFilters,
    PriceCalculateRequest,
)
from app.services import service_service
from app.crud.services_crud import (
    service_provider_crud,
    service_crud,
    service_availability_crud,
    service_booking_crud,
)
from app.crud.business_crud import business_crud
from app.models.user_model import User
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)

router = APIRouter()


# ─────────────────────────────────────────────────────────────
# SEARCH & DISCOVERY  (public)
# ─────────────────────────────────────────────────────────────

@router.post("/search", response_model=SuccessResponse[List[dict]])
def search_services(
    *,
    db: Session = Depends(get_db),
    search_params: ServiceSearchFilters,
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    """
    Search service providers.

    Per Blueprint Section 3.1: discovery is radius-based only.
    Pass 'location' (lat/lng) + 'radius_km' to scope results geographically.
    Default radius: 5 km. No LGA filtering.
    """
    location = None
    if search_params.location:
        location = (
            search_params.location.latitude,
            search_params.location.longitude,
        )

    results = service_service.search_services(
        db,
        query_text=search_params.query,
        category=search_params.category,
        subcategory=search_params.subcategory,
        min_price=search_params.min_price,
        max_price=search_params.max_price,
        location=location,
        radius_km=search_params.radius_km or 5.0,
        service_location_type=search_params.service_location_type,
        sort_by=search_params.sort_by or "created_at",
        skip=pagination["skip"],
        limit=pagination["limit"],
    )

    return {"success": True, "data": results}


@router.get("/categories/list", response_model=SuccessResponse[List[str]])
def get_service_categories(db: Session = Depends(get_db)) -> dict:
    """Return distinct active service categories."""
    from sqlalchemy import distinct
    from app.models.services_model import Service

    categories = (
        db.query(distinct(Service.category))
        .filter(Service.is_active == True)
        .all()
    )
    return {"success": True, "data": [c[0] for c in categories if c[0]]}


@router.get("/{service_id}", response_model=SuccessResponse[dict])
def get_service_details(
    *,
    db: Session = Depends(get_db),
    service_id: UUID,
) -> dict:
    data = service_service.get_service_details(db, service_id=service_id)
    return {"success": True, "data": data}


# ─────────────────────────────────────────────────────────────
# PROVIDER MANAGEMENT  (business only)
# ─────────────────────────────────────────────────────────────

@router.post(
    "/providers",
    response_model=SuccessResponse[ServiceProviderResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_service_provider(
    *,
    db: Session = Depends(get_db),
    provider_in: ServiceProviderCreateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    """
    Create service provider profile. Business must be in 'services' category.

    FIX: business_crud inherits from AsyncCRUDBase — all its methods expect
    AsyncSession. This route uses a sync Session from get_db(). Calling
    business_crud.get_by_user_id(sync_db) causes db.execute() to return a
    ChunkedIteratorResult directly; awaiting it then raises TypeError.

    Fix: use get_by_user_id_sync() which accepts a sync Session and uses
    db.query() — consistent with services_crud.py throughout this module.
    """
    biz = business_crud.get_by_user_id_sync(db, user_id=current_user.id)
    if not biz:
        raise NotFoundException("Business")
    if biz.category != "services":
        raise ValidationException(
            "Only 'services' category businesses can create a service provider"
        )
    if service_provider_crud.get_by_business_id(db, business_id=biz.id):
        raise ValidationException("Service provider already exists for this business")

    provider_data = provider_in.model_dump()
    provider_data["business_id"] = biz.id

    if provider_in.provider_location:
        from geoalchemy2.elements import WKTElement
        provider_data["provider_location"] = WKTElement(
            f"POINT({provider_in.provider_location.longitude} "
            f"{provider_in.provider_location.latitude})",
            srid=4326,
        )

    provider = service_provider_crud.create_from_dict(db, obj_in=provider_data)
    return {"success": True, "data": provider}


@router.get(
    "/providers/my",
    response_model=SuccessResponse[ServiceProviderResponse],
)
def get_my_provider(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
) -> dict:
    biz = business_crud.get_by_user_id_sync(db, user_id=current_user.id)
    if not biz:
        raise NotFoundException("Business")
    provider = service_provider_crud.get_by_business_id(db, business_id=biz.id)
    if not provider:
        raise NotFoundException("Service provider")
    return {"success": True, "data": provider}


# ─────────────────────────────────────────────────────────────
# SERVICE MANAGEMENT  (provider only)
# ─────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=SuccessResponse[ServiceResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_service(
    *,
    db: Session = Depends(get_db),
    service_in: ServiceCreateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    biz = business_crud.get_by_user_id_sync(db, user_id=current_user.id)
    if not biz:
        raise NotFoundException("Business")
    provider = service_provider_crud.get_by_business_id(db, business_id=biz.id)
    if not provider:
        raise NotFoundException(
            "Service provider — create one first via POST /services/providers"
        )

    service_data = service_in.model_dump()
    service_data["provider_id"] = provider.id
    service = service_crud.create_from_dict(db, obj_in=service_data)
    return {"success": True, "data": service}


@router.get(
    "/my/services",
    response_model=SuccessResponse[List[ServiceResponse]],
)
def get_my_services(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
    pagination: dict = Depends(get_pagination_params),
    active_only: bool = Query(True),
) -> dict:
    biz = business_crud.get_by_user_id_sync(db, user_id=current_user.id)
    if not biz:
        raise NotFoundException("Business")
    provider = service_provider_crud.get_by_business_id(db, business_id=biz.id)
    if not provider:
        raise NotFoundException("Service provider")

    services = service_crud.get_by_provider(
        db,
        provider_id=provider.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
        active_only=active_only,
    )
    return {"success": True, "data": services}


@router.put("/{service_id}", response_model=SuccessResponse[ServiceResponse])
def update_service(
    *,
    db: Session = Depends(get_db),
    service_id: UUID,
    service_in: ServiceUpdateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    service = service_crud.get(db, id=service_id)
    if not service:
        raise NotFoundException("Service")

    biz = business_crud.get_by_user_id_sync(db, user_id=current_user.id)
    if not biz:
        raise NotFoundException("Business")
    provider = service_provider_crud.get_by_business_id(db, business_id=biz.id)
    if not provider or service.provider_id != provider.id:
        raise PermissionDeniedException("You do not own this service")

    service = service_crud.update(
        db, db_obj=service, obj_in=service_in.model_dump(exclude_unset=True)
    )
    return {"success": True, "data": service}


@router.patch("/{service_id}/toggle", response_model=SuccessResponse[dict])
def toggle_service(
    *,
    db: Session = Depends(get_db),
    service_id: UUID,
    is_active: bool = Query(..., description="Set true to activate, false to deactivate"),
    current_user: User = Depends(require_business),
) -> dict:
    """Enable or disable a service offering."""
    biz = business_crud.get_by_user_id_sync(db, user_id=current_user.id)
    if not biz:
        raise NotFoundException("Business")
    provider = service_provider_crud.get_by_business_id(db, business_id=biz.id)
    if not provider:
        raise NotFoundException("Service provider")

    service_service.toggle_service_active(
        db,
        service_id=service_id,
        provider_id=provider.id,
        is_active=is_active,
    )
    return {"success": True, "data": {"is_active": is_active}}


@router.delete("/{service_id}", response_model=SuccessResponse[dict])
def delete_service(
    *,
    db: Session = Depends(get_db),
    service_id: UUID,
    current_user: User = Depends(require_business),
) -> dict:
    service = service_crud.get(db, id=service_id)
    if not service:
        raise NotFoundException("Service")

    biz = business_crud.get_by_user_id_sync(db, user_id=current_user.id)
    if not biz:
        raise NotFoundException("Business")
    provider = service_provider_crud.get_by_business_id(db, business_id=biz.id)
    if not provider or service.provider_id != provider.id:
        raise PermissionDeniedException()

    service.is_active = False
    db.commit()
    return {"success": True, "data": {"message": "Service deleted"}}


# ─────────────────────────────────────────────────────────────
# AVAILABILITY  (provider only)
# ─────────────────────────────────────────────────────────────

@router.post(
    "/availability",
    response_model=SuccessResponse[AvailabilityResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_availability(
    *,
    db: Session = Depends(get_db),
    availability_in: AvailabilityCreateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    biz = business_crud.get_by_user_id_sync(db, user_id=current_user.id)
    if not biz:
        raise NotFoundException("Business")
    provider = service_provider_crud.get_by_business_id(db, business_id=biz.id)
    if not provider:
        raise NotFoundException("Service provider")

    data = availability_in.model_dump()
    data["provider_id"] = provider.id
    availability = service_availability_crud.create_from_dict(db, obj_in=data)
    return {"success": True, "data": availability}


@router.get(
    "/availability/my",
    response_model=SuccessResponse[List[AvailabilityResponse]],
)
def get_my_availability(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
) -> dict:
    biz = business_crud.get_by_user_id_sync(db, user_id=current_user.id)
    if not biz:
        raise NotFoundException("Business")
    provider = service_provider_crud.get_by_business_id(db, business_id=biz.id)
    if not provider:
        raise NotFoundException("Service provider")

    availability = service_availability_crud.get_by_provider(
        db, provider_id=provider.id
    )
    return {"success": True, "data": availability}


@router.delete(
    "/availability/{availability_id}",
    response_model=SuccessResponse[dict],
)
def delete_availability(
    *,
    db: Session = Depends(get_db),
    availability_id: UUID,
    current_user: User = Depends(require_business),
) -> dict:
    avail = service_availability_crud.get(db, id=availability_id)
    if not avail:
        raise NotFoundException("Availability slot")

    biz = business_crud.get_by_user_id_sync(db, user_id=current_user.id)
    if not biz:
        raise NotFoundException("Business")
    provider = service_provider_crud.get_by_business_id(db, business_id=biz.id)
    if not provider or avail.provider_id != provider.id:
        raise PermissionDeniedException()

    service_availability_crud.delete(db, id=availability_id)
    return {"success": True, "data": {"message": "Availability slot deleted"}}


# ─────────────────────────────────────────────────────────────
# SLOT AVAILABILITY  (public)
# ─────────────────────────────────────────────────────────────

@router.post(
    "/{service_id}/available-slots",
    response_model=SuccessResponse[DailyAvailability],
)
def get_available_slots(
    *,
    db: Session = Depends(get_db),
    service_id: UUID,
    booking_date: date = Query(...),
) -> dict:
    slots = service_service.get_available_slots(
        db, service_id=service_id, booking_date=booking_date
    )
    day_name = dt.strptime(str(booking_date), "%Y-%m-%d").strftime("%A")
    return {
        "success": True,
        "data": {"date": booking_date, "day_name": day_name, "slots": slots},
    }


# ─────────────────────────────────────────────────────────────
# PRICE CALCULATION  (authenticated)
# ─────────────────────────────────────────────────────────────

@router.post("/bookings/calculate-price", response_model=SuccessResponse[dict])
def calculate_booking_price(
    *,
    db: Session = Depends(get_db),
    price_req: PriceCalculateRequest,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Returns price breakdown before checkout."""
    breakdown = service_service.calculate_booking_price(
        db,
        service_id=price_req.service_id,
        selected_options=price_req.selected_options,
        service_location_type=price_req.service_location_type,
    )
    return {"success": True, "data": breakdown}


# ─────────────────────────────────────────────────────────────
# BOOKING  (customer)
# ─────────────────────────────────────────────────────────────

@router.post(
    "/bookings",
    response_model=SuccessResponse[BookingResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_booking(
    *,
    db: Session = Depends(get_db),
    booking_in: BookingCreateRequest,
    current_user: User = Depends(require_customer),
) -> dict:
    """
    Book a service and pay via customer wallet.

    Platform fee: ₦100 deducted from transaction before crediting business wallet.
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
        payment_method=booking_in.payment_method,
    )
    return {"success": True, "data": booking}


@router.get(
    "/bookings/my",
    response_model=SuccessResponse[List[BookingListResponse]],
)
def get_my_bookings(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    bookings = service_booking_crud.get_customer_bookings(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )

    result = []
    for b in bookings:
        provider = service_provider_crud.get(db, id=b.provider_id)
        # FIX: business_crud.get() is async (AsyncCRUDBase). Use get_sync()
        # to stay on the sync Session used throughout this module.
        biz = business_crud.get_sync(db, id=provider.business_id) if provider else None
        result.append({
            "id":             b.id,
            "service_name":   b.service.name if b.service else "N/A",
            "provider_name":  biz.business_name if biz else "N/A",
            "booking_date":   b.booking_date,
            "booking_time":   b.booking_time,
            "total_price":    b.total_price,
            "status":         b.status,
            "payment_status": b.payment_status,
            "created_at":     b.created_at,
        })

    return {"success": True, "data": result}


@router.get(
    "/bookings/{booking_id}",
    response_model=SuccessResponse[BookingResponse],
)
def get_booking_details(
    *,
    db: Session = Depends(get_db),
    booking_id: UUID,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    booking = service_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    if current_user.user_type == "customer":
        if booking.customer_id != current_user.id:
            raise PermissionDeniedException()
    elif current_user.user_type == "business":
        biz = business_crud.get_by_user_id_sync(db, user_id=current_user.id)
        if not biz:
            raise NotFoundException("Business")
        provider = service_provider_crud.get_by_business_id(db, business_id=biz.id)
        if not provider or booking.provider_id != provider.id:
            raise PermissionDeniedException()

    return {"success": True, "data": booking}


@router.post(
    "/bookings/{booking_id}/cancel",
    response_model=SuccessResponse[BookingResponse],
)
def cancel_booking(
    *,
    db: Session = Depends(get_db),
    booking_id: UUID,
    reason: Optional[str] = None,
    current_user: User = Depends(require_customer),
) -> dict:
    """Cancel a booking. Instant wallet refund per Blueprint Section 4.1.2."""
    booking = service_service.cancel_booking(
        db, booking_id=booking_id, customer_id=current_user.id, reason=reason
    )
    return {"success": True, "data": booking}


# ─────────────────────────────────────────────────────────────
# PROVIDER BOOKING MANAGEMENT  (business)
# ─────────────────────────────────────────────────────────────

@router.get(
    "/bookings/provider/my",
    response_model=SuccessResponse[List[BookingResponse]],
)
def get_provider_bookings(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
    pagination: dict = Depends(get_pagination_params),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    booking_status: Optional[str] = Query(None),
) -> dict:
    biz = business_crud.get_by_user_id_sync(db, user_id=current_user.id)
    if not biz:
        raise NotFoundException("Business")
    provider = service_provider_crud.get_by_business_id(db, business_id=biz.id)
    if not provider:
        raise NotFoundException("Service provider")

    bookings = service_booking_crud.get_provider_bookings(
        db,
        provider_id=provider.id,
        date_from=date_from,
        date_to=date_to,
        status=booking_status,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": bookings}


@router.post(
    "/bookings/{booking_id}/start",
    response_model=SuccessResponse[BookingResponse],
)
def start_service(
    *,
    db: Session = Depends(get_db),
    booking_id: UUID,
    current_user: User = Depends(require_business),
) -> dict:
    biz = business_crud.get_by_user_id_sync(db, user_id=current_user.id)
    if not biz:
        raise NotFoundException("Business")
    provider = service_provider_crud.get_by_business_id(db, business_id=biz.id)
    if not provider:
        raise NotFoundException("Service provider")

    booking = service_service.start_service(
        db, booking_id=booking_id, provider_id=provider.id
    )
    return {"success": True, "data": booking}


@router.post(
    "/bookings/{booking_id}/complete",
    response_model=SuccessResponse[BookingResponse],
)
def complete_service(
    *,
    db: Session = Depends(get_db),
    booking_id: UUID,
    current_user: User = Depends(require_business),
) -> dict:
    biz = business_crud.get_by_user_id_sync(db, user_id=current_user.id)
    if not biz:
        raise NotFoundException("Business")
    provider = service_provider_crud.get_by_business_id(db, business_id=biz.id)
    if not provider:
        raise NotFoundException("Service provider")

    booking = service_service.complete_service(
        db, booking_id=booking_id, provider_id=provider.id
    )
    return {"success": True, "data": booking}


# ─────────────────────────────────────────────────────────────
# ANALYTICS  (business)
# ─────────────────────────────────────────────────────────────

@router.get("/analytics/my", response_model=SuccessResponse[dict])
def get_service_analytics(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
) -> dict:
    """
    Provider analytics: total bookings, revenue, completion rate,
    revenue trend (last 7 days), top services by booking count.
    """
    biz = business_crud.get_by_user_id_sync(db, user_id=current_user.id)
    if not biz:
        raise NotFoundException("Business")
    provider = service_provider_crud.get_by_business_id(db, business_id=biz.id)
    if not provider:
        raise NotFoundException("Service provider")

    analytics = service_service.get_provider_analytics(
        db, provider_id=provider.id
    )
    return {"success": True, "data": analytics}