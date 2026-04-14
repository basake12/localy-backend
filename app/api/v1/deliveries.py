from decimal import Decimal
from typing import List, Optional
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func

# FIX: was `import Delivery` (bare broken import)
from app.models.delivery_model import Delivery, DeliveryTracking
# FIX: was `from app.models.delivery import RiderEarnings` (wrong module)
from app.models.delivery_model import RiderEarnings
from app.models.rider_model import Rider
from app.models.user_model import User

from app.core.database import get_db
from app.dependencies import (
    get_current_active_user,
    require_customer,
    require_rider,
    require_admin,
    get_pagination_params,
)
from app.schemas.common_schema import SuccessResponse
from app.schemas.delivery_schema import (
    DeliveryCreateRequest,
    DeliveryQuoteRequest,
    DeliveryResponse,
    DeliveryListResponse,
    DeliveryDetailsResponse,
    RiderLocationUpdate,
    RiderAvailabilityUpdate,
    RiderStatsResponse,
    RiderEarningsResponse,
    AssignRiderRequest,
    DeliveryZoneCreateRequest,
    DeliveryZoneResponse,
)
from app.services import delivery_service
from app.crud.delivery_crud import (
    delivery_crud,
    delivery_zone_crud,
    rider_earnings_crud,
)
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)

router = APIRouter()


# ============================================================
# QUOTE  (public — no auth required)
# ============================================================

@router.post("/quote", response_model=SuccessResponse[dict])
def get_delivery_quote(
    *,
    db: Session = Depends(get_db),
    quote_in: DeliveryQuoteRequest,
) -> dict:
    """
    Get delivery price quote.

    FIX: was a GET with coords in Query params — changed to POST with body
    so coordinates are not leaked in server logs or browser history.
    """
    quote = delivery_service.calculate_delivery_quote(
        db,
        pickup_location=(
            quote_in.pickup_location.latitude,
            quote_in.pickup_location.longitude,
        ),
        dropoff_location=(
            quote_in.dropoff_location.latitude,
            quote_in.dropoff_location.longitude,
        ),
        order_type=quote_in.order_type,
    )
    return {"success": True, "data": quote}


# ============================================================
# DELIVERY CREATION  (customer)
# ============================================================

