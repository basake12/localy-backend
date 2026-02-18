from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from uuid import UUID
from datetime import date, datetime
from decimal import Decimal
import random
import string

from app.crud.base import CRUDBase
from app.models.tickets import (
    TicketEvent, TicketTier, TicketBooking, SeatMap,
    TicketStatusEnum, BookingStatusEnum
)
from app.models.business import Business
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    BookingNotAvailableException
)


class CRUDTicketEvent(CRUDBase[TicketEvent, dict, dict]):
    """CRUD for TicketEvent"""

    def get_by_business_id(
            self,
            db: Session,
            *,
            business_id: UUID,
            skip: int = 0,
            limit: int = 50
    ) -> List[TicketEvent]:
        """Get events by business"""
        return db.query(TicketEvent).filter(
            TicketEvent.business_id == business_id
        ).offset(skip).limit(limit).all()

    def search_events(
            self,
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
    ) -> List[TicketEvent]:
        """Search ticket events with filters"""
        query = db.query(TicketEvent).filter(TicketEvent.is_active == True)

        # Text search
        if query_text:
            search_filter = or_(
                TicketEvent.name.ilike(f"%{query_text}%"),
                TicketEvent.description.ilike(f"%{query_text}%"),
                TicketEvent.venue_name.ilike(f"%{query_text}%")
            )
            query = query.filter(search_filter)

        # Event type filter
        if event_type:
            query = query.filter(TicketEvent.event_type == event_type)

        # Category filter (for events)
        if category:
            query = query.filter(TicketEvent.category == category)

        # Location filter (for events)
        if location and event_type == "event":
            lat, lng = location
            query = query.filter(
                func.ST_DWithin(
                    TicketEvent.venue_location,
                    func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326),
                    radius_km * 1000
                )
            )

        # Date filters (for events)
        if event_date_from:
            query = query.filter(TicketEvent.event_date >= event_date_from)

        if event_date_to:
            query = query.filter(TicketEvent.event_date <= event_date_to)

        # Transport filters
        if origin_city:
            query = query.filter(TicketEvent.origin_city.ilike(f"%{origin_city}%"))

        if destination_city:
            query = query.filter(TicketEvent.destination_city.ilike(f"%{destination_city}%"))

        if departure_date:
            query = query.filter(TicketEvent.departure_date == departure_date)

        if transport_type:
            query = query.filter(TicketEvent.transport_type == transport_type)

        # Availability filter
        if available_only:
            query = query.filter(
                and_(
                    TicketEvent.status == TicketStatusEnum.AVAILABLE,
                    TicketEvent.available_capacity > 0
                )
            )

        # Featured filter
        if is_featured is not None:
            query = query.filter(TicketEvent.is_featured == is_featured)

        return query.order_by(
            TicketEvent.is_featured.desc(),
            TicketEvent.created_at.desc()
        ).offset(skip).limit(limit).all()


class CRUDTicketTier(CRUDBase[TicketTier, dict, dict]):
    """CRUD for TicketTier"""

    def get_by_event(
            self,
            db: Session,
            *,
            event_id: UUID,
            active_only: bool = True
    ) -> List[TicketTier]:
        """Get ticket tiers for event"""
        query = db.query(TicketTier).filter(
            TicketTier.event_id == event_id
        )

        if active_only:
            query = query.filter(TicketTier.is_active == True)

        return query.order_by(TicketTier.display_order).all()

    def check_availability(
            self,
            db: Session,
            *,
            tier_id: UUID,
            quantity: int
    ) -> bool:
        """Check if tier has enough tickets available"""
        tier = self.get(db, id=tier_id)
        if not tier or not tier.is_active:
            return False

        return tier.available_quantity >= quantity


