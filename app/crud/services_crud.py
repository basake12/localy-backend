# app/crud/services_crud.py

from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func, update as sa_update
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
    BookingNotAvailableException
)


class CRUDServiceProvider(CRUDBase[ServiceProvider, dict, dict]):

    def get_by_business_id(
        self,
        db: Session,
        *,
        business_id: UUID
    ) -> Optional[ServiceProvider]:
        return db.query(ServiceProvider).filter(
            ServiceProvider.business_id == business_id
        ).first()


class CRUDService(CRUDBase[Service, dict, dict]):

    def get_by_provider(
        self,
        db: Session,
        *,
        provider_id: UUID,
        skip: int = 0,
        limit: int = 50,
        active_only: bool = True
    ) -> List[Service]:
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
        location: Optional[tuple] = None,   # (lat, lng) — GPS coordinates
        radius_km: float = 5.0,             # Blueprint default: 5 km
        service_location_type: Optional[str] = None,
        sort_by: str = "created_at",
        skip: int = 0,
        limit: int = 20
    ) -> List[Service]:
        """
        Search services with radius-based location filter.

        Per Blueprint Section 3.1: discovery is strictly radius-based using
        PostGIS ST_DWithin. No LGA or city-name filtering anywhere.

        Eagerly loads provider + business to avoid N+1 in service layer.
        """
        query = (
            db.query(Service)
            .options(
                joinedload(Service.provider)
                .joinedload(ServiceProvider.business)
            )
            .filter(Service.is_active == True)
        )

        if query_text:
            query = query.filter(or_(
                Service.name.ilike(f"%{query_text}%"),
                Service.description.ilike(f"%{query_text}%"),
                Service.category.ilike(f"%{query_text}%")
            ))

        if category:
            query = query.filter(Service.category == category)

        if subcategory:
            query = query.filter(Service.subcategory == subcategory)

        if min_price is not None:
            query = query.filter(Service.base_price >= min_price)

        if max_price is not None:
            query = query.filter(Service.base_price <= max_price)

        # Location filter — radius-based only (Blueprint Section 3.1, no LGA)
        if location:
            lat, lng = location
            point = func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)
            # FIX: Use relationship path for join; include services with no Business.location
            # ST_DWithin(NULL) returns NULL → falsy → silently filters out unlocated providers
            query = (
                query
                .join(Service.provider)
                .join(ServiceProvider.business)
                .filter(or_(
                    Business.location.is_(None),
                    func.ST_DWithin(Business.location, point, radius_km * 1000)
                ))
            )

        if service_location_type:
            # Guard against double-join when location filter already joined provider
            if not location:
                query = query.join(Service.provider)
            query = query.filter(
                ServiceProvider.service_location_types.contains([service_location_type])
            )

        sort_map = {
            "price_asc":  Service.base_price.asc(),
            "price_desc": Service.base_price.desc(),
            "popular":    Service.bookings_count.desc(),
            "rating":     Service.average_rating.desc(),
        }
        query = query.order_by(sort_map.get(sort_by, Service.created_at.desc()))

        return query.offset(skip).limit(limit).all()


