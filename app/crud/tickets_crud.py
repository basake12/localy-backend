"""
app/crud/tickets_crud.py

FIXES vs previous version:
  1.  [HARD RULE §2/§4] lga_name filter REMOVED from search_events().
      Blueprint §2: "There is no local government area (LGA) filtering
      anywhere in the codebase. No LGA column exists in any database table."
      The comment "legacy — kept for back-compat" is not acceptable — back-compat
      with a HARD RULE violation is not a valid reason to keep it.

  2.  create_booking() no longer calls await db.commit() internally.
      It uses await db.flush() to assign IDs, then returns without committing.
      This allows the router to process wallet payment in the SAME database
      transaction and commit once everything succeeds.
      Blueprint §5.6: "All financial operations are wrapped in PostgreSQL
      transactions." Booking creation and payment debit must be atomic.
      If payment fails → db.rollback() → booking never persisted.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID
import secrets
import string

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.constants import PLATFORM_FEE_TICKET
from app.core.exceptions import (
    BookingNotAvailableException,
    NotFoundException,
    ValidationException,
)
from app.crud.base_crud import AsyncCRUDBase as CRUDBase
from app.models.tickets_model import (
    BookingStatusEnum,
    EventTypeEnum,
    PaymentStatusEnum,
    TicketBooking,
    TicketEvent,
    TicketStatusEnum,
    TicketTier,
    SeatMap,
)


# ─── Event CRUD ───────────────────────────────────────────────────────────────

class CRUDTicketEvent(CRUDBase[TicketEvent, dict, dict]):

    async def get_by_business_id(
        self,
        db: AsyncSession,
        *,
        business_id: UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> List[TicketEvent]:
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
        # lga_name DELETED — Blueprint §2/§4 HARD RULE:
        # "No LGA filtering anywhere in the codebase."
        # The previous 'lga_name' parameter ("kept for back-compat") was a
        # HARD RULE violation regardless of caller. Removed entirely.
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
        limit: int = 20,
    ) -> List[TicketEvent]:
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

        # Blueprint §4.1: radius-based discovery via PostGIS ST_DWithin.
        # No LGA filter — use GPS coordinates only.
        if location and event_type == EventTypeEnum.EVENT:
            lat, lng = location
            point = func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)
            stmt = stmt.where(
                or_(
                    TicketEvent.venue_location.is_(None),
                    func.ST_DWithin(
                        TicketEvent.venue_location, point, radius_km * 1000
                    ),
                )
            )

        if event_date_from:
            stmt = stmt.where(TicketEvent.event_date >= event_date_from)
        if event_date_to:
            stmt = stmt.where(TicketEvent.event_date <= event_date_to)
        if origin_city:
            stmt = stmt.where(TicketEvent.origin_city.ilike(f"%{origin_city}%"))
        if destination_city:
            stmt = stmt.where(
                TicketEvent.destination_city.ilike(f"%{destination_city}%")
            )
        if departure_date:
            stmt = stmt.where(TicketEvent.departure_date == departure_date)
        if transport_type:
            stmt = stmt.where(TicketEvent.transport_type == transport_type)
        if available_only:
            stmt = stmt.where(
                and_(
                    TicketEvent.status == TicketStatusEnum.AVAILABLE,
                    TicketEvent.available_capacity > 0,
                )
            )
        if is_featured is not None:
            stmt = stmt.where(TicketEvent.is_featured == is_featured)

        stmt = stmt.order_by(
            TicketEvent.is_featured.desc(), TicketEvent.created_at.desc()
        ).offset(skip).limit(limit)

        result = await db.execute(stmt)
        return result.scalars().all()


# ─── Tier CRUD ────────────────────────────────────────────────────────────────

class CRUDTicketTier(CRUDBase[TicketTier, dict, dict]):

    async def get_by_event(
        self,
        db: AsyncSession,
        *,
        event_id: UUID,
        active_only: bool = True,
    ) -> List[TicketTier]:
        stmt = select(TicketTier).where(TicketTier.event_id == event_id)
        if active_only:
            stmt = stmt.where(TicketTier.is_active == True)
        stmt = stmt.order_by(TicketTier.display_order)
        result = await db.execute(stmt)
        return result.scalars().all()


# ─── Booking CRUD ─────────────────────────────────────────────────────────────

class CRUDTicketBooking(CRUDBase[TicketBooking, dict, dict]):

    def _generate_booking_reference(self) -> str:
        """Cryptographically secure booking reference."""
        alphabet = string.ascii_uppercase + string.digits
        code = "".join(secrets.choice(alphabet) for _ in range(10))
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
        special_requests: Optional[str] = None,
    ) -> TicketBooking:
        """
        Create a ticket booking with race-condition-safe inventory deduction.

        IMPORTANT CHANGE: This method now uses flush() instead of commit().
        The router is responsible for committing after wallet payment succeeds.
        This ensures booking creation and payment are in the same DB transaction —
        if payment fails, the booking is rolled back and inventory is restored.
        Blueprint §5.6: "All financial operations are wrapped in PostgreSQL
        transactions."

        Uses SELECT ... FOR UPDATE on the tier row (row-level lock) to prevent
        overselling under concurrent DB writes.

        The Redis seat_hold (Blueprint §6.7) is acquired by the router BEFORE
        calling this method. It reserves the seat during the checkout window
        (TTL=600s) and is released after commit or on error.
        """
        # 1. Load event
        event_result = await db.execute(
            select(TicketEvent).where(
                TicketEvent.id == event_id,
                TicketEvent.is_active == True,
            )
        )
        event = event_result.scalar_one_or_none()
        if not event:
            raise NotFoundException("Event")

        # 2. Validate sales period
        now = datetime.now(timezone.utc)  # Blueprint §16.4 HARD RULE
        if event.sales_start_date and now < event.sales_start_date:
            raise ValidationException("Ticket sales have not started yet")
        if event.sales_end_date and now > event.sales_end_date:
            raise ValidationException("Ticket sales have ended")

        # 3. Lock tier row — prevents concurrent overselling
        tier_result = await db.execute(
            select(TicketTier)
            .where(TicketTier.id == tier_id, TicketTier.is_active == True)
            .with_for_update()
        )
        tier = tier_result.scalar_one_or_none()
        if not tier:
            raise NotFoundException("Ticket tier")

        # 4. Validate tier belongs to event
        if tier.event_id != event_id:
            raise ValidationException("Ticket tier does not belong to this event")

        # 5. Validate quantity limits
        if quantity < tier.min_purchase:
            raise ValidationException(
                f"Minimum purchase is {tier.min_purchase} ticket(s)"
            )
        if quantity > tier.max_purchase:
            raise ValidationException(
                f"Maximum purchase is {tier.max_purchase} ticket(s)"
            )

        # 6. Check availability (inside DB lock)
        if tier.available_quantity < quantity:
            raise BookingNotAvailableException()

        # 7. Calculate pricing — Blueprint §5.4 / §6.7
        # TicketBooking stores:
        #   unit_price    = per-ticket price (what customer pays for the ticket)
        #   service_charge = platform fee = ₦50 × quantity (Blueprint §5.4)
        #   total_amount  = unit_price × quantity + service_charge
        #
        # The customer wallet is debited total_amount (which includes the fee).
        # The business wallet receives unit_price × quantity (fee already excluded).
        unit_price     = tier.price
        service_charge = PLATFORM_FEE_TICKET * quantity   # ₦50 × qty
        total_amount   = (unit_price * quantity) + service_charge

        # 8. Generate unique booking reference
        booking_reference: str = ""
        for _ in range(5):
            ref = self._generate_booking_reference()
            existing = await db.execute(
                select(TicketBooking).where(
                    TicketBooking.booking_reference == ref
                )
            )
            if existing.scalar_one_or_none() is None:
                booking_reference = ref
                break
        else:
            raise RuntimeError("Could not generate unique booking reference")

        # 9. Create booking record
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
            booking_reference=booking_reference,
            # Status stays PENDING until router confirms payment
            status=BookingStatusEnum.PENDING,
            payment_status=PaymentStatusEnum.PENDING,
        )
        db.add(booking)

        # 10. Deduct inventory (within the DB lock — same transaction as payment)
        tier.available_quantity  -= quantity
        event.available_capacity -= quantity

        if event.available_capacity <= 0:
            event.status = TicketStatusEnum.SOLD_OUT

        # CHANGED: flush (not commit) — caller owns the commit after payment
        await db.flush()
        return booking

    async def get_customer_bookings(
        self,
        db: AsyncSession,
        *,
        customer_id: UUID,
        skip: int = 0,
        limit: int = 20,
    ) -> List[TicketBooking]:
        result = await db.execute(
            select(TicketBooking)
            .options(
                joinedload(TicketBooking.event),
                joinedload(TicketBooking.tier),
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
        limit: int = 50,
    ) -> List[TicketBooking]:
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
        booking_reference: str,
    ) -> Optional[TicketBooking]:
        result = await db.execute(
            select(TicketBooking)
            .where(TicketBooking.booking_reference == booking_reference)
            .options(
                joinedload(TicketBooking.event),
                joinedload(TicketBooking.tier),
            )
        )
        return result.scalar_one_or_none()

    async def check_in_ticket(
        self,
        db: AsyncSession,
        *,
        booking_reference: str,
    ) -> TicketBooking:
        """Check in a ticket by QR booking reference. Row-locked for safety."""
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

        booking.status       = BookingStatusEnum.CHECKED_IN
        booking.checked_in_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(booking)
        return booking

    async def cancel_booking(
        self,
        db: AsyncSession,
        *,
        booking_id: UUID,
        reason: Optional[str] = None,
    ) -> TicketBooking:
        """Cancel booking and restore capacity."""
        result = await db.execute(
            select(TicketBooking)
            .where(TicketBooking.id == booking_id)
            .with_for_update()
        )
        booking = result.scalar_one_or_none()
        if not booking:
            raise NotFoundException("Booking")

        if booking.status in (
            BookingStatusEnum.CHECKED_IN, BookingStatusEnum.CANCELLED
        ):
            raise ValidationException(
                "Cannot cancel a checked-in or already cancelled booking"
            )

        booking.status              = BookingStatusEnum.CANCELLED
        booking.cancelled_at        = datetime.now(timezone.utc)
        booking.cancellation_reason = reason

        # Restore tier inventory
        tier_result = await db.execute(
            select(TicketTier)
            .where(TicketTier.id == booking.tier_id)
            .with_for_update()
        )
        tier = tier_result.scalar_one_or_none()
        if tier:
            tier.available_quantity += booking.quantity

        # Restore event capacity
        event_result = await db.execute(
            select(TicketEvent)
            .where(TicketEvent.id == booking.event_id)
            .with_for_update()
        )
        event = event_result.scalar_one_or_none()
        if event:
            event.available_capacity += booking.quantity
            if (
                event.status == TicketStatusEnum.SOLD_OUT
                and event.available_capacity > 0
            ):
                event.status = TicketStatusEnum.AVAILABLE

        await db.commit()
        await db.refresh(booking)
        return booking


# ─── Singletons ───────────────────────────────────────────────────────────────

ticket_event_crud   = CRUDTicketEvent(TicketEvent)
ticket_tier_crud    = CRUDTicketTier(TicketTier)
ticket_booking_crud = CRUDTicketBooking(TicketBooking)