class CRUDTicketBooking(CRUDBase[TicketBooking, dict, dict]):
    """CRUD for TicketBooking"""

    def _generate_booking_reference(self, db: Session) -> str:
        """Generate unique booking reference"""
        while True:
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
            reference = f"TKT{code}"

            existing = db.query(TicketBooking).filter(
                TicketBooking.booking_reference == reference
            ).first()

            if not existing:
                return reference

    def create_booking(
            self,
            db: Session,
            *,
            event_id: UUID,
            tier_id: UUID,
            customer_id: UUID,
            quantity: int,
            attendee_name: str,
            attendee_email: str,
            attendee_phone: str,
            additional_attendees: List[Dict],
            special_requests: Optional[str] = None
    ) -> TicketBooking:
        """Create a ticket booking"""
        # Get event
        event = ticket_event_crud.get(db, id=event_id)
        if not event or not event.is_active:
            raise NotFoundException("Event")

        # Get tier
        tier = ticket_tier_crud.get(db, id=tier_id)
        if not tier or not tier.is_active:
            raise NotFoundException("Ticket tier")

        # Check tier belongs to event
        if tier.event_id != event_id:
            raise ValidationException("Ticket tier does not belong to this event")

        # Check quantity limits
        if quantity < tier.min_purchase or quantity > tier.max_purchase:
            raise ValidationException(
                f"Quantity must be between {tier.min_purchase} and {tier.max_purchase}"
            )

        # Check availability
        if not ticket_tier_crud.check_availability(db, tier_id=tier_id, quantity=quantity):
            raise BookingNotAvailableException()

        # Calculate pricing
        unit_price = tier.price
        service_charge = unit_price * quantity * Decimal('0.05')  # 5% service charge
        total_amount = (unit_price * quantity) + service_charge

        # Generate booking reference
        booking_reference = self._generate_booking_reference(db)

        # Create booking
        booking = TicketBooking(
            event_id=event_id,
            tier_id=tier_id,
            customer_id=customer_id,
            quantity=quantity,
            unit_price=unit_price,
            service_charge=service_charge,
            total_amount=total_amount,
            attendee_name=attendee_name,
            attendee_email=attendee_email,
            attendee_phone=attendee_phone,
            additional_attendees=additional_attendees,
            special_requests=special_requests,
            booking_reference=booking_reference
        )

        db.add(booking)
        db.flush()

        # Reduce available capacity
        tier.available_quantity -= quantity
        event.available_capacity -= quantity

        # TODO: Generate QR code

        db.commit()
        db.refresh(booking)

        return booking

    def get_customer_bookings(
            self,
            db: Session,
            *,
            customer_id: UUID,
            skip: int = 0,
            limit: int = 20
    ) -> List[TicketBooking]:
        """Get customer bookings"""
        return db.query(TicketBooking).options(
            joinedload(TicketBooking.event),
            joinedload(TicketBooking.tier)
        ).filter(
            TicketBooking.customer_id == customer_id
        ).order_by(
            TicketBooking.created_at.desc()
        ).offset(skip).limit(limit).all()

    def get_event_bookings(
            self,
            db: Session,
            *,
            event_id: UUID,
            status: Optional[str] = None,
            skip: int = 0,
            limit: int = 50
    ) -> List[TicketBooking]:
        """Get event bookings"""
        query = db.query(TicketBooking).filter(
            TicketBooking.event_id == event_id
        )

        if status:
            query = query.filter(TicketBooking.status == status)

        return query.order_by(
            TicketBooking.created_at.desc()
        ).offset(skip).limit(limit).all()

    def check_in_ticket(
            self,
            db: Session,
            *,
            booking_id: UUID
    ) -> TicketBooking:
        """Check in a ticket"""
        booking = self.get(db, id=booking_id)
        if not booking:
            raise NotFoundException("Booking")

        if booking.status != BookingStatusEnum.CONFIRMED:
            raise ValidationException("Can only check in confirmed bookings")

        booking.status = BookingStatusEnum.CHECKED_IN
        booking.checked_in_at = datetime.utcnow()

        db.commit()
        db.refresh(booking)

        return booking


# Singleton instances
ticket_event_crud = CRUDTicketEvent(TicketEvent)
ticket_tier_crud = CRUDTicketTier(TicketTier)
ticket_booking_crud = CRUDTicketBooking(TicketBooking)