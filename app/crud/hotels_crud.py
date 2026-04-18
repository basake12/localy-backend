"""
app/crud/hotels_crud.py

Per Blueprint v2.0 Section 6.1 — Hotels Module

CHANGES FROM ORIGINAL:
1. Removed all lga_id filtering — replaced with PostGIS radius search
2. Added transaction support for atomic booking operations
3. Subscription-tier ranking (Enterprise > Pro > Starter > Free)
4. Enhanced availability checking with overlapping detection
5. Booking cancellation with refund support
6. Converted to AsyncSession (async/await)

BUG FIXES IN THIS VERSION:
─────────────────────────────
BUG-04 FIX (hotels_crud.py — NULL location included in proximity search):
  The original search_hotels() used:
      or_(Business.location.is_(None), func.ST_DWithin(...))
  The comment claimed this was a fix to avoid silently dropping every hotel
  when location was unavailable.  It is actually a bug: a NULL location means
  geocoding failed at onboarding — the business's physical position is unknown.
  Including it in a proximity-based feed violates the platform's core promise
  of proximity-first discovery (Blueprint Section 1, 4.1, 4.3).
  Fix: removed Business.location.is_(None) from the query entirely.

BUG-05 FIX (hotels_crud.py — subscription tier ordering on VARCHAR):
  Original: query.order_by(Business.subscription_tier.desc())
  subscription_tier is VARCHAR.  Alphabetical DESC order is:
      starter > pro > free > enterprise  — Enterprise drops to last.
  Blueprint Section 7.2 provides subscription_tier_rank (INTEGER) for this:
      Enterprise=4, Pro=3, Starter=2, Free=1.
  Fix: changed to Business.subscription_tier_rank.desc().

BUG-06 FIX (hotels_crud.py — unverified businesses in search results):
  The base query had no is_verified filter.  Blueprint Section 4.3 PostGIS
  query explicitly requires AND b.is_verified = TRUE.  Any business awaiting
  admin review was appearing in all discovery results.
  Fix: added Business.is_verified == True to base query conditions.

BUG-07 FIX (hotels_crud.py — duplicate Hotel rows when price filter joins RoomType):
  When min_price or max_price was given, query.join(RoomType) was appended
  without .distinct().  A hotel with 3 room types in the price range produced
  3 joined rows.  SQLAlchemy identity-map deduplication is not guaranteed
  across paginated queries and can return inconsistent result counts.
  Fix: added .distinct() to the query when the price join is applied.

BUG-09 FIX (hotels_crud.py — cancel_booking missing CHECKED_OUT guard):
  The service layer rejects both CHECKED_IN and CHECKED_OUT bookings.
  The CRUD only rejected CHECKED_IN.  A direct call to cancel_booking()
  (e.g. from admin tooling or a future code path) could cancel a completed
  booking, corrupt its status, and trigger an erroneous refund.
  Fix: added BookingStatusEnum.CHECKED_OUT to the cancellation guard.
"""
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy import and_, func, or_, select
from uuid import UUID
from datetime import date, datetime, timezone
from decimal import Decimal

from app.crud.base_crud import AsyncCRUDBase as CRUDBase
from app.models.hotels_model import (
    Hotel, RoomType, Room, HotelBooking, HotelInStayRequest,
    RoomStatusEnum, BookingStatusEnum, PaymentStatusEnum
)
from app.models.business_model import Business
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
)
from app.core.constants import DEFAULT_RADIUS_METERS

import logging

logger = logging.getLogger(__name__)


