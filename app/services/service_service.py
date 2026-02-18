from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import date, time, datetime
from decimal import Decimal

from app.crud.services import (
    service_provider_crud,
    service_crud,
    service_availability_crud,
    service_booking_crud
)
from app.crud.wallet import wallet_crud
from app.crud.business import business_crud
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    InsufficientBalanceException,
    BookingNotAvailableException
)
from app.core.constants import TransactionType
from app.models.user import User
from app.models.services import ServiceBooking


class ServiceService:
    """Business logic for service operations"""

    @staticmethod
    def search_services(
            db: Session,
            *,
            query_text: Optional[str] = None,
            category: Optional[str] = None,
            subcategory: Optional[str] = None,
            min_price: Optional[Decimal] = None,
            max_price: Optional[Decimal] = None,
            location: Optional[tuple] = None,
            radius_km: float = 10.0,
            service_location_type: Optional[str] = None,
            sort_by: str = "created_at",
            skip: int = 0,
            limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Search services with provider info"""
        services = service_crud.search_services(
            db,
            query_text=query_text,
            category=category,
            subcategory=subcategory,
            min_price=min_price,
            max_price=max_price,
            location=location,
            radius_km=radius_km,
            service_location_type=service_location_type,
            sort_by=sort_by,
            skip=skip,
            limit=limit
        )

        # Enrich with provider and business info
        results = []
        for service in services:
            provider = service_provider_crud.get(db, id=service.provider_id)
            business = business_crud.get(db, id=provider.business_id) if provider else None

            results.append({
                "service": service,
                "provider": provider,
                "business": business
            })

        return results

    @staticmethod
    def get_service_details(
            db: Session,
            *,
            service_id: UUID
    ) -> Dict[str, Any]:
        """Get full service details"""
        service = service_crud.get(db, id=service_id)
        if not service:
            raise NotFoundException("Service")

        provider = service_provider_crud.get(db, id=service.provider_id)
        business = business_crud.get(db, id=provider.business_id) if provider else None

        return {
            "service": service,
            "provider": provider,
            "business": business
        }

    @staticmethod
    def get_available_slots(
            db: Session,
            *,
            service_id: UUID,
            booking_date: date
    ) -> List[Dict[str, Any]]:
        """Get available time slots for a service on a specific date"""
        service = service_crud.get(db, id=service_id)
        if not service:
            raise NotFoundException("Service")

        slots = service_availability_crud.get_available_slots(
            db,
            provider_id=service.provider_id,
            service_duration=service.duration_minutes or 60,
            booking_date=booking_date
        )

        return slots

    @staticmethod
    def book_and_pay(
            db: Session,
            *,
            current_user: User,
            service_id: UUID,
            booking_date: date,
            booking_time: time,
            number_of_people: int,
            service_location_type: str,
            service_address: Optional[str],
            selected_options: List[Dict],
            special_requests: Optional[str],
            payment_method: str
    ) -> ServiceBooking:
        """
        Create booking and process payment

        Currently supports wallet payment only
        """
        # Create booking
        booking = service_booking_crud.create_booking(
            db,
            service_id=service_id,
            customer_id=current_user.id,
            booking_date=booking_date,
            booking_time=booking_time,
            number_of_people=number_of_people,
            service_location_type=service_location_type,
            service_address=service_address,
            selected_options=selected_options,
            special_requests=special_requests
        )

        # Process payment
        if payment_method == "wallet":
            # Get customer wallet
            wallet = wallet_crud.get_or_create_wallet(db, user_id=current_user.id)

            # Check balance
            if wallet.balance < booking.total_price:
                # Cancel booking
                booking.status = "cancelled"
                db.commit()
                raise InsufficientBalanceException()

            # Debit wallet
            wallet_crud.debit_wallet(
                db,
                wallet_id=wallet.id,
                amount=booking.total_price,
                transaction_type=TransactionType.PAYMENT,
                description=f"Payment for service booking {booking.id}",
                reference_id=str(booking.id)
            )

            # Update booking status
            booking.payment_status = "paid"
            booking.status = "confirmed"
            booking.payment_reference = str(booking.id)
            db.commit()
            db.refresh(booking)

        return booking

    @staticmethod
    def calculate_booking_price(
            db: Session,
            *,
            service_id: UUID,
            selected_options: List[Dict],
            service_location_type: str
    ) -> Dict[str, Decimal]:
        """Calculate total booking price with breakdown"""
        service = service_crud.get(db, id=service_id)
        if not service:
            raise NotFoundException("Service")

        provider = service_provider_crud.get(db, id=service.provider_id)

        base_price = service.base_price
        add_ons_price = Decimal('0.00')

        # Calculate add-ons
        for option in selected_options:
            if 'price' in option:
                add_ons_price += Decimal(str(option['price']))

        # Calculate travel fee
        travel_fee = Decimal('0.00')
        if service_location_type == "in_home":
            travel_fee = provider.travel_fee

        total_price = base_price + add_ons_price + travel_fee

        return {
            "base_price": base_price,
            "add_ons_price": add_ons_price,
            "travel_fee": travel_fee,
            "total_price": total_price
        }


service_service = ServiceService()