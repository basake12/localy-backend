from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
from datetime import datetime

from app.core.database import get_db
from app.dependencies import (
    get_current_active_user,
    require_customer,
    require_rider,
    require_admin,
    get_pagination_params
)
from app.schemas.common import SuccessResponse
from app.schemas.delivery import (
    DeliveryCreateRequest,
    DeliveryResponse,
    DeliveryListResponse,
    DeliveryDetailsResponse,
    DeliveryTrackingResponse,
    RiderLocationUpdate,
    RiderAvailabilityUpdate,
    RiderStatsResponse,
    RiderEarningsResponse,
    AssignRiderRequest,
    DeliveryZoneCreateRequest,
    DeliveryZoneResponse
)
from app.services.delivery_service import delivery_service
from app.crud.delivery import (
    delivery_crud,
    delivery_zone_crud,
    rider_earnings_crud
)
from app.crud.user import user_crud
from app.models.user import User
from app.models.rider import Rider
from app.models.delivery import DeliveryTracking
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException
)

router = APIRouter()


# ============================================
# DELIVERY QUOTE (PUBLIC/CUSTOMER)
# ============================================

@router.post("/quote", response_model=SuccessResponse[dict])
def get_delivery_quote(
        *,
        db: Session = Depends(get_db),
        pickup_lat: float = Query(..., ge=-90, le=90),
        pickup_lng: float = Query(..., ge=-180, le=180),
        dropoff_lat: float = Query(..., ge=-90, le=90),
        dropoff_lng: float = Query(..., ge=-180, le=180),
        order_type: str = Query(...)
) -> dict:
    """
    Get delivery price quote

    - Public endpoint
    - Calculate distance and pricing
    - Estimate delivery time
    """
    quote = delivery_service.calculate_delivery_quote(
        db,
        pickup_location=(pickup_lat, pickup_lng),
        dropoff_location=(dropoff_lat, dropoff_lng),
        order_type=order_type
    )

    return {
        "success": True,
        "data": quote
    }


# ============================================
# DELIVERY CREATION (CUSTOMER)
# ============================================