class CRUDServiceAvailability(CRUDBase[ServiceAvailability, dict, dict]):

    def get_by_provider(
        self,
        db: Session,
        *,
        provider_id: UUID
    ) -> List[ServiceAvailability]:
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
        day_of_week = booking_date.weekday()

        availability = db.query(ServiceAvailability).filter(
            and_(
                ServiceAvailability.provider_id == provider_id,
                ServiceAvailability.day_of_week == day_of_week,
                ServiceAvailability.is_available == True
            )
        ).first()

        if not availability:
            return []

        slots = []
        current_time = datetime.combine(booking_date, availability.start_time)
        end_time = datetime.combine(booking_date, availability.end_time)
        slot_duration = timedelta(minutes=availability.slot_duration_minutes)

        while current_time + timedelta(minutes=service_duration) <= end_time:
            if availability.break_start and availability.break_end:
                break_start = datetime.combine(booking_date, availability.break_start)
                break_end = datetime.combine(booking_date, availability.break_end)
                if break_start <= current_time < break_end:
                    current_time = break_end
                    continue

            slot_time = current_time.time()
            existing_count = db.query(func.count(ServiceBooking.id)).filter(
                and_(
                    ServiceBooking.provider_id == provider_id,
                    ServiceBooking.booking_date == booking_date,
                    ServiceBooking.booking_time == slot_time,
                    ServiceBooking.status.in_([
                        BookingStatusEnum.PENDING.value,
                        BookingStatusEnum.CONFIRMED.value,
                        BookingStatusEnum.IN_PROGRESS.value
                    ])
                )
            ).scalar()

            available_capacity = availability.max_bookings_per_slot - (existing_count or 0)
            slots.append({
                "slot_time": slot_time,
                "available_capacity": available_capacity,
                "is_available": available_capacity > 0
            })

            current_time += slot_duration

        return slots

    def check_slot_availability(
        self,
        db: Session,
        *,
        provider_id: UUID,
        booking_date: date,
        booking_time: time
    ) -> bool:
        """Pure availability check — no side effects. Used by service layer."""
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

        existing_count = db.query(func.count(ServiceBooking.id)).filter(
            and_(
                ServiceBooking.provider_id == provider_id,
                ServiceBooking.booking_date == booking_date,
                ServiceBooking.booking_time == booking_time,
                ServiceBooking.status.in_([
                    BookingStatusEnum.PENDING.value,
                    BookingStatusEnum.CONFIRMED.value,
                    BookingStatusEnum.IN_PROGRESS.value
                ])
            )
        ).scalar()

        return (existing_count or 0) < availability.max_bookings_per_slot


class CRUDServiceBooking(CRUDBase[ServiceBooking, dict, dict]):
    """
    Pure data-access layer for bookings.
    Business logic (pricing, payment, availability validation) lives in ServiceService.
    """

    def create_booking_record(
        self,
        db: Session,
        *,
        service_id: UUID,
        provider_id: UUID,
        customer_id: UUID,
        booking_date: date,
        booking_time: time,
        duration_minutes: int,
        number_of_people: int,
        service_location_type: str,
        service_address: Optional[str],
        base_price: Decimal,
        add_ons_price: Decimal,
        travel_fee: Decimal,
        total_price: Decimal,
        selected_options: List[Dict],
        special_requests: Optional[str]
    ) -> ServiceBooking:
        booking = ServiceBooking(
            service_id=service_id,
            provider_id=provider_id,
            customer_id=customer_id,
            booking_date=booking_date,
            booking_time=booking_time,
            duration_minutes=duration_minutes,
            number_of_people=number_of_people,
            service_location_type=service_location_type,
            service_address=service_address,
            base_price=base_price,
            add_ons_price=add_ons_price,
            travel_fee=travel_fee,
            total_price=total_price,
            selected_options=selected_options,
            special_requests=special_requests,
        )
        db.add(booking)
        db.flush()  # Get ID without committing — caller controls transaction
        return booking

    def get_customer_bookings(
        self,
        db: Session,
        *,
        customer_id: UUID,
        skip: int = 0,
        limit: int = 20
    ) -> List[ServiceBooking]:
        return (
            db.query(ServiceBooking)
            .options(joinedload(ServiceBooking.service))
            .filter(ServiceBooking.customer_id == customer_id)
            .order_by(
                ServiceBooking.booking_date.desc(),
                ServiceBooking.booking_time.desc()
            )
            .offset(skip).limit(limit).all()
        )

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

    def increment_service_stats(self, db: Session, *, service_id: UUID) -> None:
        """Atomic counter increment — avoids race condition."""
        db.execute(
            sa_update(Service)
            .where(Service.id == service_id)
            .values(bookings_count=Service.bookings_count + 1)
        )

    def increment_provider_stats(self, db: Session, *, provider_id: UUID) -> None:
        """Atomic counter increment — avoids race condition."""
        db.execute(
            sa_update(ServiceProvider)
            .where(ServiceProvider.id == provider_id)
            .values(total_bookings=ServiceProvider.total_bookings + 1)
        )


# ─── Singleton instances ───────────────────────────────────────────────────
service_provider_crud = CRUDServiceProvider(ServiceProvider)
service_crud = CRUDService(Service)
service_availability_crud = CRUDServiceAvailability(ServiceAvailability)
service_booking_crud = CRUDServiceBooking(ServiceBooking)