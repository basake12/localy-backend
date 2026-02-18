from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from uuid import UUID
from datetime import datetime, timedelta
from decimal import Decimal
import random
import string

from app.crud.base import CRUDBase
from app.models.delivery import (
    Delivery, DeliveryTracking, RiderEarnings,
    DeliveryZone, RiderShift, DeliveryStatusEnum
)
from app.models.rider import Rider
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    BookingNotAvailableException
)


class CRUDDelivery(CRUDBase[Delivery, dict, dict]):
    """CRUD for Delivery"""

    def _generate_tracking_code(self, db: Session) -> str:
        """Generate unique tracking code"""
        while True:
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
            tracking_code = f"LCL{code}"

            existing = db.query(Delivery).filter(
                Delivery.tracking_code == tracking_code
            ).first()

            if not existing:
                return tracking_code

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
            cod_amount: Decimal = Decimal('0.00')
    ) -> Delivery:
        """Create a new delivery"""
        from geoalchemy2.elements import WKTElement

        # Calculate distance
        pickup_lat, pickup_lng = pickup_location
        dropoff_lat, dropoff_lng = dropoff_location

        # Simple distance calculation (haversine)
        distance_km = self._calculate_distance(
            pickup_lat, pickup_lng,
            dropoff_lat, dropoff_lng
        )

        # Calculate pricing
        pricing = self._calculate_pricing(db, distance_km, order_type)

        # Generate tracking code
        tracking_code = self._generate_tracking_code(db)

        # Create delivery
        delivery = Delivery(
            customer_id=customer_id,
            order_id=order_id,
            order_type=order_type,
            pickup_address=pickup_address,
            pickup_location=WKTElement(f"POINT({pickup_lng} {pickup_lat})", srid=4326),
            pickup_contact_name=pickup_contact_name,
            pickup_contact_phone=pickup_contact_phone,
            dropoff_address=dropoff_address,
            dropoff_location=WKTElement(f"POINT({dropoff_lng} {dropoff_lat})", srid=4326),
            dropoff_contact_name=dropoff_contact_name,
            dropoff_contact_phone=dropoff_contact_phone,
            package_description=package_description,
            package_weight_kg=package_weight_kg,
            base_fee=pricing['base_fee'],
            distance_fee=pricing['distance_fee'],
            total_fee=pricing['total_fee'],
            estimated_distance_km=distance_km,
            tracking_code=tracking_code,
            payment_method=payment_method,
            cod_amount=cod_amount
        )

        db.add(delivery)
        db.flush()

        # Create initial tracking update
        tracking = DeliveryTracking(
            delivery_id=delivery.id,
            status=DeliveryStatusEnum.PENDING,
            notes="Delivery created",
            updated_by="system"
        )
        db.add(tracking)

        db.commit()
        db.refresh(delivery)

        return delivery

    def _calculate_distance(
            self,
            lat1: float,
            lon1: float,
            lat2: float,
            lon2: float
    ) -> Decimal:
        """Calculate distance between two points (haversine formula)"""
        from math import radians, cos, sin, asin, sqrt

        # Convert to radians
        lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

        # Haversine formula
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        c = 2 * asin(sqrt(a))

        # Radius of earth in kilometers
        r = 6371

        return Decimal(str(round(c * r, 2)))

    def _calculate_pricing(
            self,
            db: Session,
            distance_km: Decimal,
            order_type: str
    ) -> Dict[str, Decimal]:
        """Calculate delivery pricing"""
        # Base fees by type
        base_fees = {
            "product": Decimal('1500.00'),
            "food": Decimal('1000.00'),
            "parcel": Decimal('2000.00'),
            "document": Decimal('800.00'),
            "prescription": Decimal('1200.00')
        }

        base_fee = base_fees.get(order_type, Decimal('1500.00'))
        per_km_fee = Decimal('150.00')

        distance_fee = distance_km * per_km_fee
        total_fee = base_fee + distance_fee

        return {
            "base_fee": base_fee,
            "distance_fee": distance_fee,
            "total_fee": total_fee
        }

    def get_by_tracking_code(
            self,
            db: Session,
            *,
            tracking_code: str
    ) -> Optional[Delivery]:
        """Get delivery by tracking code"""
        return db.query(Delivery).filter(
            Delivery.tracking_code == tracking_code
        ).first()

    def get_customer_deliveries(
            self,
            db: Session,
            *,
            customer_id: UUID,
            skip: int = 0,
            limit: int = 20,
            status: Optional[str] = None
    ) -> List[Delivery]:
        """Get customer deliveries"""
        query = db.query(Delivery).filter(
            Delivery.customer_id == customer_id
        )

        if status:
            query = query.filter(Delivery.status == status)

        return query.order_by(
            Delivery.created_at.desc()
        ).offset(skip).limit(limit).all()

    def get_rider_deliveries(
            self,
            db: Session,
            *,
            rider_id: UUID,
            skip: int = 0,
            limit: int = 50,
            status: Optional[str] = None
    ) -> List[Delivery]:
        """Get rider deliveries"""
        query = db.query(Delivery).filter(
            Delivery.rider_id == rider_id
        )

        if status:
            query = query.filter(Delivery.status == status)

        return query.order_by(
            Delivery.created_at.desc()
        ).offset(skip).limit(limit).all()

    def find_available_riders(
            self,
            db: Session,
            *,
            pickup_location: tuple,
            radius_km: float = 10.0,
            vehicle_type: Optional[str] = None
    ) -> List[Rider]:
        """Find available riders near pickup location"""
        lat, lng = pickup_location

        query = db.query(Rider).filter(
            and_(
                Rider.is_online == True,
                Rider.is_available == True,
                Rider.is_verified == True
            )
        )

        # Filter by vehicle type if specified
        if vehicle_type:
            query = query.filter(Rider.vehicle_type == vehicle_type)

        # Location-based filtering
        query = query.filter(
            func.ST_DWithin(
                Rider.current_location,
                func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326),
                radius_km * 1000
            )
        )

        # Order by rating and distance
        return query.order_by(
            Rider.average_rating.desc()
        ).limit(10).all()

    def assign_rider(
            self,
            db: Session,
            *,
            delivery_id: UUID,
            rider_id: UUID
    ) -> Delivery:
        """Assign rider to delivery"""
        delivery = self.get(db, id=delivery_id)
        if not delivery:
            raise NotFoundException("Delivery")

        rider = db.query(Rider).get(rider_id)
        if not rider:
            raise NotFoundException("Rider")

        if not rider.is_online or not rider.is_available:
            raise ValidationException("Rider is not available")

        # Assign rider
        delivery.rider_id = rider_id
        delivery.status = DeliveryStatusEnum.ASSIGNED
        delivery.assigned_at = datetime.utcnow()

        # Update rider availability
        rider.is_available = False

        # Create tracking update
        tracking = DeliveryTracking(
            delivery_id=delivery_id,
            status=DeliveryStatusEnum.ASSIGNED,
            notes=f"Assigned to rider {rider.user.email}",
            updated_by="system"
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
            location: Optional[tuple] = None
    ) -> Delivery:
        """Update delivery status"""
        delivery = self.get(db, id=delivery_id)
        if not delivery:
            raise NotFoundException("Delivery")

        delivery.status = new_status

        # Update timestamps
        if new_status == DeliveryStatusEnum.PICKED_UP:
            delivery.picked_up_at = datetime.utcnow()
        elif new_status == DeliveryStatusEnum.DELIVERED:
            delivery.delivered_at = datetime.utcnow()

            # Make rider available again
            if delivery.rider_id:
                rider = db.query(Rider).get(delivery.rider_id)
                if rider:
                    rider.is_available = True
                    rider.total_deliveries += 1

        # Create tracking update
        tracking_data = {
            "delivery_id": delivery_id,
            "status": new_status,
            "notes": notes,
            "updated_by": "rider"
        }

        if location:
            from geoalchemy2.elements import WKTElement
            lat, lng = location
            tracking_data["location"] = WKTElement(f"POINT({lng} {lat})", srid=4326)

        tracking = DeliveryTracking(**tracking_data)
        db.add(tracking)

        db.commit()
        db.refresh(delivery)

        return delivery


