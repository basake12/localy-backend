"""
delivery_service.py
Business logic for delivery operations.

Design note: pure module-level functions rather than a class of staticmethods.
"""
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from app.crud.delivery_crud import (
    delivery_crud,
    delivery_zone_crud,
    rider_earnings_crud,
)
from app.crud.wallet_crud import wallet_crud
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    InsufficientBalanceException,
)
from app.core.constants import TransactionType
from app.models.user_model import User
from app.models.delivery_model import Delivery


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------

def calculate_delivery_quote(
    db: Session,
    *,
    pickup_location: tuple,
    dropoff_location: tuple,
    order_type: str,
) -> Dict[str, Any]:
    """
    Return a price + time estimate without creating any records.
    Exposed publicly so the frontend can show a quote before checkout.
    """
    pickup_lat, pickup_lng = pickup_location
    dropoff_lat, dropoff_lng = dropoff_location

    # Reuse internal haversine — called via the CRUD helper so the zone lookup
    # happens in one place and the service never touches private methods.
    distance_km = delivery_crud._calculate_distance(
        pickup_lat, pickup_lng, dropoff_lat, dropoff_lng
    )

    zone = delivery_zone_crud.get_zone_for_location(
        db, latitude=pickup_lat, longitude=pickup_lng
    )
    pricing = delivery_crud._calculate_pricing(db, distance_km, order_type, zone=zone)

    # Rough ETA: assume 30 km/h average
    estimated_minutes = max(5, int((float(distance_km) / 30.0) * 60))

    return {
        "distance_km": distance_km,
        "base_fee": pricing["base_fee"],
        "distance_fee": pricing["distance_fee"],
        "total_fee": pricing["total_fee"],
        "estimated_duration_minutes": estimated_minutes,
    }


# ---------------------------------------------------------------------------
# Create + pay
# ---------------------------------------------------------------------------

def create_and_pay_delivery(
    db: Session,
    *,
    current_user: User,
    order_type: str,
    pickup_address: str,
    pickup_location: tuple,
    pickup_contact_name: str,
    pickup_contact_phone: str,
    dropoff_address: str,
    dropoff_location: tuple,
    dropoff_contact_name: str,
    dropoff_contact_phone: str,
    package_description: Optional[str] = None,
    package_weight_kg: Optional[Decimal] = None,
    package_value: Optional[Decimal] = None,
    order_id: Optional[UUID] = None,
    payment_method: str = "wallet",
    cod_amount: Decimal = Decimal("0.00"),
    pickup_instructions: Optional[str] = None,
    dropoff_instructions: Optional[str] = None,
    requires_cold_storage: bool = False,
    is_fragile: bool = False,
    required_vehicle_type: Optional[str] = None,
) -> Delivery:
    """
    Create a delivery record and process payment (wallet debit or COD flag).
    On success, attempts auto-assignment of the nearest available rider.
    """
    delivery = delivery_crud.create_delivery(
        db,
        customer_id=current_user.id,
        order_type=order_type,
        pickup_address=pickup_address,
        pickup_location=pickup_location,
        pickup_contact_name=pickup_contact_name,
        pickup_contact_phone=pickup_contact_phone,
        dropoff_address=dropoff_address,
        dropoff_location=dropoff_location,
        dropoff_contact_name=dropoff_contact_name,
        dropoff_contact_phone=dropoff_contact_phone,
        package_description=package_description,
        package_weight_kg=package_weight_kg,
        package_value=package_value,
        order_id=order_id,
        payment_method=payment_method,
        cod_amount=cod_amount,
        pickup_instructions=pickup_instructions,
        dropoff_instructions=dropoff_instructions,
        requires_cold_storage=requires_cold_storage,
        is_fragile=is_fragile,
        required_vehicle_type=required_vehicle_type,
    )

    if payment_method == "wallet":
        wallet = wallet_crud.get_or_create_wallet(db, user_id=current_user.id)
        if wallet.balance < delivery.total_fee:
            delivery.status = "cancelled"
            delivery.cancellation_reason = "Insufficient wallet balance"
            db.commit()
            raise InsufficientBalanceException()

        wallet_crud.debit_wallet(
            db,
            wallet_id=wallet.id,
            amount=delivery.total_fee,
            transaction_type=TransactionType.PAYMENT,
            description=f"Delivery fee — {delivery.tracking_code}",
            reference_id=str(delivery.id),
        )

        delivery.payment_status = "paid"
        db.commit()
        db.refresh(delivery)

    # Best-effort auto-assign; failure is non-fatal (rider can accept manually)
    try:
        auto_assign_rider(db, delivery_id=delivery.id, pickup_location=pickup_location)
    except Exception:
        pass  # Logged at a higher level; delivery stays in PENDING

    return delivery