class CRUDHotel(CRUDBase[Hotel, dict, dict]):
    """Hotel CRUD operations with radius-based search."""

    async def get_by_business_id(
        self, db: AsyncSession, *, business_id: UUID
    ) -> Optional[Hotel]:
        """Get hotel by business ID."""
        result = await db.execute(
            select(Hotel).where(Hotel.business_id == business_id)
        )
        return result.scalars().first()

    async def get_with_room_types(
        self, db: AsyncSession, *, hotel_id: UUID
    ) -> Optional[Hotel]:
        """Get hotel with eagerly loaded room types and business."""
        result = await db.execute(
            select(Hotel)
            .options(joinedload(Hotel.room_types), joinedload(Hotel.business))
            .where(Hotel.id == hotel_id)
        )
        return result.scalars().first()

    async def search_hotels(
        self,
        db: AsyncSession,
        *,
        skip: int = 0,
        limit: int = 20,
        location: Optional[Tuple[float, float]] = None,
        radius_meters: int = DEFAULT_RADIUS_METERS,
        star_rating: Optional[int] = None,
        facilities: Optional[List[str]] = None,
        min_price: Optional[Decimal] = None,
        max_price: Optional[Decimal] = None,
    ) -> List[Hotel]:
        """
        Search hotels using radius-based filtering (PostGIS ST_DWithin).

        Per Blueprint Section 4:
        - Radius-based only — no LGA dependency of any kind.
        - Default radius 5 km; adjustable 1–50 km by user.
        - Results ranked Enterprise > Pro > Starter > Free via
          subscription_tier_rank (INTEGER).
        - Only verified, active businesses are returned.

        Args:
            location:      (latitude, longitude) for radius filtering.
                           When omitted, no geographic filter is applied
                           (e.g. admin tooling that needs all hotels).
            radius_meters: Search radius in metres (default 5000 = 5 km).
            star_rating:   Filter by star rating (1–5).
            facilities:    Required facilities; all must be present (AND logic).
            min_price:     Minimum room price per night.
            max_price:     Maximum room price per night.
            skip / limit:  Pagination.

        Returns:
            List of Hotel ORM objects within radius, ranked by tier then distance.
        """
        query = (
            select(Hotel)
            .join(Business, Business.id == Hotel.business_id)
            .where(
                # BUG-06 FIX: Only surface verified, active businesses.
                # Blueprint Section 4.3 PostGIS query: AND b.is_verified = TRUE.
                # Unverified businesses (awaiting admin review) must not appear
                # in any discovery surface.
                Business.is_active == True,
                Business.is_verified == True,
            )
            .options(
                joinedload(Hotel.business),
                selectinload(Hotel.room_types),
            )
        )

        # ── Radius-based location filter (PostGIS ST_DWithin) ────────────────
        if location:
            lat, lng = location
            point = func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)

            # BUG-04 FIX: removed or_(Business.location.is_(None), ...).
            #
            # Original code included hotels whose business had no location:
            #     or_(Business.location.is_(None), ST_DWithin(...))
            # A NULL location means geocoding failed at onboarding — the
            # business's physical position is unknown.  Per Blueprint Section
            # 4.1, every business is geocoded at registration.  A NULL location
            # is a data-quality failure, not a reason to include the business
            # in a proximity-based result set.
            #
            # Including unknown-location businesses in a "near you" feed
            # directly violates the platform's core promise.  The correct
            # response to a NULL location is to flag the business in the admin
            # panel for re-geocoding, not to show it to every customer.
            query = query.where(
                func.ST_DWithin(Business.location, point, radius_meters)
            )

        # ── Star rating filter ───────────────────────────────────────────────
        if star_rating:
            query = query.where(Hotel.star_rating == star_rating)

        # ── Facilities filter (all required facilities must be present) ──────
        if facilities:
            for facility in facilities:
                query = query.where(Hotel.facilities.contains([facility]))

        # ── Price range filter ───────────────────────────────────────────────
        # BUG-07 FIX: Added .distinct() before the join.
        #
        # Without distinct(), joining RoomType produces one row per matching
        # room type.  A hotel with 3 room types in the price range returns 3
        # rows.  SQLAlchemy identity-map deduplication is not reliable across
        # paginated queries (e.g. if a hotel spans a page boundary the same
        # hotel can appear on both pages).  .distinct() forces the DB to
        # deduplicate before returning results.
        if min_price or max_price:
            query = query.distinct().join(
                RoomType, RoomType.hotel_id == Hotel.id
            )
            if min_price:
                query = query.where(RoomType.base_price_per_night >= min_price)
            if max_price:
                query = query.where(RoomType.base_price_per_night <= max_price)

        # ── Ranking ─────────────────────────────────────────────────────────
        # BUG-05 FIX: Order by subscription_tier_rank (INTEGER), not
        # subscription_tier (VARCHAR).
        #
        # subscription_tier is a VARCHAR enum: 'free', 'starter', 'pro',
        # 'enterprise'.  Alphabetical DESC order is:
        #     starter > pro > free > enterprise   ← completely wrong.
        # Blueprint Section 7.2 provides subscription_tier_rank (INTEGER) with
        # values Enterprise=4, Pro=3, Starter=2, Free=1 specifically for ORDER BY.
        query = query.order_by(Business.subscription_tier_rank.desc())

        result = await db.execute(query.offset(skip).limit(limit))
        return list(result.scalars().unique().all())


