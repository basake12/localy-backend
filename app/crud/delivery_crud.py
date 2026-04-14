from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from uuid import UUID
from datetime import datetime
from decimal import Decimal
import random
import string

from app.crud.base_crud import CRUDBase
from app.models.delivery_model import (
    Delivery,
    DeliveryTracking,
    RiderEarnings,
    DeliveryZone,
    RiderShift,
    DeliveryStatusEnum,
)
from app.models.rider_model import Rider
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
)


class CRUDDelivery(CRUDBase[Delivery, dict, dict]):
    """CRUD for Delivery"""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_tracking_code(self, db: Session, max_attempts: int = 20) -> str:
        """Generate a unique tracking code (LCL + 10 alphanumeric chars)."""
        for _ in range(max_attempts):
            suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
            code = f"LCL{suffix}"
            if not db.query(Delivery).filter(Delivery.tracking_code == code).first():
                return code
        raise RuntimeError("Failed to generate unique tracking code after max attempts")

    def _calculate_distance(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
    ) -> Decimal:
        """Haversine distance between two lat/lon pairs, in km."""
        from math import radians, cos, sin, asin, sqrt

        lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        c = 2 * asin(sqrt(a))
        r = 6371  # Earth radius in km
        return Decimal(str(round(c * r, 2)))

    def _calculate_pricing(
        self,
        db: Session,
        distance_km: Decimal,
        order_type: str,
        zone: Optional[DeliveryZone] = None,
    ) -> Dict[str, Decimal]:
        """
        Calculate delivery pricing.

        Tries the zone's configured rates first; falls back to
        hardcoded defaults when no zone is available.
        """
        if zone:
            base_fee = zone.base_fee
            per_km_fee = zone.per_km_fee
        else:
            # Fallback defaults by order type (₦)
            base_fees: Dict[str, Decimal] = {
                "product": Decimal("1500.00"),
                "food": Decimal("1000.00"),
                "parcel": Decimal("2000.00"),
                "document": Decimal("800.00"),
                "prescription": Decimal("1200.00"),
            }
            base_fee = base_fees.get(order_type, Decimal("1500.00"))
            per_km_fee = Decimal("150.00")

        distance_fee = distance_km * per_km_fee
        total_fee = base_fee + distance_fee

        return {
            "base_fee": base_fee,
            "distance_fee": distance_fee,
            "total_fee": total_fee,
        }

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_delivery(
        self,
        db: Session,
        *,
        customer_id: UUID,
        order_type: str,
        pickup_address: str,
        pickup_location: tuple,
        pickup_contact_name: str,
        pickup_contact_phone: str,
        dropoff_address: str,
        dropoff_location: tuple,
        dropoff_contact_name: str,
        dropoff_contact_phone: str,
        package_description: Optional[str] = None,
        package_weight_kg: Optional[Decimal] = None,
        order_id: Optional[UUID] = None,
        payment_method: str = "wallet",
        cod_amount: Decimal = Decimal("0.00"),
        pickup_instructions: Optional[str] = None,
        dropoff_instructions: Optional[str] = None,
        requires_cold_storage: bool = False,
        is_fragile: bool = False,
        required_vehicle_type: Optional[str] = None,
        package_value: Optional[Decimal] = None,
    ) -> Delivery:
        """Create a new delivery record and an initial PENDING tracking entry."""
        from geoalchemy2.elements import WKTElement

        pickup_lat, pickup_lng = pickup_location
        dropoff_lat, dropoff_lng = dropoff_location

        distance_km = self._calculate_distance(
            pickup_lat, pickup_lng, dropoff_lat, dropoff_lng
        )

        # Attempt to use zone pricing
        zone = delivery_zone_crud.get_zone_for_location(
            db, latitude=pickup_lat, longitude=pickup_lng
        )
        pricing = self._calculate_pricing(db, distance_km, order_type, zone=zone)

        tracking_code = self._generate_tracking_code(db)

        delivery = Delivery(
            customer_id=customer_id,
            order_id=order_id,
            order_type=order_type,
            pickup_address=pickup_address,
            pickup_location=WKTElement(f"POINT({pickup_lng} {pickup_lat})", srid=4326),
            pickup_contact_name=pickup_contact_name,
            pickup_contact_phone=pickup_contact_phone,
            pickup_instructions=pickup_instructions,
            dropoff_address=dropoff_address,
            dropoff_location=WKTElement(f"POINT({dropoff_lng} {dropoff_lat})", srid=4326),
            dropoff_contact_name=dropoff_contact_name,
            dropoff_contact_phone=dropoff_contact_phone,
            dropoff_instructions=dropoff_instructions,
            package_description=package_description,
            package_weight_kg=package_weight_kg,
            package_value=package_value,
            requires_cold_storage=requires_cold_storage,
            is_fragile=is_fragile,
            required_vehicle_type=required_vehicle_type,
            base_fee=pricing["base_fee"],
            distance_fee=pricing["distance_fee"],
            total_fee=pricing["total_fee"],
            estimated_distance_km=distance_km,
            tracking_code=tracking_code,
            payment_method=payment_method,
            cod_amount=cod_amount,
        )

        db.add(delivery)
        db.flush()  # get delivery.id before adding tracking

        tracking = DeliveryTracking(
            delivery_id=delivery.id,
            status=DeliveryStatusEnum.PENDING,
            notes="Delivery created",
            updated_by="system",
        )
        db.add(tracking)

        db.commit()
        db.refresh(delivery)
        return delivery

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_by_tracking_code(
        self, db: Session, *, tracking_code: str
    ) -> Optional[Delivery]:
        return (
            db.query(Delivery)
            .filter(Delivery.tracking_code == tracking_code)
            .first()
        )

    def get_customer_deliveries(
        self,
        db: Session,
        *,
        customer_id: UUID,
        skip: int = 0,
        limit: int = 20,
        status: Optional[str] = None,
    ) -> List[Delivery]:
        query = db.query(Delivery).filter(Delivery.customer_id == customer_id)
        if status:
            query = query.filter(Delivery.status == status)
        return query.order_by(Delivery.created_at.desc()).offset(skip).limit(limit).all()

    def get_rider_deliveries(
        self,
        db: Session,
        *,
        rider_id: UUID,
        skip: int = 0,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> List[Delivery]:
        query = db.query(Delivery).filter(Delivery.rider_id == rider_id)
        if status:
            query = query.filter(Delivery.status == status)
        return query.order_by(Delivery.created_at.desc()).offset(skip).limit(limit).all()

    def find_available_riders(
        self,
        db: Session,
        *,
        pickup_location: tuple,
        radius_km: float = 10.0,
        vehicle_type: Optional[str] = None,
    ) -> List[Rider]:
        """Return online, available, verified riders within radius_km of pickup."""
        lat, lng = pickup_location

        query = db.query(Rider).filter(
            and_(
                Rider.is_online == True,
                Rider.is_active == True,
                Rider.is_verified == True,
            )
        )

        if vehicle_type:
            query = query.filter(Rider.vehicle_type == vehicle_type)

        # ST_DWithin on Geography type uses metres
        query = query.filter(
            func.ST_DWithin(
                Rider.current_location,
                func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326),
                radius_km * 1000,
            )
        )

        return query.order_by(Rider.average_rating.desc()).limit(10).all()

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def assign_rider(
        self,
        db: Session,
        *,
        delivery_id: UUID,
        rider_id: UUID,
    ) -> Delivery:
        """Assign a rider to a delivery and mark the rider as unavailable."""
        delivery = self.get(db, id=delivery_id)
        if not delivery:
            raise NotFoundException("Delivery")

        # FIX: use db.get() — Session.query(...).get() deprecated in SQLAlchemy 2.0
        rider = db.get(Rider, rider_id)
        if not rider:
            raise NotFoundException("Rider")

        if not rider.is_online or not rider.is_active:
            raise ValidationException("Rider is not available")

        delivery.rider_id = rider_id
        delivery.status = DeliveryStatusEnum.ASSIGNED
        delivery.assigned_at = datetime.utcnow()

        # rider stays online — they're now on a job

        tracking = DeliveryTracking(
            delivery_id=delivery_id,
            status=DeliveryStatusEnum.ASSIGNED,
            notes=f"Assigned to rider",
            updated_by="system",
        )
        db.add(tracking)

        db.commit()
        db.refresh(delivery)
        return delivery

    def update_delivery_status(
        self,
        db: Session,
        *,
        delivery_id: UUID,
        new_status: str,
        notes: Optional[str] = None,
        location: Optional[tuple] = None,
        updated_by: str = "rider",
    ) -> Delivery:
        """Advance delivery status and append a tracking entry."""
        delivery = self.get(db, id=delivery_id)
        if not delivery:
            raise NotFoundException("Delivery")

        delivery.status = new_status

        if new_status == DeliveryStatusEnum.PICKED_UP:
            delivery.picked_up_at = datetime.utcnow()
        elif new_status == DeliveryStatusEnum.DELIVERED:
            delivery.delivered_at = datetime.utcnow()
            if delivery.rider_id:
                # FIX: use db.get() — deprecated .get() removed
                rider = db.get(Rider, delivery.rider_id)
                if rider:
                    rider.total_deliveries += 1

        tracking_data: Dict[str, Any] = {
            "delivery_id": delivery_id,
            "status": new_status,
            "notes": notes,
            "updated_by": updated_by,
        }

        if location:
            from geoalchemy2.elements import WKTElement

            lat, lng = location
            tracking_data["location"] = WKTElement(f"POINT({lng} {lat})", srid=4326)

        db.add(DeliveryTracking(**tracking_data))
        db.commit()
        db.refresh(delivery)
        return delivery


    def get_active_job_for_rider(
        self,
        db: Session,
        *,
        rider_id: UUID,
    ) -> Optional[Delivery]:
        """Return the rider's current in-progress delivery, or None."""
        active_statuses = [
            DeliveryStatusEnum.ASSIGNED,
            DeliveryStatusEnum.PICKED_UP,
            DeliveryStatusEnum.IN_TRANSIT,
            DeliveryStatusEnum.ARRIVED,
        ]
        return (
            db.query(Delivery)
            .filter(
                and_(
                    Delivery.rider_id == rider_id,
                    Delivery.status.in_(active_statuses),
                )
            )
            .order_by(Delivery.assigned_at.desc())
            .first()
        )

    def get_available_jobs_for_rider(
        self,
        db: Session,
        *,
        rider: "Rider",  # type: ignore[name-defined]
        radius_km: float = 15.0,
        limit: int = 20,
    ) -> List[Delivery]:
        """
        Return PENDING deliveries near the rider's current location.
        Falls back to all pending jobs if the rider has no location set.
        """
        query = db.query(Delivery).filter(
            Delivery.status == DeliveryStatusEnum.PENDING
        )

        if rider.current_location is not None:
            query = query.filter(
                func.ST_DWithin(
                    Delivery.pickup_location,
                    rider.current_location,
                    radius_km * 1000,  # Geography uses metres
                )
            ).order_by(
                func.ST_Distance(Delivery.pickup_location, rider.current_location)
            )
        else:
            query = query.order_by(Delivery.created_at.desc())

        return query.limit(limit).all()

    def accept_job(
        self,
        db: Session,
        *,
        job_id: UUID,
        rider_id: UUID,
    ) -> Optional[Delivery]:
        """
        Atomically accept a pending delivery job for a rider.
        Returns None if the job doesn't exist or is already taken.
        """
        delivery = (
            db.query(Delivery)
            .filter(
                and_(
                    Delivery.id == job_id,
                    Delivery.status == DeliveryStatusEnum.PENDING,
                    Delivery.rider_id.is_(None),
                )
            )
            .first()
        )
        if not delivery:
            return None

        delivery.rider_id = rider_id
        delivery.status = DeliveryStatusEnum.ASSIGNED
        delivery.assigned_at = datetime.utcnow()

        tracking = DeliveryTracking(
            delivery_id=job_id,
            status=DeliveryStatusEnum.ASSIGNED,
            notes="Rider accepted the job",
            updated_by="rider",
        )
        db.add(tracking)
        db.commit()
        db.refresh(delivery)
        return delivery

    def update_job_status(
        self,
        db: Session,
        *,
        job_id: UUID,
        rider_id: UUID,
        new_status: str,
    ) -> Optional[Delivery]:
        """
        Advance a delivery's status.  Returns None if the job doesn't
        belong to the rider or isn't found.
        """
        delivery = (
            db.query(Delivery)
            .filter(
                and_(
                    Delivery.id == job_id,
                    Delivery.rider_id == rider_id,
                )
            )
            .first()
        )
        if not delivery:
            return None

        status_map = {
            "picked_up": DeliveryStatusEnum.PICKED_UP,
            "in_transit": DeliveryStatusEnum.IN_TRANSIT,
            "delivered": DeliveryStatusEnum.DELIVERED,
            "cancelled": DeliveryStatusEnum.CANCELLED,
        }
        delivery.status = status_map.get(new_status, new_status)

        if new_status == "picked_up":
            delivery.picked_up_at = datetime.utcnow()
        elif new_status == "delivered":
            delivery.delivered_at = datetime.utcnow()
        elif new_status == "cancelled":
            delivery.cancelled_at = datetime.utcnow()
            delivery.cancelled_by = "rider"

        tracking = DeliveryTracking(
            delivery_id=job_id,
            status=delivery.status,
            notes=f"Status updated to {new_status}",
            updated_by="rider",
        )
        db.add(tracking)
        db.commit()
        db.refresh(delivery)
        return delivery

    def get_earnings_summary(
        self,
        db: Session,
        *,
        rider_id: UUID,
    ) -> dict:
        """
        Aggregate earnings broken down by today / this week / this month / lifetime.
        """
        from sqlalchemy import cast, Date
        from datetime import date, timedelta

        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)

        def _sum(date_filter) -> Decimal:
            q = db.query(func.coalesce(func.sum(RiderEarnings.net_earning), 0)).filter(
                RiderEarnings.rider_id == rider_id
            )
            if date_filter is not None:
                q = q.filter(date_filter)
            result = q.scalar()
            return Decimal(str(result))

        today_earnings = _sum(cast(RiderEarnings.created_at, Date) == today)
        week_earnings = _sum(cast(RiderEarnings.created_at, Date) >= week_start)
        month_earnings = _sum(cast(RiderEarnings.created_at, Date) >= month_start)
        lifetime_earnings = _sum(None)

        total_distance = db.query(
            func.coalesce(func.sum(Delivery.actual_distance_km), 0)
        ).filter(
            and_(
                Delivery.rider_id == rider_id,
                Delivery.status == DeliveryStatusEnum.DELIVERED,
            )
        ).scalar()

        return {
            "today": today_earnings,
            "this_week": week_earnings,
            "this_month": month_earnings,
            "lifetime": lifetime_earnings,
            "total_distance_km": Decimal(str(total_distance)),
        }


