from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from uuid import UUID
from datetime import date, time, datetime, timedelta
from decimal import Decimal

from app.crud.base_crud import CRUDBase
from app.models.services_model import (
    ServiceProvider, Service, ServiceAvailability,
    ServiceBooking, BookingStatusEnum
)
from app.models.business_model import Business
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    BookingNotAvailableException
)


class CRUDServiceProvider(CRUDBase[ServiceProvider, dict, dict]):
    """CRUD for ServiceProvider"""

    def get_by_business_id(
            self,
            db: Session,
            *,
            business_id: UUID
    ) -> Optional[ServiceProvider]:
        """Get provider by business ID"""
        return db.query(ServiceProvider).filter(
            ServiceProvider.business_id == business_id
        ).first()


class CRUDService(CRUDBase[Service, dict, dict]):
    """CRUD for Service"""

    def get_by_provider(
            self,
            db: Session,
            *,
            provider_id: UUID,
            skip: int = 0,
            limit: int = 50,
            active_only: bool = True
    ) -> List[Service]:
        """Get services by provider"""
        query = db.query(Service).filter(Service.provider_id == provider_id)

        if active_only:
            query = query.filter(Service.is_active == True)

        return query.offset(skip).limit(limit).all()

    def search_services(
            self,
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
    ) -> List[Service]:
        """Search services with filters"""
        query = db.query(Service).filter(Service.is_active == True)

        # Text search
        if query_text:
            search_filter = or_(
                Service.name.ilike(f"%{query_text}%"),
                Service.description.ilike(f"%{query_text}%"),
                Service.category.ilike(f"%{query_text}%")
            )
            query = query.filter(search_filter)

        # Category filters
        if category:
            query = query.filter(Service.category == category)

        if subcategory:
            query = query.filter(Service.subcategory == subcategory)

        # Price range
        if min_price:
            query = query.filter(Service.base_price >= min_price)

        if max_price:
            query = query.filter(Service.base_price <= max_price)

        # Location filter
        if location:
            lat, lng = location
            query = query.join(ServiceProvider).join(Business).filter(
                func.ST_DWithin(
                    Business.location,
                    func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326),
                    radius_km * 1000
                )
            )

        # Service location type filter
        if service_location_type:
            query = query.join(ServiceProvider).filter(
                ServiceProvider.service_location_types.contains([service_location_type])
            )

        # Sorting
        if sort_by == "price_asc":
            query = query.order_by(Service.base_price.asc())
        elif sort_by == "price_desc":
            query = query.order_by(Service.base_price.desc())
        elif sort_by == "popular":
            query = query.order_by(Service.bookings_count.desc())
        elif sort_by == "rating":
            query = query.order_by(Service.average_rating.desc())
        else:
            query = query.order_by(Service.created_at.desc())

        return query.offset(skip).limit(limit).all()


class CRUDServiceAvailability(CRUDBase[ServiceAvailability, dict, dict]):
    """CRUD for ServiceAvailability"""

    def get_by_provider(
            self,
            db: Session,
            *,
            provider_id: UUID
    ) -> List[ServiceAvailability]:
        """Get all availability slots for provider"""
        return db.query(ServiceAvailability).filter(
            ServiceAvailability.provider_id == provider_id
        ).order_by(ServiceAvailability.day_of_week).all()

    def get_available_slots(
            self,
            db: Session,
            *,
            provider_id: UUID,
            service_duration: int,
            booking_date: date
    ) -> List[Dict[str, Any]]:
        """
        Get available time slots for a specific date

        Args:
            provider_id: Provider UUID
            service_duration: Service duration in minutes
            booking_date: Date to check

        Returns:
            List of available slots with times and capacity
        """
        # Get day of week (0=Monday)
        day_of_week = booking_date.weekday()

        # Get availability for this day
        availability = db.query(ServiceAvailability).filter(
            and_(
                ServiceAvailability.provider_id == provider_id,
                ServiceAvailability.day_of_week == day_of_week,
                ServiceAvailability.is_available == True
            )
        ).first()

        if not availability:
            return []

        # Generate time slots
        slots = []
        current_time = datetime.combine(booking_date, availability.start_time)
        end_time = datetime.combine(booking_date, availability.end_time)

        slot_duration = timedelta(minutes=availability.slot_duration_minutes)

        while current_time + timedelta(minutes=service_duration) <= end_time:
            # Skip break time
            if availability.break_start and availability.break_end:
                break_start = datetime.combine(booking_date, availability.break_start)
                break_end = datetime.combine(booking_date, availability.break_end)

                if break_start <= current_time < break_end:
                    current_time = break_end
                    continue

            # Check existing bookings for this slot
            slot_time = current_time.time()
            existing_bookings = db.query(func.count(ServiceBooking.id)).filter(
                and_(
                    ServiceBooking.provider_id == provider_id,
                    ServiceBooking.booking_date == booking_date,
                    ServiceBooking.booking_time == slot_time,
                    ServiceBooking.status.in_([
                        BookingStatusEnum.PENDING,
                        BookingStatusEnum.CONFIRMED,
                        BookingStatusEnum.IN_PROGRESS
                    ])
                )
            ).scalar()

            available_capacity = availability.max_bookings_per_slot - (existing_bookings or 0)

            slots.append({
                "slot_time": slot_time,
                "available_capacity": available_capacity,
                "is_available": available_capacity > 0
            })

            current_time += slot_duration

        return slots