class CRUDDeliveryZone(CRUDBase[DeliveryZone, dict, dict]):
    """CRUD for DeliveryZone"""

    def get_zone_for_location(
            self,
            db: Session,
            *,
            latitude: float,
            longitude: float
    ) -> Optional[DeliveryZone]:
        """Find delivery zone for a location"""
        zones = db.query(DeliveryZone).filter(
            DeliveryZone.is_active == True
        ).all()

        for zone in zones:
            # Check if location is within zone radius
            # TODO: Implement proper geographic check
            pass

        return None


class CRUDRiderEarnings(CRUDBase[RiderEarnings, dict, dict]):
    """CRUD for RiderEarnings"""

    def create_earnings(
            self,
            db: Session,
            *,
            rider_id: UUID,
            delivery_id: UUID,
            delivery_fee: Decimal
    ) -> RiderEarnings:
        """Create earnings record for completed delivery"""
        # Calculate earnings (80% goes to rider, 20% platform commission)
        platform_commission_rate = Decimal('0.20')

        base_earning = delivery_fee
        platform_commission = base_earning * platform_commission_rate
        net_earning = base_earning - platform_commission

        earnings = RiderEarnings(
            rider_id=rider_id,
            delivery_id=delivery_id,
            base_earning=base_earning,
            total_earning=base_earning,
            platform_commission=platform_commission,
            net_earning=net_earning
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
            limit: int = 50
    ) -> List[RiderEarnings]:
        """Get rider earnings"""
        query = db.query(RiderEarnings).filter(
            RiderEarnings.rider_id == rider_id
        )

        if date_from:
            query = query.filter(RiderEarnings.created_at >= date_from)

        if date_to:
            query = query.filter(RiderEarnings.created_at <= date_to)

        return query.order_by(
            RiderEarnings.created_at.desc()
        ).offset(skip).limit(limit).all()


# Singleton instances
delivery_crud = CRUDDelivery(Delivery)
delivery_zone_crud = CRUDDeliveryZone(DeliveryZone)
rider_earnings_crud = CRUDRiderEarnings(RiderEarnings)