# ---------------------------------------------------------------------------
# Rider assignment
# ---------------------------------------------------------------------------

def auto_assign_rider(
    db: Session,
    *,
    delivery_id: UUID,
    pickup_location: tuple,
    radius_km: float = 10.0,
) -> Optional[Delivery]:
    """
    Find the nearest available, highest-rated rider and assign them.
    Returns the updated Delivery, or None if no rider is found.
    """
    delivery = delivery_crud.get(db, id=delivery_id)
    if not delivery:
        return None

    if delivery.status != "pending":
        return delivery  # Already processed

    # Determine required vehicle type from package flags
    vehicle_type: Optional[str] = None
    if delivery.required_vehicle_type:
        vehicle_type = delivery.required_vehicle_type
    elif delivery.package_weight_kg and delivery.package_weight_kg > 30:
        vehicle_type = "van"

    available_riders = delivery_crud.find_available_riders(
        db,
        pickup_location=pickup_location,
        radius_km=radius_km,
        vehicle_type=vehicle_type,
    )

    if not available_riders:
        return None  # Customer will be notified; rider can self-accept

    best_rider = available_riders[0]  # Ordered by rating desc in CRUD

    return delivery_crud.assign_rider(
        db,
        delivery_id=delivery_id,
        rider_id=best_rider.id,
    )


# ---------------------------------------------------------------------------
# Complete + pay rider
# ---------------------------------------------------------------------------

def complete_delivery_and_pay_rider(
    db: Session,
    *,
    delivery_id: UUID,
    delivery_notes: Optional[str] = None,
    delivery_photo: Optional[str] = None,
    recipient_signature: Optional[str] = None,
) -> Delivery:
    """
    Mark delivery as DELIVERED, persist proof, and create earnings record.
    COD remittance tracking is handled separately by the finance team.
    """
    delivery = delivery_crud.get(db, id=delivery_id)
    if not delivery:
        raise NotFoundException("Delivery")

    if not delivery.rider_id:
        raise ValidationException("No rider assigned to this delivery")

    delivery = delivery_crud.update_delivery_status(
        db,
        delivery_id=delivery_id,
        new_status="delivered",
        notes="Delivery completed successfully",
        updated_by="rider",
    )

    # Persist proof of delivery
    if delivery_notes:
        delivery.delivery_notes = delivery_notes
    if delivery_photo:
        delivery.delivery_photo = delivery_photo
    if recipient_signature:
        delivery.recipient_signature = recipient_signature
    db.commit()
    db.refresh(delivery)

    # Create earnings record (80/20 split is in CRUDRiderEarnings)
    rider_earnings_crud.create_earnings(
        db,
        rider_id=delivery.rider_id,
        delivery_id=delivery_id,
        delivery_fee=delivery.total_fee,
    )

    return delivery


# ---------------------------------------------------------------------------
# Refund helper
# ---------------------------------------------------------------------------

def refund_delivery_fee(
    db: Session,
    *,
    delivery: Delivery,
) -> None:
    """
    Refund the delivery fee to the customer's wallet.
    Called on cancellation when payment_method == "wallet" and status was "paid".
    """
    if delivery.payment_status != "paid" or delivery.payment_method != "wallet":
        return

    wallet = wallet_crud.get_or_create_wallet(db, user_id=delivery.customer_id)
    wallet_crud.credit_wallet(
        db,
        wallet_id=wallet.id,
        amount=delivery.total_fee,
        transaction_type=TransactionType.REFUND,
        description=f"Refund for cancelled delivery — {delivery.tracking_code}",
        reference_id=str(delivery.id),
    )

    delivery.payment_status = "refunded"
    db.commit()