class CRUDDeliveryZone(CRUDBase[DeliveryZone, dict, dict]):
    """CRUD for DeliveryZone"""

    def get_zone_for_location(
        self,
        db: Session,
        *,
        latitude: float,
        longitude: float,
    ) -> Optional[DeliveryZone]:
        """
        Return the smallest active zone whose center is within radius_km of
        the given coordinate.  Falls back to None (caller uses default pricing).
        """
        from math import radians, cos, sin, asin, sqrt

        def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
            lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
            dlon = lon2 - lon1
            dlat = lat2 - lat1
            a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
            return 2 * asin(sqrt(a)) * 6371

        active_zones = (
            db.query(DeliveryZone).filter(DeliveryZone.is_active == True).all()
        )

        candidates: List[DeliveryZone] = []
        for zone in active_zones:
            # Extract zone centre coords from Geography field via PostGIS helpers
            # when a full GIS pipeline is available.  For now we rely on a
            # dedicated centre_lat / centre_lng if added, or skip gracefully.
            try:
                centre = db.execute(
                    func.ST_AsText(zone.center_location)
                ).scalar()
                # centre is like "POINT(7.3986 9.0765)"
                parts = centre.replace("POINT(", "").replace(")", "").split()
                c_lng, c_lat = float(parts[0]), float(parts[1])
                dist = haversine(latitude, longitude, c_lat, c_lng)
                if dist <= float(zone.radius_km):
                    candidates.append((dist, zone))
            except Exception:
                continue

        if not candidates:
            return None

        # Return the most specific (smallest radius) matching zone
        candidates.sort(key=lambda x: float(x[1].radius_km))
        return candidates[0][1]

    def create_zone(
        self,
        db: Session,
        *,
        name: str,
        state: str,
        local_government: str,
        center_lat: float,
        center_lng: float,
        radius_km: Decimal,
        base_fee: Decimal,
        per_km_fee: Decimal,
        peak_hours: list,
    ) -> DeliveryZone:
        from geoalchemy2.elements import WKTElement

        zone = DeliveryZone(
            name=name,
            state=state,
            local_government=local_government,
            center_location=WKTElement(
                f"POINT({center_lng} {center_lat})", srid=4326
            ),
            radius_km=radius_km,
            base_fee=base_fee,
            per_km_fee=per_km_fee,
            peak_hours=peak_hours,
        )
        db.add(zone)
        db.commit()
        db.refresh(zone)
        return zone