@router.post("/", response_model=SuccessResponse[DeliveryResponse], status_code=status.HTTP_201_CREATED)
def create_delivery(
        *,
        db: Session = Depends(get_db),
        delivery_in: DeliveryCreateRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Create a new delivery

    - Only for customer accounts
    - Calculates pricing
    - Processes payment
    - Auto-assigns rider if available
    """
    delivery = delivery_service.create_and_pay_delivery(
        db,
        current_user=current_user,
        order_type=delivery_in.order_type,
        pickup_address=delivery_in.pickup_address,
        pickup_location=(
            delivery_in.pickup_location.latitude,
            delivery_in.pickup_location.longitude
        ),
        pickup_contact_name=delivery_in.pickup_contact_name,
        pickup_contact_phone=delivery_in.pickup_contact_phone,
        dropoff_address=delivery_in.dropoff_address,
        dropoff_location=(
            delivery_in.dropoff_location.latitude,
            delivery_in.dropoff_location.longitude
        ),
        dropoff_contact_name=delivery_in.dropoff_contact_name,
        dropoff_contact_phone=delivery_in.dropoff_contact_phone,
        package_description=delivery_in.package_description,
        package_weight_kg=delivery_in.package_weight_kg,
        order_id=delivery_in.order_id,
        payment_method=delivery_in.payment_method,
        cod_amount=delivery_in.cod_amount
    )

    return {
        "success": True,
        "data": delivery
    }


# ============================================
# DELIVERY TRACKING (PUBLIC)
# ============================================

@router.get("/track/{tracking_code}", response_model=SuccessResponse[DeliveryDetailsResponse])
def track_delivery(
        *,
        db: Session = Depends(get_db),
        tracking_code: str
) -> dict:
    """
    Track delivery by tracking code

    - Public endpoint
    - Returns delivery details and tracking updates
    """
    delivery = delivery_crud.get_by_tracking_code(db, tracking_code=tracking_code)
    if not delivery:
        raise NotFoundException("Delivery not found")

    # Get tracking updates
    tracking_updates = db.query(DeliveryTracking).filter(
        DeliveryTracking.delivery_id == delivery.id
    ).order_by(DeliveryTracking.created_at.desc()).all()

    # Get rider info if assigned
    rider_info = None
    if delivery.rider_id:
        rider = db.query(Rider).get(delivery.rider_id)
        if rider:
            rider_info = {
                "name": f"{rider.first_name} {rider.last_name}",
                "phone": rider.phone,
                "vehicle_type": rider.vehicle_type,
                "vehicle_plate": rider.vehicle_plate_number,
                "average_rating": float(rider.average_rating)
            }

    return {
        "success": True,
        "data": {
            "delivery": delivery,
            "tracking_updates": tracking_updates,
            "rider_info": rider_info
        }
    }


# ============================================
# CUSTOMER DELIVERY MANAGEMENT
# ============================================

@router.get("/my", response_model=SuccessResponse[List[DeliveryListResponse]])
def get_my_deliveries(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_customer),
        pagination: dict = Depends(get_pagination_params),
        status: Optional[str] = Query(None)
) -> dict:
    """Get current customer's deliveries"""
    deliveries = delivery_crud.get_customer_deliveries(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
        status=status
    )

    # Transform to list response
    delivery_list = []
    for delivery in deliveries:
        delivery_list.append({
            "id": delivery.id,
            "tracking_code": delivery.tracking_code,
            "order_type": delivery.order_type,
            "dropoff_address": delivery.dropoff_address,
            "total_fee": delivery.total_fee,
            "status": delivery.status,
            "created_at": delivery.created_at
        })

    return {
        "success": True,
        "data": delivery_list
    }


@router.get("/{delivery_id}", response_model=SuccessResponse[DeliveryDetailsResponse])
def get_delivery_details(
        *,
        db: Session = Depends(get_db),
        delivery_id: UUID,
        current_user: User = Depends(get_current_active_user)
) -> dict:
    """Get delivery details"""
    delivery = delivery_crud.get(db, id=delivery_id)
    if not delivery:
        raise NotFoundException("Delivery")

    # Verify permission
    if current_user.user_type == "customer":
        if delivery.customer_id != current_user.id:
            raise PermissionDeniedException()
    elif current_user.user_type == "rider":
        rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
        if not rider or delivery.rider_id != rider.id:
            raise PermissionDeniedException()

    # Get tracking updates
    tracking_updates = db.query(DeliveryTracking).filter(
        DeliveryTracking.delivery_id == delivery_id
    ).order_by(DeliveryTracking.created_at.desc()).all()

    # Get rider info
    rider_info = None
    if delivery.rider_id:
        rider = db.query(Rider).get(delivery.rider_id)
        if rider:
            rider_info = {
                "name": f"{rider.first_name} {rider.last_name}",
                "phone": rider.phone,
                "vehicle_type": rider.vehicle_type,
                "average_rating": float(rider.average_rating)
            }

    return {
        "success": True,
        "data": {
            "delivery": delivery,
            "tracking_updates": tracking_updates,
            "rider_info": rider_info
        }
    }


@router.post("/{delivery_id}/cancel", response_model=SuccessResponse[DeliveryResponse])
def cancel_delivery(
        *,
        db: Session = Depends(get_db),
        delivery_id: UUID,
        reason: Optional[str] = None,
        current_user: User = Depends(require_customer)
) -> dict:
    """Cancel a delivery"""
    delivery = delivery_crud.get(db, id=delivery_id)
    if not delivery:
        raise NotFoundException("Delivery")

    # Verify ownership
    if delivery.customer_id != current_user.id:
        raise PermissionDeniedException()

    if delivery.status in ["delivered", "cancelled"]:
        raise ValidationException("Cannot cancel delivered or already cancelled delivery")

    # Cancel delivery
    delivery.status = "cancelled"
    delivery.cancelled_at = datetime.utcnow()
    delivery.cancellation_reason = reason
    delivery.cancelled_by = "customer"

    # Make rider available if assigned
    if delivery.rider_id:
        rider = db.query(Rider).get(delivery.rider_id)
        if rider:
            rider.is_available = True

    # TODO: Process refund

    db.commit()
    db.refresh(delivery)

    return {
        "success": True,
        "data": delivery
    }


# ============================================
# RIDER DELIVERY MANAGEMENT
# ============================================

@router.get("/rider/available", response_model=SuccessResponse[List[DeliveryResponse]])
def get_available_deliveries(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_rider),
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """
    Get available deliveries for rider

    - Shows pending deliveries near rider
    """
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    if not rider:
        raise NotFoundException("Rider profile")

    # Get pending deliveries
    deliveries = db.query(Delivery).filter(
        Delivery.status == "pending",
        Delivery.rider_id == None
    ).order_by(
        Delivery.created_at
    ).offset(pagination["skip"]).limit(pagination["limit"]).all()

    return {
        "success": True,
        "data": deliveries
    }


@router.get("/rider/my", response_model=SuccessResponse[List[DeliveryResponse]])
def get_my_rider_deliveries(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_rider),
        pagination: dict = Depends(get_pagination_params),
        status: Optional[str] = Query(None)
) -> dict:
    """Get current rider's deliveries"""
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    if not rider:
        raise NotFoundException("Rider profile")

    deliveries = delivery_crud.get_rider_deliveries(
        db,
        rider_id=rider.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
        status=status
    )

    return {
        "success": True,
        "data": deliveries
    }


