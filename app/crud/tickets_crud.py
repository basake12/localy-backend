from typing import Optional, List, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, or_, func, select, update
from sqlalchemy.orm import joinedload, selectinload
from uuid import UUID
from datetime import date, datetime, timezone
from decimal import Decimal
import secrets
import string

from app.crud.base_crud import AsyncCRUDBase as CRUDBase
from app.models.tickets_model import (
    TicketEvent, TicketTier, TicketBooking, SeatMap,
    TicketStatusEnum, BookingStatusEnum, PaymentStatusEnum, EventTypeEnum
)
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    BookingNotAvailableException
)
from app.core.constants import TICKET_SERVICE_CHARGE_RATE   # e.g. Decimal("0.05")


# ============================================
# TICKET EVENT CRUD
# ============================================

class CRUDTicketEvent(CRUDBase[TicketEvent, dict, dict]):
    """CRUD for TicketEvent"""

    async def get_by_business_id(
            self,
            db: AsyncSession,
            *,
            business_id: UUID,
            skip: int = 0,
            limit: int = 50
    ) -> List[TicketEvent]:
        """Get events by business"""
        result = await db.execute(
            select(TicketEvent)
            .where(TicketEvent.business_id == business_id)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def search_events(
            self,
            db: AsyncSession,
            *,
            query_text: Optional[str] = None,
            event_type: Optional[str] = None,
            category: Optional[str] = None,
            # FIX: parameter renamed from lga_id (UUID) to lga_name (str) to match
            # the model column TicketEvent.lga_name (String). The old lga_id referred
            # to a non-existent column — any caller passing lga_id caused AttributeError.
            # Note: blueprint §3.1 says no LGA filtering; tickets.py router never sends
            # this. The parameter is kept for back-compat but marked as legacy.
            lga_name: Optional[str] = None,
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
        stmt = (
            select(TicketEvent)
            .where(TicketEvent.is_active == True)
            .options(selectinload(TicketEvent.ticket_tiers))
        )

        if query_text:
            stmt = stmt.where(
                or_(
                    TicketEvent.name.ilike(f"%{query_text}%"),
                    TicketEvent.description.ilike(f"%{query_text}%"),
                    TicketEvent.venue_name.ilike(f"%{query_text}%"),
                )
            )

        if event_type:
            stmt = stmt.where(TicketEvent.event_type == event_type)

        if category:
            stmt = stmt.where(TicketEvent.category == category)

        # lga_name filter (legacy — blueprint §3.1 mandates radius-only discovery;
        # tickets.py router does not send this parameter).
        if lga_name:
            stmt = stmt.where(TicketEvent.lga_name == lga_name)
        elif location and event_type == EventTypeEnum.EVENT:
            lat, lng = location
            point = func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)
            # FIX: Include events with no venue_location — ST_DWithin(NULL) returns NULL → empty results
            stmt = stmt.where(or_(
                TicketEvent.venue_location.is_(None),
                func.ST_DWithin(TicketEvent.venue_location, point, radius_km * 1000)
            ))

        if event_date_from:
            stmt = stmt.where(TicketEvent.event_date >= event_date_from)

        if event_date_to:
            stmt = stmt.where(TicketEvent.event_date <= event_date_to)

        if origin_city:
            stmt = stmt.where(TicketEvent.origin_city.ilike(f"%{origin_city}%"))

        if destination_city:
            stmt = stmt.where(TicketEvent.destination_city.ilike(f"%{destination_city}%"))

        if departure_date:
            stmt = stmt.where(TicketEvent.departure_date == departure_date)

        if transport_type:
            stmt = stmt.where(TicketEvent.transport_type == transport_type)

        if available_only:
            stmt = stmt.where(
                and_(
                    TicketEvent.status == TicketStatusEnum.AVAILABLE,
                    TicketEvent.available_capacity > 0
                )
            )

        if is_featured is not None:
            stmt = stmt.where(TicketEvent.is_featured == is_featured)

        stmt = stmt.order_by(
            TicketEvent.is_featured.desc(),
            TicketEvent.created_at.desc()
        ).offset(skip).limit(limit)

        result = await db.execute(stmt)
        return result.scalars().all()


# ============================================
# TICKET TIER CRUD
# ============================================

class CRUDTicketTier(CRUDBase[TicketTier, dict, dict]):
    """CRUD for TicketTier"""

    async def get_by_event(
            self,
            db: AsyncSession,
            *,
            event_id: UUID,
            active_only: bool = True
    ) -> List[TicketTier]:
        """Get ticket tiers for event"""
        stmt = select(TicketTier).where(TicketTier.event_id == event_id)

        if active_only:
            stmt = stmt.where(TicketTier.is_active == True)

        stmt = stmt.order_by(TicketTier.display_order)
        result = await db.execute(stmt)
        return result.scalars().all()


# ============================================
# TICKET BOOKING CRUD
# ============================================

class CRUDTicketBooking(CRUDBase[TicketBooking, dict, dict]):
    """CRUD for TicketBooking"""

    def _generate_booking_reference(self) -> str:
        """
        Generate a cryptographically secure booking reference.
        FIX: replaced random.choices (not cryptographically secure) with secrets.
        """
        alphabet = string.ascii_uppercase + string.digits
        code = ''.join(secrets.choice(alphabet) for _ in range(10))
        return f"TKT{code}"

    async def create_booking(
            self,
            db: AsyncSession,
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
        """
        Create a ticket booking with race-condition-safe inventory deduction.

        FIX: Uses SELECT ... FOR UPDATE on the tier row to prevent overselling
        under concurrent requests. Without this, two users could both pass the
        availability check before either commits, resulting in negative inventory.
        """
        # --- 1. Load event (no lock needed, we only read it here) ---
        event_result = await db.execute(
            select(TicketEvent).where(
                TicketEvent.id == event_id,
                TicketEvent.is_active == True
            )
        )
        event = event_result.scalar_one_or_none()
        if not event:
            raise NotFoundException("Event")

        # --- 2. Validate sales period ---
        now = datetime.now(timezone.utc)
        if event.sales_start_date and now < event.sales_start_date:
            raise ValidationException("Ticket sales have not started yet")
        if event.sales_end_date and now > event.sales_end_date:
            raise ValidationException("Ticket sales have ended")

        # --- 3. Lock the tier row to prevent concurrent overselling ---
        tier_result = await db.execute(
            select(TicketTier)
            .where(
                TicketTier.id == tier_id,
                TicketTier.is_active == True
            )
            .with_for_update()   # FIX: row-level lock
        )
        tier = tier_result.scalar_one_or_none()
        if not tier:
            raise NotFoundException("Ticket tier")

        # --- 4. Validate tier belongs to this event ---
        if tier.event_id != event_id:
            raise ValidationException("Ticket tier does not belong to this event")

        # --- 5. Validate quantity limits ---
        if quantity < tier.min_purchase:
            raise ValidationException(f"Minimum purchase is {tier.min_purchase} ticket(s)")
        if quantity > tier.max_purchase:
            raise ValidationException(f"Maximum purchase is {tier.max_purchase} ticket(s)")

        # --- 6. Check availability (inside the lock) ---
        if tier.available_quantity < quantity:
            raise BookingNotAvailableException()

        # --- 7. Calculate pricing ---
        unit_price = tier.price
        service_charge = (unit_price * quantity * TICKET_SERVICE_CHARGE_RATE).quantize(
            Decimal("0.01")
        )
        total_amount = (unit_price * quantity) + service_charge

        # --- 8. Generate unique booking reference ---
        # Loop is safe — probability of collision is negligible but we guard it
        for _ in range(5):
            ref = self._generate_booking_reference()
            existing = await db.execute(
                select(TicketBooking).where(TicketBooking.booking_reference == ref)
            )
            if existing.scalar_one_or_none() is None:
                booking_reference = ref
                break
        else:
            raise RuntimeError("Could not generate unique booking reference")

        # --- 9. Create booking ---
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

        # --- 10. Deduct inventory (within the same transaction as the lock) ---
        tier.available_quantity -= quantity
        event.available_capacity -= quantity

        # Mark sold out if no capacity left
        if event.available_capacity <= 0:
            event.status = TicketStatusEnum.SOLD_OUT

        await db.flush()   # get booking.id before committing
        # NOTE: QR code generation should be dispatched as a Celery task here
        # after commit, using booking.id and booking_reference.

        await db.commit()
        await db.refresh(booking)

        return booking

    async def get_customer_bookings(
            self,
            db: AsyncSession,
            *,
            customer_id: UUID,
            skip: int = 0,
            limit: int = 20
    ) -> List[TicketBooking]:
        """Get customer bookings with event and tier eager-loaded"""
        result = await db.execute(
            select(TicketBooking)
            .options(
                joinedload(TicketBooking.event),
                joinedload(TicketBooking.tier)
            )
            .where(TicketBooking.customer_id == customer_id)
            .order_by(TicketBooking.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_event_bookings(
            self,
            db: AsyncSession,
            *,
            event_id: UUID,
            status: Optional[str] = None,
            skip: int = 0,
            limit: int = 50
    ) -> List[TicketBooking]:
        """Get event bookings"""
        stmt = (
            select(TicketBooking)
            .where(TicketBooking.event_id == event_id)
            .order_by(TicketBooking.created_at.desc())
            .offset(skip)
            .limit(limit)
        )

        if status:
            stmt = stmt.where(TicketBooking.status == status)

        result = await db.execute(stmt)
        return result.scalars().all()

    async def get_by_reference(
            self,
            db: AsyncSession,
            *,
            booking_reference: str
    ) -> Optional[TicketBooking]:
        """Look up a booking by its reference code"""
        result = await db.execute(
            select(TicketBooking)
            .where(TicketBooking.booking_reference == booking_reference)
            .options(
                joinedload(TicketBooking.event),
                joinedload(TicketBooking.tier)
            )
        )
        return result.scalar_one_or_none()

    async def check_in_ticket(
            self,
            db: AsyncSession,
            *,
            booking_reference: str
    ) -> TicketBooking:
        """
        Check in a ticket by its QR booking reference.

        FIX: accepts booking_reference string (from QR scan) instead of UUID.
        Also uses SELECT FOR UPDATE to prevent double check-in race conditions.
        FIX: datetime.now(timezone.utc) replaces deprecated datetime.utcnow().
        """
        # Lock the row to prevent concurrent double-checkins
        result = await db.execute(
            select(TicketBooking)
            .where(TicketBooking.booking_reference == booking_reference)
            .with_for_update()
        )
        booking = result.scalar_one_or_none()

        if not booking:
            raise NotFoundException("Booking")

        if booking.status == BookingStatusEnum.CHECKED_IN:
            raise ValidationException("Ticket already checked in")

        if booking.status != BookingStatusEnum.CONFIRMED:
            raise ValidationException(
                f"Cannot check in a booking with status '{booking.status.value}'"
            )

        booking.status = BookingStatusEnum.CHECKED_IN
        # FIX: datetime.utcnow() is deprecated since Python 3.12
        booking.checked_in_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(booking)

        return booking

    async def cancel_booking(
            self,
            db: AsyncSession,
            *,
            booking_id: UUID,
            reason: Optional[str] = None
    ) -> TicketBooking:
        """Cancel a booking and restore capacity"""
        # Lock the booking row
        result = await db.execute(
            select(TicketBooking)
            .where(TicketBooking.id == booking_id)
            .with_for_update()
        )
        booking = result.scalar_one_or_none()

        if not booking:
            raise NotFoundException("Booking")

        if booking.status in (BookingStatusEnum.CHECKED_IN, BookingStatusEnum.CANCELLED):
            raise ValidationException(
                "Cannot cancel a checked-in or already cancelled booking"
            )

        booking.status = BookingStatusEnum.CANCELLED
        booking.cancelled_at = datetime.now(timezone.utc)
        booking.cancellation_reason = reason

        # Restore capacity
        tier_result = await db.execute(
            select(TicketTier)
            .where(TicketTier.id == booking.tier_id)
            .with_for_update()
        )
        tier = tier_result.scalar_one_or_none()
        if tier:
            tier.available_quantity += booking.quantity

        event_result = await db.execute(
            select(TicketEvent)
            .where(TicketEvent.id == booking.event_id)
            .with_for_update()
        )
        event = event_result.scalar_one_or_none()
        if event:
            event.available_capacity += booking.quantity
            # Restore status if it was sold out
            if event.status == TicketStatusEnum.SOLD_OUT and event.available_capacity > 0:
                event.status = TicketStatusEnum.AVAILABLE

        await db.commit()
        await db.refresh(booking)
        return booking


# Singleton instances
ticket_event_crud = CRUDTicketEvent(TicketEvent)
ticket_tier_crud = CRUDTicketTier(TicketTier)
ticket_booking_crud = CRUDTicketBooking(TicketBooking)