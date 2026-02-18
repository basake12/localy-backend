from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, func, or_
from uuid import UUID
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.crud.base import CRUDBase
from app.models.hotels import (
    Hotel, RoomType, Room, HotelBooking, HotelService,
    RoomStatusEnum, BookingStatusEnum
)
from app.models.business import Business
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    BookingNotAvailableException
)


class CRUDHotel(CRUDBase[Hotel, dict, dict]):
    """CRUD operations for Hotel"""

    def get_by_business_id(self, db: Session, *, business_id: UUID) -> Optional[Hotel]:
        """Get hotel by business ID"""
        return db.query(Hotel).filter(Hotel.business_id == business_id).first()

    def get_with_room_types(self, db: Session, *, hotel_id: UUID) -> Optional[Hotel]:
        """Get hotel with all room types"""
        return db.query(Hotel).options(
            joinedload(Hotel.room_types),
            joinedload(Hotel.business)
        ).filter(Hotel.id == hotel_id).first()

    def search_hotels(
            self,
            db: Session,
            *,
            skip: int = 0,
            limit: int = 20,
            location: Optional[tuple] = None,
            radius_km: float = 10.0,
            star_rating: Optional[int] = None,
            facilities: Optional[List[str]] = None,
            min_price: Optional[Decimal] = None,
            max_price: Optional[Decimal] = None
    ) -> List[Hotel]:
        """
        Search hotels with filters

        Args:
            db: Database session
            skip: Pagination offset
            limit: Results limit
            location: (latitude, longitude) tuple
            radius_km: Search radius in kilometers
            star_rating: Filter by star rating
            facilities: Required facilities
            min_price: Minimum room price
            max_price: Maximum room price
        """
        query = db.query(Hotel).join(Business)

        # Location-based filtering
        if location:
            lat, lng = location
            # PostGIS distance query
            query = query.filter(
                func.ST_DWithin(
                    Business.location,
                    func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326),
                    radius_km * 1000  # Convert to meters
                )
            )

        # Star rating filter
        if star_rating:
            query = query.filter(Hotel.star_rating == star_rating)

        # Facilities filter (JSONB contains)
        if facilities:
            for facility in facilities:
                query = query.filter(
                    Hotel.facilities.contains([facility])
                )

        # Price filter (check room types)
        if min_price or max_price:
            query = query.join(RoomType)
            if min_price:
                query = query.filter(RoomType.base_price_per_night >= min_price)
            if max_price:
                query = query.filter(RoomType.base_price_per_night <= max_price)

        # Only active businesses
        query = query.filter(Business.is_active == True)

        return query.offset(skip).limit(limit).all()


class CRUDRoomType(CRUDBase[RoomType, dict, dict]):
    """CRUD operations for RoomType"""

    def get_by_hotel(
            self,
            db: Session,
            *,
            hotel_id: UUID,
            skip: int = 0,
            limit: int = 50
    ) -> List[RoomType]:
        """Get all room types for a hotel"""
        return db.query(RoomType).filter(
            RoomType.hotel_id == hotel_id
        ).offset(skip).limit(limit).all()

    def check_availability(
            self,
            db: Session,
            *,
            room_type_id: UUID,
            check_in: date,
            check_out: date,
            number_of_rooms: int = 1
    ) -> int:
        """
        Check available rooms for date range

        Returns:
            Number of available rooms
        """
        room_type = self.get(db, id=room_type_id)
        if not room_type:
            return 0

        # Get overlapping bookings
        overlapping_bookings = db.query(
            func.sum(HotelBooking.number_of_rooms)
        ).filter(
            and_(
                HotelBooking.room_type_id == room_type_id,
                HotelBooking.status.in_([
                    BookingStatusEnum.PENDING,
                    BookingStatusEnum.CONFIRMED,
                    BookingStatusEnum.CHECKED_IN
                ]),
                or_(
                    and_(
                        HotelBooking.check_in_date <= check_in,
                        HotelBooking.check_out_date > check_in
                    ),
                    and_(
                        HotelBooking.check_in_date < check_out,
                        HotelBooking.check_out_date >= check_out
                    ),
                    and_(
                        HotelBooking.check_in_date >= check_in,
                        HotelBooking.check_out_date <= check_out
                    )
                )
            )
        ).scalar() or 0

        available = room_type.total_rooms - overlapping_bookings
        return max(0, available)

    def get_available_room_types(
            self,
            db: Session,
            *,
            hotel_id: UUID,
            check_in: date,
            check_out: date,
            number_of_rooms: int = 1,
            number_of_guests: int = 1
    ) -> List[Dict[str, Any]]:
        """
        Get available room types with availability info

        Returns:
            List of room types with 'available_rooms' field
        """
        room_types = self.get_by_hotel(db, hotel_id=hotel_id)

        result = []
        for room_type in room_types:
            # Check if can accommodate guests
            if room_type.max_occupancy < number_of_guests:
                continue

            # Check availability
            available = self.check_availability(
                db,
                room_type_id=room_type.id,
                check_in=check_in,
                check_out=check_out,
                number_of_rooms=number_of_rooms
            )

            if available >= number_of_rooms:
                result.append({
                    **room_type.__dict__,
                    'available_rooms': available
                })

        return result