@router.post("/{delivery_id}/accept", response_model=SuccessResponse[DeliveryResponse])
def accept_delivery(
        *,
        db: Session = Depends(get_db),
        delivery_id: UUID,
        current_user: User = Depends(require_rider)
) -> dict:
    """
    Accept delivery (rider action)
    """
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

    # Assign to rider
    delivery = delivery_crud.assign_rider(
        db,
        delivery_id=delivery_id,
        rider_id=rider.id
    )

    return {
        "success": True,
        "data": delivery
    }


@router.post("/{delivery_id}/pickup", response_model=SuccessResponse[DeliveryResponse])
def confirm_pickup(
        *,
        db: Session = Depends(get_db),
        delivery_id: UUID,
        location: RiderLocationUpdate,
        current_user: User = Depends(require_rider)
) -> dict:
    """
    Confirm package pickup (rider action)
    """
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    if not rider:
        raise NotFoundException("Rider profile")

    delivery = delivery_crud.get(db, id=delivery_id)
    if not delivery:
        raise NotFoundException("Delivery")

    if delivery.rider_id != rider.id:
        raise PermissionDeniedException()

    if delivery.status != "assigned":
        raise ValidationException("Can only pickup assigned deliveries")

    # Update status
    delivery = delivery_crud.update_delivery_status(
        db,
        delivery_id=delivery_id,
        new_status="picked_up",
        notes="Package picked up by rider",
        location=(location.latitude, location.longitude)
    )

    return {
        "success": True,
        "data": delivery
    }


@router.post("/{delivery_id}/in-transit", response_model=SuccessResponse[DeliveryResponse])
def mark_in_transit(
        *,
        db: Session = Depends(get_db),
        delivery_id: UUID,
        location: RiderLocationUpdate,
        current_user: User = Depends(require_rider)
) -> dict:
    """
    Mark delivery as in transit (rider action)
    """
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    delivery = delivery_crud.get(db, id=delivery_id)

    if not delivery or delivery.rider_id != rider.id:
        raise PermissionDeniedException()

    delivery = delivery_crud.update_delivery_status(
        db,
        delivery_id=delivery_id,
        new_status="in_transit",
        notes="On the way to destination",
        location=(location.latitude, location.longitude)
    )

    return {
        "success": True,
        "data": delivery
    }


@router.post("/{delivery_id}/arrived", response_model=SuccessResponse[DeliveryResponse])
def mark_arrived(
        *,
        db: Session = Depends(get_db),
        delivery_id: UUID,
        location: RiderLocationUpdate,
        current_user: User = Depends(require_rider)
) -> dict:
    """
    Mark arrived at destination (rider action)
    """
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    delivery = delivery_crud.get(db, id=delivery_id)

    if not delivery or delivery.rider_id != rider.id:
        raise PermissionDeniedException()

    delivery = delivery_crud.update_delivery_status(
        db,
        delivery_id=delivery_id,
        new_status="arrived",
        notes="Arrived at destination",
        location=(location.latitude, location.longitude)
    )

    return {
        "success": True,
        "data": delivery
    }


@router.post("/{delivery_id}/complete", response_model=SuccessResponse[DeliveryResponse])
def complete_delivery(
        *,
        db: Session = Depends(get_db),
        delivery_id: UUID,
        location: RiderLocationUpdate,
        delivery_notes: Optional[str] = None,
        current_user: User = Depends(require_rider)
) -> dict:
    """
    Complete delivery (rider action)

    - Marks delivery as delivered
    - Creates rider earnings
    - Makes rider available
    """
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    delivery = delivery_crud.get(db, id=delivery_id)

    if not delivery or delivery.rider_id != rider.id:
        raise PermissionDeniedException()

    # Complete delivery and create earnings
    delivery = delivery_service.complete_delivery_and_pay_rider(
        db,
        delivery_id=delivery_id
    )

    # Add delivery notes
    if delivery_notes:
        delivery.delivery_notes = delivery_notes
        db.commit()

    return {
        "success": True,
        "data": delivery
    }


@router.post("/{delivery_id}/update-location", response_model=SuccessResponse[dict])
def update_rider_location(
        *,
        db: Session = Depends(get_db),
        delivery_id: UUID,
        location: RiderLocationUpdate,
        current_user: User = Depends(require_rider)
) -> dict:
    """
    Update rider location during delivery

    - Real-time location tracking
    """
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    delivery = delivery_crud.get(db, id=delivery_id)

    if not delivery or delivery.rider_id != rider.id:
        raise PermissionDeniedException()

    # Update rider location
    from geoalchemy2.elements import WKTElement
    rider.current_location = WKTElement(
        f"POINT({location.longitude} {location.latitude})",
        srid=4326
    )

    # Create tracking update
    tracking = DeliveryTracking(
        delivery_id=delivery_id,
        status=delivery.status,
        location=WKTElement(
            f"POINT({location.longitude} {location.latitude})",
            srid=4326
        ),
        notes="Location update",
        updated_by="rider"
    )
    db.add(tracking)
    db.commit()

    return {
        "success": True,
        "data": {"message": "Location updated"}
    }


