"""
app/crud/hotels_crud.py

Per Blueprint v2.0 Section 11.1 - Hotels Module

CHANGES FROM ORIGINAL:
1. Removed all lga_id filtering - replaced with PostGIS radius search
2. Added transaction support for atomic booking operations
3. Subscription-tier ranking (Enterprise > Pro > Starter > Free)
4. Enhanced availability checking with overlapping detection
5. Booking cancellation with refund support
6. Converted to AsyncSession (async/await)
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
    Hotel, RoomType, Room, HotelBooking, HotelService,
    RoomStatusEnum, BookingStatusEnum, PaymentStatusEnum
)
from app.models.business_model import Business
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
)
from app.core.constants import DEFAULT_RADIUS_METERS


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
        Search hotels using radius-based filtering (PostGIS).

        Per Blueprint Section 3: "Location model — Radius-based (default 5 km)
        — no LGA dependency"

        Args:
            location: (latitude, longitude) tuple for radius filtering
            radius_meters: Search radius in meters (default 5000 = 5km)
            star_rating: Filter by star rating (1-5)
            facilities: Required facilities list
            min_price: Minimum room price per night
            max_price: Maximum room price per night
            skip: Pagination offset
            limit: Pagination limit

        Returns:
            List of hotels within radius, ranked by subscription tier
        """
        query = (
            select(Hotel)
            .join(Business)
            .where(Business.is_active == True)
            .options(
                joinedload(Hotel.business),
                selectinload(Hotel.room_types),
            )
        )

        # ── Radius-based location filter (PostGIS ST_DWithin) ────────────
        if location:
            lat, lng = location
            point = func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)
            # FIX: Include hotels whose business has no location set
            # ST_DWithin(NULL, ...) returns NULL → falsy → silently filters everything out
            query = query.where(
                or_(
                    Business.location.is_(None),
                    func.ST_DWithin(Business.location, point, radius_meters)
                )
            )

        # Star rating filter
        if star_rating:
            query = query.where(Hotel.star_rating == star_rating)

        # Facilities filter (all required facilities must be present)
        if facilities:
            for facility in facilities:
                query = query.where(Hotel.facilities.contains([facility]))  # fixed: was .filter()

        # Price range filter
        if min_price or max_price:
            query = query.join(RoomType)
            if min_price:
                query = query.where(RoomType.base_price_per_night >= min_price)
            if max_price:
                query = query.where(RoomType.base_price_per_night <= max_price)

        # Blueprint: subscription tier drives search ranking
        # Enterprise(4) > Pro(3) > Starter(2) > Free(1)
        query = query.order_by(Business.subscription_tier.desc())

        result = await db.execute(query.offset(skip).limit(limit))
        return list(result.scalars().all())


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
        Check room availability for given dates.

        Returns the number of rooms still available for the date range.
        Considers overlapping bookings in PENDING, CONFIRMED, or CHECKED_IN status.

        Args:
            room_type_id: Room type to check
            check_in: Check-in date
            check_out: Check-out date
            number_of_rooms: Number of rooms requested

        Returns:
            Number of available rooms (0 if none available)
        """
        # Get room type total
        result = await db.execute(
            select(RoomType).where(RoomType.id == room_type_id)
        )
        room_type = result.scalars().first()
        if not room_type:
            return 0

        # Count overlapping bookings
        # A booking overlaps if:
        # - Its check_in is before our check_out AND
        # - Its check_out is after our check_in
        overlapping_result = await db.execute(
            select(func.sum(HotelBooking.number_of_rooms))
            .where(
                and_(
                    HotelBooking.room_type_id == room_type_id,
                    HotelBooking.status.in_([
                        BookingStatusEnum.PENDING,
                        BookingStatusEnum.CONFIRMED,
                        BookingStatusEnum.CHECKED_IN,
                    ]),
                    or_(
                        and_(
                            HotelBooking.check_in_date < check_out,
                            HotelBooking.check_out_date > check_in,
                        ),
                    ),
                )
            )
        )
        overlapping_booked = overlapping_result.scalar() or 0

        # Available = Total - Booked
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
        Get room types with availability info for specific dates.

        Filters out room types that:
        - Cannot accommodate the number of guests
        - Don't have enough available rooms

        Returns:
            List of room types with 'available_rooms' field added
        """
        room_types = await self.get_by_hotel(db, hotel_id=hotel_id)
        result = []

        for rt in room_types:
            # Skip if room can't accommodate guests
            if rt.max_occupancy < number_of_guests:
                continue

            # Check availability
            available = await self.check_availability(
                db,
                room_type_id=rt.id,
                check_in=check_in,
                check_out=check_out,
                number_of_rooms=number_of_rooms,
            )

            # Include only if enough rooms available
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
                    "amenities": rt.amenities,
                    "base_price_per_night": rt.base_price_per_night,
                    "images": rt.images,
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
        Create a hotel booking (WITHOUT payment processing).

        Payment is handled separately by hotel_service using transaction_service.
        This method only creates the booking record with calculated pricing.

        NOTE: Caller must commit (for transaction atomicity with payment).

        Raises:
            NotFoundException: If room_type not found
            ValidationException: If room_type doesn't belong to hotel or unavailable
        """
        # Verify room type exists and belongs to hotel
        result = await db.execute(
            select(RoomType).where(RoomType.id == room_type_id)
        )
        room_type = result.scalars().first()
        if not room_type:
            raise NotFoundException("Room type")

        if room_type.hotel_id != hotel_id:
            raise ValidationException("Room type does not belong to this hotel")

        # Check availability
        available = await room_type_crud.check_availability(
            db,
            room_type_id=room_type_id,
            check_in=check_in,
            check_out=check_out,
            number_of_rooms=number_of_rooms,
        )
        if available < number_of_rooms:
            raise ValidationException(
                f"Only {available} rooms available for selected dates. "
                f"Requested: {number_of_rooms}"
            )

        # Calculate pricing
        nights = (check_out - check_in).days
        base_price = room_type.base_price_per_night * nights * number_of_rooms

        # Process add-ons pricing
        add_ons_price = Decimal("0.00")
        add_ons_serialised = []
        for item in add_ons:
            # Handle both dict and Pydantic model
            item_dict = item if isinstance(item, dict) else item.model_dump()
            item_price = Decimal(str(item_dict.get("price") or 0))
            item_qty = int(item_dict.get("quantity", 1))
            add_ons_price += item_price * item_qty
            add_ons_serialised.append(item_dict)

        total_price = base_price + add_ons_price

        # Create booking
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
        # NOTE: Do NOT commit here - caller must commit for atomicity
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

        NOTE: Caller must commit.

        Raises:
            NotFoundException: If booking not found
            ValidationException: If booking already checked in
        """
        result = await db.execute(
            select(HotelBooking).where(HotelBooking.id == booking_id)
        )
        booking = result.scalars().first()
        if not booking:
            raise NotFoundException("Booking")

        if booking.status == BookingStatusEnum.CHECKED_IN:
            raise ValidationException("Cannot cancel a checked-in booking")

        booking.status = BookingStatusEnum.CANCELLED
        booking.cancelled_at = datetime.now(timezone.utc)
        booking.cancellation_reason = reason
        return booking


# Singleton instances
hotel_crud = CRUDHotel(Hotel)
room_type_crud = CRUDRoomType(RoomType)
hotel_booking_crud = CRUDHotelBooking(HotelBooking)