class CRUDRiderEarnings(CRUDBase[RiderEarnings, dict, dict]):
    """CRUD for RiderEarnings"""

    PLATFORM_COMMISSION_RATE = Decimal("0.20")  # 20 %

    def create_earnings(
        self,
        db: Session,
        *,
        rider_id: UUID,
        delivery_id: UUID,
        delivery_fee: Decimal,
        tip: Decimal = Decimal("0.00"),
        distance_bonus: Decimal = Decimal("0.00"),
        peak_hour_bonus: Decimal = Decimal("0.00"),
    ) -> RiderEarnings:
        """Record earnings for a completed delivery (80 / 20 split)."""
        total_earning = delivery_fee + tip + distance_bonus + peak_hour_bonus
        platform_commission = total_earning * self.PLATFORM_COMMISSION_RATE
        net_earning = total_earning - platform_commission

        earnings = RiderEarnings(
            rider_id=rider_id,
            delivery_id=delivery_id,
            base_earning=delivery_fee,
            tip=tip,
            distance_bonus=distance_bonus,
            peak_hour_bonus=peak_hour_bonus,
            total_earning=total_earning,
            platform_commission=platform_commission,
            net_earning=net_earning,
        )

        db.add(earnings)
        db.commit()
        db.refresh(earnings)
        return earnings

    def get_rider_earnings(
        self,
        db: Session,
        *,
        rider_id: UUID,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[RiderEarnings]:
        query = db.query(RiderEarnings).filter(RiderEarnings.rider_id == rider_id)
        if date_from:
            query = query.filter(RiderEarnings.created_at >= date_from)
        if date_to:
            query = query.filter(RiderEarnings.created_at <= date_to)
        return query.order_by(RiderEarnings.created_at.desc()).offset(skip).limit(limit).all()


# ---------------------------------------------------------------------------
# Singleton instances
# ---------------------------------------------------------------------------
delivery_crud = CRUDDelivery(Delivery)
delivery_zone_crud = CRUDDeliveryZone(DeliveryZone)
rider_earnings_crud = CRUDRiderEarnings(RiderEarnings)