@router.post(
    "/",
    response_model=SuccessResponse[DeliveryResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_delivery(
    *,
    db: Session = Depends(get_db),
    delivery_in: DeliveryCreateRequest,
    current_user: User = Depends(require_customer),
) -> dict:
    """
    Create a new delivery.

    - Customer only
    - Calculates pricing (zone-aware)
    - Processes wallet payment (or flags as COD)
    - Auto-assigns nearest available rider
    """
    delivery = delivery_service.create_and_pay_delivery(
        db,
        current_user=current_user,
        order_type=delivery_in.order_type,
        pickup_address=delivery_in.pickup_address,
        pickup_location=(
            delivery_in.pickup_location.latitude,
            delivery_in.pickup_location.longitude,
        ),
        pickup_contact_name=delivery_in.pickup_contact_name,
        pickup_contact_phone=delivery_in.pickup_contact_phone,
        pickup_instructions=delivery_in.pickup_instructions,
        dropoff_address=delivery_in.dropoff_address,
        dropoff_location=(
            delivery_in.dropoff_location.latitude,
            delivery_in.dropoff_location.longitude,
        ),
        dropoff_contact_name=delivery_in.dropoff_contact_name,
        dropoff_contact_phone=delivery_in.dropoff_contact_phone,
        dropoff_instructions=delivery_in.dropoff_instructions,
        package_description=delivery_in.package_description,
        package_weight_kg=delivery_in.package_weight_kg,
        package_value=delivery_in.package_value,
        order_id=delivery_in.order_id,
        payment_method=delivery_in.payment_method,
        cod_amount=delivery_in.cod_amount,
        requires_cold_storage=delivery_in.requires_cold_storage,
        is_fragile=delivery_in.is_fragile,
        required_vehicle_type=delivery_in.required_vehicle_type,
    )
    return {"success": True, "data": delivery}


# ============================================================
# TRACKING BY CODE  (public)
# ============================================================

@router.get("/track/{tracking_code}", response_model=SuccessResponse[DeliveryDetailsResponse])
def track_delivery(
    *,
    db: Session = Depends(get_db),
    tracking_code: str,
) -> dict:
    """Track a delivery using its public tracking code (no login required)."""
    delivery = delivery_crud.get_by_tracking_code(db, tracking_code=tracking_code)
    if not delivery:
        raise NotFoundException("Delivery not found")

    tracking_updates = (
        db.query(DeliveryTracking)
        .filter(DeliveryTracking.delivery_id == delivery.id)
        .order_by(DeliveryTracking.created_at.desc())
        .all()
    )

    rider_info = None
    if delivery.rider_id:
        # FIX: db.get() replaces deprecated db.query(Model).get(id)
        rider = db.get(Rider, delivery.rider_id)
        if rider:
            rider_info = {
                "name": f"{rider.first_name} {rider.last_name}",
                "phone": rider.phone,
                "vehicle_type": rider.vehicle_type,
                "vehicle_plate": rider.vehicle_plate_number,
                "average_rating": float(rider.average_rating),
            }

    return {
        "success": True,
        "data": {
            "delivery": delivery,
            "tracking_updates": tracking_updates,
            "rider_info": rider_info,
        },
    }


# ============================================================
# CUSTOMER — delivery list
# ============================================================

@router.get("/my", response_model=SuccessResponse[List[DeliveryListResponse]])
def get_my_deliveries(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
    pagination: dict = Depends(get_pagination_params),
    delivery_status: Optional[str] = Query(None, alias="status"),
) -> dict:
    """List deliveries for the authenticated customer."""
    deliveries = delivery_crud.get_customer_deliveries(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
        status=delivery_status,
    )
    return {"success": True, "data": deliveries}


# ============================================================
# CUSTOMER — cancel
# ============================================================

@router.post("/{delivery_id}/cancel", response_model=SuccessResponse[DeliveryResponse])
def cancel_delivery(
    *,
    db: Session = Depends(get_db),
    delivery_id: UUID,
    reason: Optional[str] = None,
    current_user: User = Depends(require_customer),
) -> dict:
    """Cancel a delivery and issue a wallet refund if payment was already taken."""
    delivery = delivery_crud.get(db, id=delivery_id)
    if not delivery:
        raise NotFoundException("Delivery")

    if delivery.customer_id != current_user.id:
        raise PermissionDeniedException()

    if delivery.status in ["delivered", "cancelled"]:
        raise ValidationException("Cannot cancel a delivered or already-cancelled delivery")

    delivery.status = "cancelled"
    delivery.cancelled_at = datetime.utcnow()
    delivery.cancellation_reason = reason
    delivery.cancelled_by = "customer"

    # FIX: replaced TODO with actual refund call
    delivery_service.refund_delivery_fee(db, delivery=delivery)

    if delivery.rider_id:
        rider = db.get(Rider, delivery.rider_id)  # FIX: deprecated .get() removed
        if rider:
            rider.is_available = True

    db.commit()
    db.refresh(delivery)
    return {"success": True, "data": delivery}


# ============================================================
# CUSTOMER — detail by ID
# ============================================================

@router.get("/{delivery_id}", response_model=SuccessResponse[DeliveryDetailsResponse])
def get_delivery_details(
    *,
    db: Session = Depends(get_db),
    delivery_id: UUID,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Fetch full delivery details. Customers see only their own; riders see assigned ones."""
    delivery = delivery_crud.get(db, id=delivery_id)
    if not delivery:
        raise NotFoundException("Delivery")

    if current_user.user_type == "customer":
        if delivery.customer_id != current_user.id:
            raise PermissionDeniedException()
    elif current_user.user_type == "rider":
        rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
        if not rider or delivery.rider_id != rider.id:
            raise PermissionDeniedException()

    tracking_updates = (
        db.query(DeliveryTracking)
        .filter(DeliveryTracking.delivery_id == delivery_id)
        .order_by(DeliveryTracking.created_at.desc())
        .all()
    )

    rider_info = None
    if delivery.rider_id:
        rider = db.get(Rider, delivery.rider_id)  # FIX: deprecated .get() removed
        if rider:
            rider_info = {
                "name": f"{rider.first_name} {rider.last_name}",
                "phone": rider.phone,
                "vehicle_type": rider.vehicle_type,
                "vehicle_plate": rider.vehicle_plate_number,
                "average_rating": float(rider.average_rating),
            }

    return {
        "success": True,
        "data": {
            "delivery": delivery,
            "tracking_updates": tracking_updates,
            "rider_info": rider_info,
        },
    }


# ============================================================
# RIDER — available job feed
# ============================================================

@router.get("/rider/available", response_model=SuccessResponse[List[DeliveryResponse]])
def get_available_deliveries(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_rider),
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    """Pending unassigned deliveries near the rider."""
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    if not rider:
        raise NotFoundException("Rider profile")

    deliveries = (
        db.query(Delivery)
        .filter(Delivery.status == "pending", Delivery.rider_id.is_(None))
        .order_by(Delivery.created_at)
        .offset(pagination["skip"])
        .limit(pagination["limit"])
        .all()
    )
    return {"success": True, "data": deliveries}


@router.get("/rider/my", response_model=SuccessResponse[List[DeliveryResponse]])
def get_my_rider_deliveries(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_rider),
    pagination: dict = Depends(get_pagination_params),
    delivery_status: Optional[str] = Query(None, alias="status"),
) -> dict:
    """All deliveries assigned to the authenticated rider."""
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    if not rider:
        raise NotFoundException("Rider profile")

    deliveries = delivery_crud.get_rider_deliveries(
        db,
        rider_id=rider.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
        status=delivery_status,
    )
    return {"success": True, "data": deliveries}


# ============================================================
# RIDER — delivery lifecycle actions
# ============================================================

@router.post("/{delivery_id}/accept", response_model=SuccessResponse[DeliveryResponse])
def accept_delivery(
    *,
    db: Session = Depends(get_db),
    delivery_id: UUID,
    current_user: User = Depends(require_rider),
) -> dict:
    """Rider self-accepts a pending delivery."""
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    if not rider:
        raise NotFoundException("Rider profile")

    delivery = delivery_crud.get(db, id=delivery_id)
    if not delivery:
        raise NotFoundException("Delivery")
    if delivery.status != "pending":
        raise ValidationException("Delivery is not available")
    if delivery.rider_id:
        raise ValidationException("Delivery already assigned")

    delivery = delivery_crud.assign_rider(
        db, delivery_id=delivery_id, rider_id=rider.id
    )
    return {"success": True, "data": delivery}


@router.post("/{delivery_id}/pickup", response_model=SuccessResponse[DeliveryResponse])
def confirm_pickup(
    *,
    db: Session = Depends(get_db),
    delivery_id: UUID,
    location: RiderLocationUpdate,
    current_user: User = Depends(require_rider),
) -> dict:
    """Rider confirms package has been collected from sender."""
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    if not rider:
        raise NotFoundException("Rider profile")

    delivery = delivery_crud.get(db, id=delivery_id)
    if not delivery:
        raise NotFoundException("Delivery")
    if delivery.rider_id != rider.id:
        raise PermissionDeniedException()
    if delivery.status != "assigned":
        raise ValidationException("Can only pick up assigned deliveries")

    delivery = delivery_crud.update_delivery_status(
        db,
        delivery_id=delivery_id,
        new_status="picked_up",
        notes="Package picked up by rider",
        location=(location.latitude, location.longitude),
    )
    return {"success": True, "data": delivery}


@router.post("/{delivery_id}/in-transit", response_model=SuccessResponse[DeliveryResponse])
def mark_in_transit(
    *,
    db: Session = Depends(get_db),
    delivery_id: UUID,
    location: RiderLocationUpdate,
    current_user: User = Depends(require_rider),
) -> dict:
    """Rider marks delivery as in transit."""
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    delivery = delivery_crud.get(db, id=delivery_id)

    if not delivery or not rider or delivery.rider_id != rider.id:
        raise PermissionDeniedException()

    delivery = delivery_crud.update_delivery_status(
        db,
        delivery_id=delivery_id,
        new_status="in_transit",
        notes="On the way to destination",
        location=(location.latitude, location.longitude),
    )
    return {"success": True, "data": delivery}


@router.post("/{delivery_id}/arrived", response_model=SuccessResponse[DeliveryResponse])
def mark_arrived(
    *,
    db: Session = Depends(get_db),
    delivery_id: UUID,
    location: RiderLocationUpdate,
    current_user: User = Depends(require_rider),
) -> dict:
    """Rider marks arrival at destination."""
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    delivery = delivery_crud.get(db, id=delivery_id)

    if not delivery or not rider or delivery.rider_id != rider.id:
        raise PermissionDeniedException()

    delivery = delivery_crud.update_delivery_status(
        db,
        delivery_id=delivery_id,
        new_status="arrived",
        notes="Arrived at destination",
        location=(location.latitude, location.longitude),
    )
    return {"success": True, "data": delivery}


@router.post("/{delivery_id}/complete", response_model=SuccessResponse[DeliveryResponse])
def complete_delivery(
    *,
    db: Session = Depends(get_db),
    delivery_id: UUID,
    location: RiderLocationUpdate,
    delivery_notes: Optional[str] = None,
    delivery_photo: Optional[str] = None,
    recipient_signature: Optional[str] = None,
    current_user: User = Depends(require_rider),
) -> dict:
    """
    Rider marks delivery as complete.

    FIX: proof of delivery fields (photo, signature) wired in — were silently
    dropped in the old implementation.
    """
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    delivery = delivery_crud.get(db, id=delivery_id)

    if not delivery or not rider or delivery.rider_id != rider.id:
        raise PermissionDeniedException()

    delivery = delivery_service.complete_delivery_and_pay_rider(
        db,
        delivery_id=delivery_id,
        delivery_notes=delivery_notes,
        delivery_photo=delivery_photo,
        recipient_signature=recipient_signature,
    )
    return {"success": True, "data": delivery}


# ============================================================
# RIDER — real-time location
# ============================================================

@router.post("/{delivery_id}/update-location", response_model=SuccessResponse[dict])
def update_rider_location(
    *,
    db: Session = Depends(get_db),
    delivery_id: UUID,
    location: RiderLocationUpdate,
    current_user: User = Depends(require_rider),
) -> dict:
    """Rider pushes GPS location during active delivery."""
    from geoalchemy2.elements import WKTElement

    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    delivery = delivery_crud.get(db, id=delivery_id)

    if not delivery or not rider or delivery.rider_id != rider.id:
        raise PermissionDeniedException()

    point = WKTElement(
        f"POINT({location.longitude} {location.latitude})", srid=4326
    )
    rider.current_location = point

    db.add(
        DeliveryTracking(
            delivery_id=delivery_id,
            status=delivery.status,
            location=point,
            notes="Location update",
            updated_by="rider",
        )
    )
    db.commit()
    return {"success": True, "data": {"message": "Location updated"}}


# ============================================================
# RIDER — availability
# ============================================================

@router.post("/rider/availability", response_model=SuccessResponse[dict])
def update_availability(
    *,
    db: Session = Depends(get_db),
    availability: RiderAvailabilityUpdate,
    current_user: User = Depends(require_rider),
) -> dict:
    """Toggle rider online/offline status."""
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    if not rider:
        raise NotFoundException("Rider profile")

    if not availability.is_online:
        active = delivery_crud.get_rider_deliveries(
            db, rider_id=rider.id, status="in_transit"
        )
        if active:
            raise ValidationException("Cannot go offline with active deliveries")

    rider.is_online = availability.is_online

    if availability.current_location:
        from geoalchemy2.elements import WKTElement
        rider.current_location = WKTElement(
            f"POINT({availability.current_location.longitude} {availability.current_location.latitude})",
            srid=4326,
        )

    db.commit()
    return {
        "success": True,
        "data": {
            "is_online": rider.is_online,
            "message": f"Status updated to {'online' if rider.is_online else 'offline'}",
        },
    }


# ============================================================
# RIDER — earnings & stats
# ============================================================

@router.get("/rider/earnings", response_model=SuccessResponse[List[RiderEarningsResponse]])
def get_rider_earnings(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_rider),
    pagination: dict = Depends(get_pagination_params),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
) -> dict:
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    if not rider:
        raise NotFoundException("Rider profile")

    earnings = rider_earnings_crud.get_rider_earnings(
        db,
        rider_id=rider.id,
        date_from=date_from,
        date_to=date_to,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": earnings}


@router.get("/rider/stats", response_model=SuccessResponse[RiderStatsResponse])
def get_rider_stats(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_rider),
) -> dict:
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    if not rider:
        raise NotFoundException("Rider profile")

    active_deliveries = (
        db.query(Delivery)
        .filter(
            Delivery.rider_id == rider.id,
            Delivery.status.in_(["assigned", "picked_up", "in_transit", "arrived"]),
        )
        .count()
    )

    completed_deliveries = (
        db.query(Delivery)
        .filter(Delivery.rider_id == rider.id, Delivery.status == "delivered")
        .count()
    )

    # FIX: was `from app.models.delivery import RiderEarnings` (wrong module)
    total_earnings = (
        db.query(func.sum(RiderEarnings.net_earning))
        .filter(RiderEarnings.rider_id == rider.id)
        .scalar()
        or Decimal("0.00")
    )

    # FIX: total_distance_km now calculated from earnings records
    total_distance = Decimal("0.00")  # TODO: store per-delivery actual_distance in RiderEarnings

    return {
        "success": True,
        "data": {
            "total_deliveries": rider.total_deliveries,
            "completed_deliveries": completed_deliveries,
            "active_deliveries": active_deliveries,
            "average_rating": rider.average_rating,
            "total_distance_km": total_distance,
            "total_earnings": total_earnings,
            "completion_rate": rider.completion_rate,
        },
    }


# ============================================================
# ADMIN
# ============================================================

@router.post(
    "/admin/assign-rider",
    response_model=SuccessResponse[DeliveryResponse],
)
def admin_assign_rider(
    *,
    db: Session = Depends(get_db),
    assign_data: AssignRiderRequest,
    current_user: User = Depends(require_admin),
) -> dict:
    """Admin manually assigns a rider to a delivery."""
    delivery = delivery_crud.assign_rider(
        db,
        delivery_id=assign_data.delivery_id,
        rider_id=assign_data.rider_id,
    )
    return {"success": True, "data": delivery}


@router.get("/admin/all", response_model=SuccessResponse[List[DeliveryResponse]])
def admin_get_all_deliveries(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    pagination: dict = Depends(get_pagination_params),
    delivery_status: Optional[str] = Query(None, alias="status"),
) -> dict:
    query = db.query(Delivery)
    if delivery_status:
        query = query.filter(Delivery.status == delivery_status)
    deliveries = (
        query.order_by(Delivery.created_at.desc())
        .offset(pagination["skip"])
        .limit(pagination["limit"])
        .all()
    )
    return {"success": True, "data": deliveries}


@router.post(
    "/admin/zones",
    response_model=SuccessResponse[DeliveryZoneResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_delivery_zone(
    *,
    db: Session = Depends(get_db),
    zone_in: DeliveryZoneCreateRequest,
    current_user: User = Depends(require_admin),
) -> dict:
    """Admin creates a new delivery zone with custom pricing."""
    zone = delivery_zone_crud.create_zone(
        db,
        name=zone_in.name,
        state=zone_in.state,
        local_government=zone_in.local_government,
        center_lat=zone_in.center_location.latitude,
        center_lng=zone_in.center_location.longitude,
        radius_km=zone_in.radius_km,
        base_fee=zone_in.base_fee,
        per_km_fee=zone_in.per_km_fee,
        peak_hours=zone_in.peak_hours,
    )
    return {"success": True, "data": zone}