# ============================================
# RIDER AVAILABILITY MANAGEMENT
# ============================================

@router.post("/rider/availability", response_model=SuccessResponse[dict])
def update_availability(
        *,
        db: Session = Depends(get_db),
        availability: RiderAvailabilityUpdate,
        current_user: User = Depends(require_rider)
) -> dict:
    """
    Update rider online/offline status
    """
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    if not rider:
        raise NotFoundException("Rider profile")

    rider.is_online = availability.is_online

    # Update location if provided
    if availability.current_location:
        from geoalchemy2.elements import WKTElement
        rider.current_location = WKTElement(
            f"POINT({availability.current_location.longitude} {availability.current_location.latitude})",
            srid=4326
        )

    # If going offline, make sure no active deliveries
    if not availability.is_online:
        active_deliveries = delivery_crud.get_rider_deliveries(
            db,
            rider_id=rider.id,
            status="in_transit"
        )
        if active_deliveries:
            raise ValidationException("Cannot go offline with active deliveries")

    db.commit()

    return {
        "success": True,
        "data": {
            "is_online": rider.is_online,
            "message": f"Status updated to {'online' if rider.is_online else 'offline'}"
        }
    }


# ============================================
# RIDER EARNINGS
# ============================================

@router.get("/rider/earnings", response_model=SuccessResponse[List[RiderEarningsResponse]])
def get_rider_earnings(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_rider),
        pagination: dict = Depends(get_pagination_params),
        date_from: Optional[datetime] = Query(None),
        date_to: Optional[datetime] = Query(None)
) -> dict:
    """Get rider earnings"""
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    if not rider:
        raise NotFoundException("Rider profile")

    earnings = rider_earnings_crud.get_rider_earnings(
        db,
        rider_id=rider.id,
        date_from=date_from,
        date_to=date_to,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": earnings
    }


@router.get("/rider/stats", response_model=SuccessResponse[RiderStatsResponse])
def get_rider_stats(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_rider)
) -> dict:
    """Get rider statistics"""
    rider = db.query(Rider).filter(Rider.user_id == current_user.id).first()
    if not rider:
        raise NotFoundException("Rider profile")

    # Get active deliveries count
    active_deliveries = db.query(Delivery).filter(
        Delivery.rider_id == rider.id,
        Delivery.status.in_(["assigned", "picked_up", "in_transit", "arrived"])
    ).count()

    # Get completed deliveries
    completed_deliveries = db.query(Delivery).filter(
        Delivery.rider_id == rider.id,
        Delivery.status == "delivered"
    ).count()

    # Get total earnings
    from sqlalchemy import func
    from app.models.delivery import RiderEarnings

    total_earnings = db.query(
        func.sum(RiderEarnings.net_earning)
    ).filter(
        RiderEarnings.rider_id == rider.id
    ).scalar() or Decimal('0.00')

    return {
        "success": True,
        "data": {
            "total_deliveries": rider.total_deliveries,
            "completed_deliveries": completed_deliveries,
            "active_deliveries": active_deliveries,
            "average_rating": rider.average_rating,
            "total_distance_km": Decimal('0.00'),  # TODO: Calculate
            "total_earnings": total_earnings,
            "completion_rate": rider.completion_rate
        }
    }


# ============================================
# ADMIN ENDPOINTS
# ============================================

@router.post("/admin/assign-rider", response_model=SuccessResponse[DeliveryResponse])
def admin_assign_rider(
        *,
        db: Session = Depends(get_db),
        assign_data: AssignRiderRequest,
        current_user: User = Depends(require_admin)
) -> dict:
    """Admin assign rider to delivery"""
    delivery = delivery_crud.assign_rider(
        db,
        delivery_id=assign_data.delivery_id,
        rider_id=assign_data.rider_id
    )

    return {
        "success": True,
        "data": delivery
    }


@router.get("/admin/all", response_model=SuccessResponse[List[DeliveryResponse]])
def admin_get_all_deliveries(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_admin),
        pagination: dict = Depends(get_pagination_params),
        status: Optional[str] = Query(None)
) -> dict:
    """Admin get all deliveries"""
    query = db.query(Delivery)

    if status:
        query = query.filter(Delivery.status == status)

    deliveries = query.order_by(
        Delivery.created_at.desc()
    ).offset(pagination["skip"]).limit(pagination["limit"]).all()

    return {
        "success": True,
        "data": deliveries
    }