class CRUDServiceBooking(CRUDBase[ServiceBooking, dict, dict]):
    """CRUD for ServiceBooking"""

    def check_availability(
            self,
            db: Session,
            *,
            provider_id: UUID,
            booking_date: date,
            booking_time: time
    ) -> bool:
        """Check if slot is available"""
        # Get availability settings
        day_of_week = booking_date.weekday()
        availability = db.query(ServiceAvailability).filter(
            and_(
                ServiceAvailability.provider_id == provider_id,
                ServiceAvailability.day_of_week == day_of_week,
                ServiceAvailability.is_available == True
            )
        ).first()

        if not availability:
            return False

        # Count existing bookings
        existing_count = db.query(func.count(ServiceBooking.id)).filter(
            and_(
                ServiceBooking.provider_id == provider_id,
                ServiceBooking.booking_date == booking_date,
                ServiceBooking.booking_time == booking_time,
                ServiceBooking.status.in_([
                    BookingStatusEnum.PENDING,
                    BookingStatusEnum.CONFIRMED,
                    BookingStatusEnum.IN_PROGRESS
                ])
            )
        ).scalar()

        return (existing_count or 0) < availability.max_bookings_per_slot

    def create_booking(
            self,
            db: Session,
            *,
            service_id: UUID,
            customer_id: UUID,
            booking_date: date,
            booking_time: time,
            number_of_people: int,
            service_location_type: str,
            service_address: Optional[str],
            selected_options: List[Dict],
            special_requests: Optional[str]
    ) -> ServiceBooking:
        """Create a new booking"""
        # Get service
        service = service_crud.get(db, id=service_id)
        if not service or not service.is_active:
            raise NotFoundException("Service")

        # Get provider
        provider = service_provider_crud.get(db, id=service.provider_id)
        if not provider:
            raise NotFoundException("Provider")

        # Check availability
        if not self.check_availability(
                db,
                provider_id=service.provider_id,
                booking_date=booking_date,
                booking_time=booking_time
        ):
            raise BookingNotAvailableException()

        # Calculate pricing
        base_price = service.base_price
        add_ons_price = Decimal('0.00')

        # Calculate add-ons from selected options
        for option in selected_options:
            if 'price' in option:
                add_ons_price += Decimal(str(option['price']))

        # Calculate travel fee if in-home service
        travel_fee = Decimal('0.00')
        if service_location_type == "in_home":
            travel_fee = provider.travel_fee

        total_price = base_price + add_ons_price + travel_fee

        # Create booking
        booking = ServiceBooking(
            service_id=service_id,
            provider_id=service.provider_id,
            customer_id=customer_id,
            booking_date=booking_date,
            booking_time=booking_time,
            duration_minutes=service.duration_minutes or 60,
            number_of_people=number_of_people,
            service_location_type=service_location_type,
            service_address=service_address,
            base_price=base_price,
            add_ons_price=add_ons_price,
            travel_fee=travel_fee,
            total_price=total_price,
            selected_options=selected_options,
            special_requests=special_requests
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
            limit: int = 20
    ) -> List[ServiceBooking]:
        """Get customer bookings"""
        return db.query(ServiceBooking).options(
            joinedload(ServiceBooking.service)
        ).filter(
            ServiceBooking.customer_id == customer_id
        ).order_by(
            ServiceBooking.booking_date.desc(),
            ServiceBooking.booking_time.desc()
        ).offset(skip).limit(limit).all()

    def get_provider_bookings(
            self,
            db: Session,
            *,
            provider_id: UUID,
            date_from: Optional[date] = None,
            date_to: Optional[date] = None,
            status: Optional[str] = None,
            skip: int = 0,
            limit: int = 50
    ) -> List[ServiceBooking]:
        """Get provider bookings"""
        query = db.query(ServiceBooking).filter(
            ServiceBooking.provider_id == provider_id
        )

        if date_from:
            query = query.filter(ServiceBooking.booking_date >= date_from)

        if date_to:
            query = query.filter(ServiceBooking.booking_date <= date_to)

        if status:
            query = query.filter(ServiceBooking.status == status)

        return query.order_by(
            ServiceBooking.booking_date,
            ServiceBooking.booking_time
        ).offset(skip).limit(limit).all()


# Singleton instances
service_provider_crud = CRUDServiceProvider(ServiceProvider)
service_crud = CRUDService(Service)
service_availability_crud = CRUDServiceAvailability(ServiceAvailability)
service_booking_crud = CRUDServiceBooking(ServiceBooking)