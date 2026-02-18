from sqlalchemy.orm import Session
from sqlalchemy import and_, func, text
from typing import Optional, List, Tuple
from uuid import UUID
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point
from decimal import Decimal

from app.crud.base import CRUDBase
from app.models.rider import Rider
from app.schemas.rider import RiderCreate, RiderUpdate


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
            obj_in: RiderCreate
    ) -> Rider:
        """Create a new rider profile."""
        rider = Rider(
            user_id=user_id,
            **obj_in.model_dump()
        )
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
            longitude: float
    ) -> Rider:
        """Update rider's current location."""
        rider = self.get(db, id=rider_id)
        if rider:
            # Create PostGIS point
            point = Point(longitude, latitude)
            rider.current_location = from_shape(point, srid=4326)
            db.commit()
            db.refresh(rider)
        return rider

    def set_online_status(self, db: Session, *, rider_id: UUID, is_online: bool) -> Rider:
        """Set rider online/offline status."""
        rider = self.get(db, id=rider_id)
        if rider:
            rider.is_online = is_online
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
            vehicle_type: Optional[str] = None
    ) -> List[Rider]:
        """
        Get available riders within radius using PostGIS.
        """
        # Create search point
        search_point = from_shape(Point(longitude, latitude), srid=4326)

        # Build query
        query = db.query(Rider).filter(
            and_(
                Rider.is_online == True,
                Rider.is_verified == True,
                Rider.is_active == True,
                Rider.current_location.isnot(None)
            )
        )

        # Filter by vehicle type if specified
        if vehicle_type:
            query = query.filter(Rider.vehicle_type == vehicle_type)

        # Use ST_DWithin for radius search (radius in meters)
        radius_meters = radius_km * 1000
        query = query.filter(
            func.ST_DWithin(
                Rider.current_location,
                search_point,
                radius_meters
            )
        )

        # Order by distance
        query = query.order_by(
            func.ST_Distance(Rider.current_location, search_point)
        )

        return query.all()

    def get_top_rated_riders(
            self,
            db: Session,
            *,
            limit: int = 10,
            min_deliveries: int = 5
    ) -> List[Rider]:
        """Get top-rated riders with minimum delivery count."""
        return db.query(Rider).filter(
            and_(
                Rider.is_active == True,
                Rider.is_verified == True,
                Rider.total_deliveries >= min_deliveries
            )
        ).order_by(
            Rider.average_rating.desc(),
            Rider.total_deliveries.desc()
        ).limit(limit).all()

    def update_stats(
            self,
            db: Session,
            *,
            rider_id: UUID,
            new_rating: Optional[float] = None,
            increment_deliveries: bool = False,
            increment_completed: bool = False
    ) -> Rider:
        """Update rider statistics."""
        rider = self.get(db, id=rider_id)
        if not rider:
            return None

        if increment_deliveries:
            rider.total_deliveries += 1

        if new_rating is not None:
            # Calculate new average rating
            total = (float(rider.average_rating) * rider.total_deliveries) + new_rating
            rider.total_deliveries += 1
            rider.average_rating = Decimal(str(total / rider.total_deliveries))

        # Update completion rate
        if increment_deliveries and increment_completed:
            completed = int(rider.total_deliveries * float(rider.completion_rate) / 100)
            completed += 1
            rider.completion_rate = Decimal(str((completed / rider.total_deliveries) * 100))

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
            limit: int = 20
    ) -> Tuple[List[Rider], int]:
        """Search riders with filters."""
        db_query = db.query(Rider).filter(Rider.is_active == True)

        if query:
            search_filter = f"%{query}%"
            db_query = db_query.filter(
                (Rider.first_name.ilike(search_filter)) |
                (Rider.last_name.ilike(search_filter)) |
                (Rider.vehicle_plate_number.ilike(search_filter))
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