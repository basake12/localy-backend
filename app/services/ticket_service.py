from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import date
from decimal import Decimal

from app.crud.tickets import (
    ticket_event_crud,
    ticket_tier_crud,
    ticket_booking_crud
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
from app.models.tickets import TicketBooking


class TicketService:
    """Business logic for ticket operations"""

    @staticmethod
    def search_events(
            db: Session,
            *,
            query_text: Optional[str] = None,
            event_type: Optional[str] = None,
            category: Optional[str] = None,
            location: Optional[tuple] = None,
            radius_km: float = 50.0,
            event_date_from: Optional[date] = None,
            event_date_to: Optional[date] = None,
            origin_city: Optional[str] = None,
            destination_city: Optional[str] = None,
            departure_date: Optional[date] = None,
            transport_type: Optional[str] = None,
            available_only: bool = True,
            is_featured: Optional[bool] = None,
            skip: int = 0,
            limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Search events with business info"""
        events = ticket_event_crud.search_events(
            db,
            query_text=query_text,
            event_type=event_type,
            category=category,
            location=location,
            radius_km=radius_km,
            event_date_from=event_date_from,
            event_date_to=event_date_to,
            origin_city=origin_city,
            destination_city=destination_city,
            departure_date=departure_date,
            transport_type=transport_type,
            available_only=available_only,
            is_featured=is_featured,
            skip=skip,
            limit=limit
        )

        # Enrich with business info
        results = []
        for event in events:
            business = business_crud.get(db, id=event.business_id)

            results.append({
                "event": event,
                "business": business
            })

        return results

    @staticmethod
    def get_event_details(
            db: Session,
            *,
            event_id: UUID
    ) -> Dict[str, Any]:
        """Get full event details with ticket tiers"""
        event = ticket_event_crud.get(db, id=event_id)
        if not event:
            raise NotFoundException("Event")

        business = business_crud.get(db, id=event.business_id)
        tiers = ticket_tier_crud.get_by_event(db, event_id=event_id)

        return {
            "event": event,
            "business": business,
            "tiers": tiers
        }

    @staticmethod
    def book_and_pay(
            db: Session,
            *,
            current_user: User,
            event_id: UUID,
            tier_id: UUID,
            quantity: int,
            attendee_name: str,
            attendee_email: str,
            attendee_phone: str,
            additional_attendees: List[Dict],
            special_requests: Optional[str] = None,
            payment_method: str = "wallet"
    ) -> TicketBooking:
        """Create booking and process payment"""
        # Create booking
        booking = ticket_booking_crud.create_booking(
            db,
            event_id=event_id,
            tier_id=tier_id,
            customer_id=current_user.id,
            quantity=quantity,
            attendee_name=attendee_name,
            attendee_email=attendee_email,
            attendee_phone=attendee_phone,
            additional_attendees=additional_attendees,
            special_requests=special_requests
        )

        # Process payment
        if payment_method == "wallet":
            wallet = wallet_crud.get_or_create_wallet(db, user_id=current_user.id)

            # Check balance
            if wallet.balance < booking.total_amount:
                # Cancel booking and restore capacity
                tier = ticket_tier_crud.get(db, id=tier_id)
                event = ticket_event_crud.get(db, id=event_id)

                tier.available_quantity += quantity
                event.available_capacity += quantity

                db.delete(booking)
                db.commit()

                raise InsufficientBalanceException()

            # Debit wallet
            wallet_crud.debit_wallet(
                db,
                wallet_id=wallet.id,
                amount=booking.total_amount,
                transaction_type=TransactionType.PAYMENT,
                description=f"Ticket booking {booking.booking_reference}",
                reference_id=str(booking.id)
            )

            # Update payment status
            booking.payment_status = "paid"
            booking.status = "confirmed"
            booking.payment_reference = str(booking.id)

            # Update event stats
            event = ticket_event_crud.get(db, id=event_id)
            event.total_tickets_sold += quantity
            event.total_revenue += booking.total_amount

            db.commit()
            db.refresh(booking)

        return booking

    @staticmethod
    def calculate_booking_price(
            db: Session,
            *,
            tier_id: UUID,
            quantity: int
    ) -> Dict[str, Decimal]:
        """Calculate booking price with breakdown"""
        tier = ticket_tier_crud.get(db, id=tier_id)
        if not tier:
            raise NotFoundException("Ticket tier")

        unit_price = tier.price
        subtotal = unit_price * quantity
        service_charge = subtotal * Decimal('0.05')  # 5% service charge
        total_amount = subtotal + service_charge

        return {
            "unit_price": unit_price,
            "subtotal": subtotal,
            "service_charge": service_charge,
            "total_amount": total_amount,
            "quantity": quantity
        }


ticket_service = TicketService()