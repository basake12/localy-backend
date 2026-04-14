from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from typing import Optional, List, Tuple
from uuid import UUID
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from decimal import Decimal

from app.crud.base_crud import CRUDBase
from app.models.rider_model import Rider
from app.schemas.rider_schema import RiderCreate, RiderUpdate


class CRUDRider(CRUDBase[Rider, RiderCreate, RiderUpdate]):
    """CRUD operations for Rider model."""

    def get_by_user_id(self, db: Session, *, user_id: UUID) -> Optional[Rider]:
        """Get rider by user ID."""
        return db.query(Rider).filter(Rider.user_id == user_id).first()

    def create_rider(
        self,
        db: Session,
        *,
        user_id: UUID,
        obj_in: RiderCreate,
    ) -> Rider:
        """Create a new rider profile."""
        rider = Rider(user_id=user_id, **obj_in.model_dump())
        db.add(rider)
        db.commit()
        db.refresh(rider)
        return rider

    def update_location(
        self,
        db: Session,
        *,
        rider_id: UUID,
        latitude: float,
        longitude: float,
    ) -> Optional[Rider]:
        """Update rider's current location using PostGIS."""
        rider = self.get(db, id=rider_id)
        if rider:
            rider.current_location = from_shape(
                Point(longitude, latitude), srid=4326
            )
            db.commit()
            db.refresh(rider)
        return rider

    def set_online_status(
        self, db: Session, *, rider_id: UUID, is_online: bool
    ) -> Optional[Rider]:
        """Set rider online/offline status."""
        rider = self.get(db, id=rider_id)
        if rider:
            rider.is_online = is_online
            db.commit()
            db.refresh(rider)
        return rider

    def update_fcm_token(
        self, db: Session, *, rider_id: UUID, fcm_token: str
    ) -> Optional[Rider]:
        """Update FCM push token for job-alert notifications."""
        rider = self.get(db, id=rider_id)
        if rider:
            rider.fcm_token = fcm_token
            db.commit()
            db.refresh(rider)
        return rider

    def get_available_riders(
        self,
        db: Session,
        *,
        latitude: float,
        longitude: float,
        radius_km: float = 10.0,
        vehicle_type: Optional[str] = None,
    ) -> List[Rider]:
        """Get available, verified riders within a radius using PostGIS ST_DWithin."""
        search_point = from_shape(Point(longitude, latitude), srid=4326)
        radius_meters = radius_km * 1000

        query = db.query(Rider).filter(
            and_(
                Rider.is_online == True,
                Rider.is_verified == True,
                Rider.is_active == True,
                Rider.current_location.isnot(None),
            )
        )

        if vehicle_type:
            query = query.filter(Rider.vehicle_type == vehicle_type)

        query = query.filter(
            func.ST_DWithin(Rider.current_location, search_point, radius_meters)
        ).order_by(
            func.ST_Distance(Rider.current_location, search_point)
        )

        return query.all()

    def get_top_rated_riders(
        self,
        db: Session,
        *,
        limit: int = 10,
        min_deliveries: int = 5,
    ) -> List[Rider]:
        """Get top-rated verified riders with a minimum delivery count."""
        return (
            db.query(Rider)
            .filter(
                and_(
                    Rider.is_active == True,
                    Rider.is_verified == True,
                    Rider.total_deliveries >= min_deliveries,
                )
            )
            .order_by(Rider.average_rating.desc(), Rider.total_deliveries.desc())
            .limit(limit)
            .all()
        )

    def update_stats(
        self,
        db: Session,
        *,
        rider_id: UUID,
        new_rating: Optional[float] = None,
        increment_deliveries: bool = False,
        increment_completed: bool = False,
    ) -> Optional[Rider]:
        """
        Update rider statistics atomically.

        Rules:
        - `increment_deliveries` adds 1 to total_deliveries (call once per job).
        - `increment_completed` adds 1 to completed_deliveries (call when job finishes).
        - `new_rating` recalculates the running average using the CURRENT total_deliveries
          AFTER the increment, so pass both flags together on delivery completion.
        """
        rider = self.get(db, id=rider_id)
        if not rider:
            return None

        if increment_deliveries:
            rider.total_deliveries += 1

        if increment_completed:
            rider.completed_deliveries += 1

        # Recalculate completion rate from tracked columns (not a ratio estimate)
        if increment_deliveries or increment_completed:
            if rider.total_deliveries > 0:
                rider.completion_rate = Decimal(
                    str((rider.completed_deliveries / rider.total_deliveries) * 100)
                )

        # Recalculate running average rating using the (already updated) total
        if new_rating is not None and rider.total_deliveries > 0:
            previous_total = (
                float(rider.average_rating) * (rider.total_deliveries - 1)
            )
            rider.average_rating = Decimal(
                str((previous_total + new_rating) / rider.total_deliveries)
            )

        db.commit()
        db.refresh(rider)
        return rider

    def search_riders(
        self,
        db: Session,
        *,
        query: Optional[str] = None,
        vehicle_type: Optional[str] = None,
        is_verified: Optional[bool] = None,
        is_online: Optional[bool] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[Rider], int]:
        """Search riders with optional filters. Returns (items, total)."""
        db_query = db.query(Rider).filter(Rider.is_active == True)

        if query:
            pattern = f"%{query}%"
            db_query = db_query.filter(
                Rider.first_name.ilike(pattern)
                | Rider.last_name.ilike(pattern)
                | Rider.vehicle_plate_number.ilike(pattern)
            )

        if vehicle_type:
            db_query = db_query.filter(Rider.vehicle_type == vehicle_type)

        if is_verified is not None:
            db_query = db_query.filter(Rider.is_verified == is_verified)

        if is_online is not None:
            db_query = db_query.filter(Rider.is_online == is_online)

        total = db_query.count()
        riders = db_query.offset(skip).limit(limit).all()
        return riders, total


# Singleton instance
rider_crud = CRUDRider(Rider)