from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from app.crud.delivery_crud import (
    delivery_crud,
    delivery_zone_crud,
    rider_earnings_crud
)
from app.crud.wallet_crud import wallet_crud
from app.crud.user_crud import user_crud
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    InsufficientBalanceException
)
from app.core.constants import TransactionType
from app.models.user_model import User
from app.models.delivery_model import Delivery


class DeliveryService:
    """Business logic for delivery operations"""

    @staticmethod
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
            order_id: Optional[UUID] = None,
            payment_method: str = "wallet",
            cod_amount: Decimal = Decimal('0.00')
    ) -> Delivery:
        """
        Create delivery and process payment
        """
        # Create delivery
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
            order_id=order_id,
            payment_method=payment_method,
            cod_amount=cod_amount
        )

        # Process payment if not COD
        if payment_method == "wallet":
            wallet = wallet_crud.get_or_create_wallet(db, user_id=current_user.id)

            # Check balance
            if wallet.balance < delivery.total_fee:
                # Cancel delivery
                delivery.status = "cancelled"
                delivery.cancellation_reason = "Insufficient wallet balance"
                db.commit()
                raise InsufficientBalanceException()

            # Debit wallet
            wallet_crud.debit_wallet(
                db,
                wallet_id=wallet.id,
                amount=delivery.total_fee,
                transaction_type=TransactionType.PAYMENT,
                description=f"Delivery fee for {delivery.tracking_code}",
                reference_id=str(delivery.id)
            )

            # Update payment status
            delivery.payment_status = "paid"
            db.commit()
            db.refresh(delivery)

        # Auto-assign rider
        DeliveryService.auto_assign_rider(db, delivery_id=delivery.id)

        return delivery

    @staticmethod
    def auto_assign_rider(
            db: Session,
            *,
            delivery_id: UUID
    ) -> Optional[Delivery]:
        """
        Automatically find and assign available rider
        """
        delivery = delivery_crud.get(db, id=delivery_id)
        if not delivery:
            return None

        # Get pickup location
        # Note: In production, extract coordinates from Geography field
        # For now, we'll skip auto-assignment in this implementation
        # TODO: Implement automatic rider assignment algorithm

        return delivery

    @staticmethod
    def calculate_delivery_quote(
            db: Session,
            *,
            pickup_location: tuple,
            dropoff_location: tuple,
            order_type: str
    ) -> Dict[str, Any]:
        """Calculate delivery quote before creating delivery"""
        pickup_lat, pickup_lng = pickup_location
        dropoff_lat, dropoff_lng = dropoff_location

        # Calculate distance
        distance_km = delivery_crud._calculate_distance(
            pickup_lat, pickup_lng,
            dropoff_lat, dropoff_lng
        )

        # Calculate pricing
        pricing = delivery_crud._calculate_pricing(db, distance_km, order_type)

        # Estimate time (rough estimate: 30km/h average speed)
        estimated_minutes = int((float(distance_km) / 30.0) * 60)

        return {
            "distance_km": distance_km,
            "base_fee": pricing['base_fee'],
            "distance_fee": pricing['distance_fee'],
            "total_fee": pricing['total_fee'],
            "estimated_duration_minutes": estimated_minutes
        }

    @staticmethod
    def complete_delivery_and_pay_rider(
            db: Session,
            *,
            delivery_id: UUID
    ) -> Delivery:
        """Complete delivery and create rider earnings"""
        delivery = delivery_crud.get(db, id=delivery_id)
        if not delivery:
            raise NotFoundException("Delivery")

        if not delivery.rider_id:
            raise ValidationException("No rider assigned")

        # Update status
        delivery = delivery_crud.update_delivery_status(
            db,
            delivery_id=delivery_id,
            new_status="delivered",
            notes="Delivery completed successfully"
        )

        # Create rider earnings
        rider_earnings_crud.create_earnings(
            db,
            rider_id=delivery.rider_id,
            delivery_id=delivery_id,
            delivery_fee=delivery.total_fee
        )

        return delivery


delivery_service = DeliveryService()