class CRUDRoomType(CRUDBase[RoomType, dict, dict]):
    """RoomType CRUD operations with availability checking."""

    async def get_by_hotel(
        self,
        db: AsyncSession,
        *,
        hotel_id: UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> List[RoomType]:
        """Get all room types for a hotel."""
        result = await db.execute(
            select(RoomType)
            .where(RoomType.hotel_id == hotel_id)
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def check_availability(
        self,
        db: AsyncSession,
        *,
        room_type_id: UUID,
        check_in: date,
        check_out: date,
        number_of_rooms: int = 1,
    ) -> int:
        """
        Check room availability for a given date range.

        Returns the number of rooms still available for the period.
        Considers overlapping bookings in PENDING, CONFIRMED, or CHECKED_IN
        status (CANCELLED, CHECKED_OUT, and NO_SHOW do not hold inventory).

        A booking overlaps iff its check_in < our check_out AND
        its check_out > our check_in (standard half-open interval overlap).

        Args:
            room_type_id:    Room type to check.
            check_in:        Requested check-in date.
            check_out:       Requested check-out date.
            number_of_rooms: Number of rooms requested.

        Returns:
            Number of available rooms (0 if fully booked).
        """
        result = await db.execute(
            select(RoomType).where(RoomType.id == room_type_id)
        )
        room_type = result.scalars().first()
        if not room_type:
            return 0

        # Count rooms held by overlapping active bookings.
        overlapping_result = await db.execute(
            select(func.sum(HotelBooking.number_of_rooms))
            .where(
                HotelBooking.room_type_id == room_type_id,
                HotelBooking.status.in_([
                    BookingStatusEnum.PENDING,
                    BookingStatusEnum.CONFIRMED,
                    BookingStatusEnum.CHECKED_IN,
                ]),
                # Overlap condition (half-open intervals):
                HotelBooking.check_in_date < check_out,
                HotelBooking.check_out_date > check_in,
            )
        )
        overlapping_booked = overlapping_result.scalar() or 0

        available = max(0, room_type.total_rooms - int(overlapping_booked))
        return available

    async def get_available_room_types(
        self,
        db: AsyncSession,
        *,
        hotel_id: UUID,
        check_in: date,
        check_out: date,
        number_of_rooms: int = 1,
        number_of_guests: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Get room types that can accommodate the requested stay.

        Filters out room types that:
        - Cannot accommodate the number of guests (max_occupancy < number_of_guests)
        - Do not have enough available rooms for the date range

        Returns:
            List of plain dicts (safe for jsonable_encoder) with an added
            'available_rooms' field showing remaining inventory.
        """
        room_types = await self.get_by_hotel(db, hotel_id=hotel_id)
        result = []

        for rt in room_types:
            # Skip room types that can't accommodate the party size.
            if rt.max_occupancy < number_of_guests:
                continue

            available = await self.check_availability(
                db,
                room_type_id=rt.id,
                check_in=check_in,
                check_out=check_out,
                number_of_rooms=number_of_rooms,
            )

            # Only include if enough rooms are available.
            if available >= number_of_rooms:
                rt_dict = {
                    "id": rt.id,
                    "hotel_id": rt.hotel_id,
                    "name": rt.name,
                    "description": rt.description,
                    "bed_configuration": rt.bed_configuration,
                    "max_occupancy": rt.max_occupancy,
                    "size_sqm": rt.size_sqm,
                    "floor_range": rt.floor_range,
                    "view_type": rt.view_type,
                    "amenities": rt.amenities or [],
                    "base_price_per_night": rt.base_price_per_night,
                    "images": rt.images or [],
                    "total_rooms": rt.total_rooms,
                    "available_rooms": available,
                    "created_at": rt.created_at,
                }
                result.append(rt_dict)

        return result


class CRUDHotelBooking(CRUDBase[HotelBooking, dict, dict]):
    """HotelBooking CRUD operations with payment integration."""

    async def create_booking(
        self,
        db: AsyncSession,
        *,
        hotel_id: UUID,
        room_type_id: UUID,
        customer_id: UUID,
        check_in: date,
        check_out: date,
        number_of_rooms: int,
        number_of_guests: int,
        add_ons: list,
        special_requests: Optional[str] = None,
    ) -> HotelBooking:
        """
        Create a hotel booking record (WITHOUT payment processing).

        Payment is handled separately by hotel_service using transaction_service.
        This method only creates the booking record with calculated pricing.

        Raises:
            NotFoundException:  If room_type not found.
            ValidationException: If room_type doesn't belong to hotel,
                                 or if not enough rooms available.

        NOTE: Caller must commit (for transaction atomicity with payment).
        """
        result = await db.execute(
            select(RoomType).where(RoomType.id == room_type_id)
        )
        room_type = result.scalars().first()
        if not room_type:
            raise NotFoundException("Room type")

        if room_type.hotel_id != hotel_id:
            raise ValidationException("Room type does not belong to this hotel")

        available = await room_type_crud.check_availability(
            db,
            room_type_id=room_type_id,
            check_in=check_in,
            check_out=check_out,
            number_of_rooms=number_of_rooms,
        )
        if available < number_of_rooms:
            raise ValidationException(
                f"Only {available} room(s) available for the selected dates. "
                f"Requested: {number_of_rooms}"
            )

        # Calculate pricing
        nights = (check_out - check_in).days
        base_price = room_type.base_price_per_night * nights * number_of_rooms

        add_ons_price = Decimal("0.00")
        add_ons_serialised = []
        for item in add_ons:
            # Accept both plain dicts and Pydantic model instances.
            item_dict = item if isinstance(item, dict) else item.model_dump()
            item_price = Decimal(str(item_dict.get("price") or 0))
            item_qty = int(item_dict.get("quantity", 1))
            add_ons_price += item_price * item_qty
            add_ons_serialised.append(item_dict)

        total_price = base_price + add_ons_price

        booking = HotelBooking(
            hotel_id=hotel_id,
            room_type_id=room_type_id,
            customer_id=customer_id,
            check_in_date=check_in,
            check_out_date=check_out,
            number_of_rooms=number_of_rooms,
            number_of_guests=number_of_guests,
            base_price=base_price,
            add_ons_price=add_ons_price,
            total_price=total_price,
            add_ons=add_ons_serialised,
            special_requests=special_requests,
            status=BookingStatusEnum.PENDING,
            payment_status=PaymentStatusEnum.PENDING,
        )

        db.add(booking)
        # Do NOT commit here — caller manages the transaction boundary
        # so the booking and payment are committed atomically.
        return booking

    async def get_customer_bookings(
        self,
        db: AsyncSession,
        *,
        customer_id: UUID,
        skip: int = 0,
        limit: int = 20,
        status: Optional[str] = None,
    ) -> List[HotelBooking]:
        """Get bookings for a customer, optionally filtered by status."""
        query = select(HotelBooking).where(
            HotelBooking.customer_id == customer_id
        )
        if status:
            query = query.where(HotelBooking.status == status)

        result = await db.execute(
            query.order_by(HotelBooking.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_hotel_bookings(
        self,
        db: AsyncSession,
        *,
        hotel_id: UUID,
        skip: int = 0,
        limit: int = 50,
        status: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[HotelBooking]:
        """Get bookings for a hotel with optional filters."""
        query = select(HotelBooking).where(HotelBooking.hotel_id == hotel_id)

        if status:
            query = query.where(HotelBooking.status == status)
        if date_from:
            query = query.where(HotelBooking.check_in_date >= date_from)
        if date_to:
            query = query.where(HotelBooking.check_out_date <= date_to)

        result = await db.execute(
            query.order_by(HotelBooking.check_in_date)
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def confirm_booking(
        self, db: AsyncSession, *, booking_id: UUID
    ) -> HotelBooking:
        """
        Confirm a pending booking.

        NOTE: Caller must commit.
        """
        result = await db.execute(
            select(HotelBooking).where(HotelBooking.id == booking_id)
        )
        booking = result.scalars().first()
        if not booking:
            raise NotFoundException("Booking")

        booking.status = BookingStatusEnum.CONFIRMED
        return booking

    async def mark_paid(
        self, db: AsyncSession, *, booking_id: UUID
    ) -> HotelBooking:
        """
        Mark booking as paid after successful payment.

        Called by hotel_service after transaction_service.process_payment() succeeds.

        NOTE: Caller must commit.
        """
        result = await db.execute(
            select(HotelBooking).where(HotelBooking.id == booking_id)
        )
        booking = result.scalars().first()
        if not booking:
            raise NotFoundException("Booking")

        booking.payment_status = PaymentStatusEnum.PAID
        booking.status = BookingStatusEnum.CONFIRMED
        return booking

    async def cancel_booking(
        self,
        db: AsyncSession,
        *,
        booking_id: UUID,
        reason: Optional[str] = None,
    ) -> HotelBooking:
        """
        Cancel a booking (refund handled separately by service layer).

        BUG-09 FIX: Added BookingStatusEnum.CHECKED_OUT to the guard.
        Original code only rejected CHECKED_IN.  The service layer rejects
        both CHECKED_IN and CHECKED_OUT, but a direct CRUD call (e.g. from
        an admin action or a future code path) could bypass the service and
        cancel a CHECKED_OUT booking, corrupt its final status, and trigger
        an erroneous refund.  Both terminal states are now guarded here.

        NOTE: Caller must commit.

        Raises:
            NotFoundException:   If booking not found.
            ValidationException: If booking is in a non-cancellable state.
        """
        result = await db.execute(
            select(HotelBooking).where(HotelBooking.id == booking_id)
        )
        booking = result.scalars().first()
        if not booking:
            raise NotFoundException("Booking")

        # BUG-09 FIX: Guard both terminal states, not just CHECKED_IN.
        if booking.status in [
            BookingStatusEnum.CHECKED_IN,
            BookingStatusEnum.CHECKED_OUT,
        ]:
            raise ValidationException(
                "Cannot cancel a booking that is checked-in or already completed"
            )

        booking.status = BookingStatusEnum.CANCELLED
        booking.cancelled_at = datetime.now(timezone.utc)
        booking.cancellation_reason = reason
        return booking


# Singleton instances
hotel_crud = CRUDHotel(Hotel)
room_type_crud = CRUDRoomType(RoomType)
hotel_booking_crud = CRUDHotelBooking(HotelBooking)