class CRUDHotelBooking(CRUDBase[HotelBooking, dict, dict]):
    """CRUD operations for HotelBooking"""

    def create_booking(
            self,
            db: Session,
            *,
            hotel_id: UUID,
            room_type_id: UUID,
            customer_id: UUID,
            check_in: date,
            check_out: date,
            number_of_rooms: int,
            number_of_guests: int,
            add_ons: List[dict],
            special_requests: Optional[str] = None
    ) -> HotelBooking:
        """
        Create a new booking

        Args:
            All booking details

        Returns:
            Created booking

        Raises:
            NotFoundException: If room type not found
            BookingNotAvailableException: If rooms not available
            ValidationException: If validation fails
        """
        # Validate room type
        room_type = room_type_crud.get(db, id=room_type_id)
        if not room_type:
            raise NotFoundException("Room type")

        if room_type.hotel_id != hotel_id:
            raise ValidationException("Room type does not belong to this hotel")

        # Check availability
        available = room_type_crud.check_availability(
            db,
            room_type_id=room_type_id,
            check_in=check_in,
            check_out=check_out,
            number_of_rooms=number_of_rooms
        )

        if available < number_of_rooms:
            raise BookingNotAvailableException()

        # Calculate pricing
        nights = (check_out - check_in).days
        base_price = room_type.base_price_per_night * nights * number_of_rooms

        # Calculate add-ons price
        add_ons_price = Decimal('0.00')
        # TODO: Implement add-ons pricing logic

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
            add_ons=add_ons,
            special_requests=special_requests,
            status=BookingStatusEnum.PENDING
        )

        db.add(booking)
        db.commit()
        db.refresh(booking)

        return booking

    def get_customer_bookings(
            self,
            db: Session,
            *,
            customer_id: UUID,
            skip: int = 0,
            limit: int = 20,
            status: Optional[str] = None
    ) -> List[HotelBooking]:
        """Get customer's bookings"""
        query = db.query(HotelBooking).filter(
            HotelBooking.customer_id == customer_id
        )

        if status:
            query = query.filter(HotelBooking.status == status)

        return query.order_by(
            HotelBooking.created_at.desc()
        ).offset(skip).limit(limit).all()

    def get_hotel_bookings(
            self,
            db: Session,
            *,
            hotel_id: UUID,
            skip: int = 0,
            limit: int = 50,
            status: Optional[str] = None,
            date_from: Optional[date] = None,
            date_to: Optional[date] = None
    ) -> List[HotelBooking]:
        """Get hotel's bookings"""
        query = db.query(HotelBooking).filter(
            HotelBooking.hotel_id == hotel_id
        )

        if status:
            query = query.filter(HotelBooking.status == status)

        if date_from:
            query = query.filter(HotelBooking.check_in_date >= date_from)

        if date_to:
            query = query.filter(HotelBooking.check_out_date <= date_to)

        return query.order_by(
            HotelBooking.check_in_date
        ).offset(skip).limit(limit).all()

    def confirm_booking(self, db: Session, *, booking_id: UUID) -> HotelBooking:
        """Confirm a booking"""
        booking = self.get(db, id=booking_id)
        if not booking:
            raise NotFoundException("Booking")

        booking.status = BookingStatusEnum.CONFIRMED
        db.commit()
        db.refresh(booking)

        return booking

    def cancel_booking(
            self,
            db: Session,
            *,
            booking_id: UUID,
            reason: Optional[str] = None
    ) -> HotelBooking:
        """Cancel a booking"""
        booking = self.get(db, id=booking_id)
        if not booking:
            raise NotFoundException("Booking")

        if booking.status == BookingStatusEnum.CHECKED_IN:
            raise ValidationException("Cannot cancel checked-in booking")

        booking.status = BookingStatusEnum.CANCELLED
        booking.cancelled_at = datetime.utcnow()
        booking.cancellation_reason = reason

        db.commit()
        db.refresh(booking)

        return booking


# Singleton instances
hotel_crud = CRUDHotel(Hotel)
room_type_crud = CRUDRoomType(RoomType)
hotel_booking_crud = CRUDHotelBooking(HotelBooking)