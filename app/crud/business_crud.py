"""
app/crud/business_crud.py

Business CRUD with PostGIS radius-based discovery.

Per Blueprint v2.0 Section 3: "Location is radius-based (default 5 km).
No local-government-area filtering."

ALL LGA/city/state filtering has been REMOVED. Discovery is purely
coordinate + radius based using PostGIS ST_DWithin.

FIX: Custom async update() override added.
  Business.latitude and Business.longitude are read-only @property getters
  that extract coordinates from the PostGIS Geography 'location' column.
  The base AsyncCRUDBase.update() does setattr(obj, 'latitude', value) which
  raises:
      AttributeError: property 'latitude' of 'Business' object has no setter
  The override intercepts latitude/longitude from the update payload and
  converts them into a PostGIS ST_SetSRID(ST_MakePoint(lng, lat), 4326)
  expression written to the 'location' column instead.
"""

from typing import Optional, Tuple, List, Union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import or_, case, func, and_, select, update as sa_update
from uuid import UUID
from geoalchemy2.functions import ST_DWithin, ST_Distance

from app.crud.base_crud import AsyncCRUDBase as CRUDBase
from app.models.business_model import Business, BusinessHours
from app.models.user_model import User
from app.core.constants import DEFAULT_RADIUS_METERS


# Subscription tier ordering: Enterprise > Pro > Starter > Free
_TIER_ORDER = case(
    (Business.subscription_tier == "enterprise", 0),
    (Business.subscription_tier == "pro", 1),
    (Business.subscription_tier == "starter", 2),
    else_=3,
)

# Relationships eagerly loaded for every list/search query
_EAGER = [
    selectinload(Business.user),
    selectinload(Business.business_hours),
]


class CRUDBusiness(CRUDBase[Business, dict, dict]):
    """CRUD operations for Business with PostGIS spatial queries."""

    # ── Update (custom — handles lat/lng → PostGIS location) ─────────────────

    async def update(
        self,
        db: AsyncSession,
        *,
        db_obj: Business,
        obj_in: Union[dict, object],
    ) -> Business:
        """
        Update a Business record.

        FIX: latitude and longitude are @property getters on the model (they
        read from the PostGIS Geography 'location' column). The base update()
        calls setattr(obj, 'latitude', value) which raises AttributeError
        because there is no setter.

        This override:
          1. Pops latitude/longitude from the update dict.
          2. Builds a PostGIS point and writes it to obj.location.
          3. Sets all other fields normally.
        """
        if hasattr(obj_in, "model_dump"):
            update_data = obj_in.model_dump(exclude_unset=True)
        elif hasattr(obj_in, "dict"):
            update_data = obj_in.dict(exclude_unset=True)
        else:
            update_data = dict(obj_in)

        # Extract lat/lng before iterating — they cannot be set via setattr
        new_lat = update_data.pop("latitude", None)
        new_lng = update_data.pop("longitude", None)

        # Build PostGIS point if either coordinate was provided
        if new_lat is not None or new_lng is not None:
            # Fall back to current coordinates for whichever was not supplied
            current_lat = db_obj.latitude if db_obj.location else 0.0
            current_lng = db_obj.longitude if db_obj.location else 0.0
            lat = new_lat if new_lat is not None else current_lat
            lng = new_lng if new_lng is not None else current_lng
            # ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
            # Note: ST_MakePoint takes (x=longitude, y=latitude)
            db_obj.location = func.ST_SetSRID(
                func.ST_MakePoint(float(lng), float(lat)), 4326
            )

        # Apply remaining fields normally
        for field, value in update_data.items():
            setattr(db_obj, field, value)

        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    # ── Lookups ──────────────────────────────────────────────────────────────

    async def get_by_user_id(
        self, db: AsyncSession, *, user_id: UUID
    ) -> Optional[Business]:
        """Get business by user ID."""
        result = await db.execute(
            select(Business)
            .options(*_EAGER)
            .where(Business.user_id == user_id)
        )
        return result.scalars().first()

    async def get_nearby_businesses(
        self,
        db: AsyncSession,
        *,
        latitude: float,
        longitude: float,
        radius_meters: float = DEFAULT_RADIUS_METERS,
        category: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Tuple[List[Business], int]:
        """
        Get businesses within radius of coordinates.

        This is the PRIMARY discovery method per Blueprint v2.0.
        No LGA filtering — purely GPS coordinate + radius.
        """
        user_point = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)

        base_filters = [
            Business.is_active == True,
            ST_DWithin(Business.location, user_point, radius_meters),
        ]

        if category:
            base_filters.append(Business.category == category)

        count_result = await db.execute(
            select(func.count(Business.id)).where(*base_filters)
        )
        total: int = count_result.scalar() or 0

        distance_col = ST_Distance(Business.location, user_point).label("distance_meters")
        data_result = await db.execute(
            select(Business, distance_col)
            .options(*_EAGER)
            .where(*base_filters)
            .order_by(
                _TIER_ORDER,
                Business.is_featured.desc(),
                Business.average_rating.desc(),
                distance_col,
            )
            .offset(skip)
            .limit(limit)
        )

        business_list = []
        for business, distance_meters in data_result.all():
            business.distance_km = distance_meters / 1000.0
            business_list.append(business)

        return business_list, total

    async def search_businesses(
        self,
        db: AsyncSession,
        *,
        latitude: float,
        longitude: float,
        radius_meters: float = DEFAULT_RADIUS_METERS,
        category: Optional[str] = None,
        search_query: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Tuple[List[Business], int]:
        """
        Search businesses within radius with text query.

        Per Blueprint: "Unified search — radius-filtered — subscription-ranked"
        """
        user_point = func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326)

        base_filters = [
            Business.is_active == True,
            ST_DWithin(Business.location, user_point, radius_meters),
        ]

        if category:
            base_filters.append(Business.category == category)

        if search_query:
            pattern = f"%{search_query}%"
            base_filters.append(
                or_(
                    Business.business_name.ilike(pattern),
                    Business.description.ilike(pattern),
                    Business.subcategory.ilike(pattern),
                )
            )

        count_result = await db.execute(
            select(func.count(Business.id)).where(*base_filters)
        )
        total: int = count_result.scalar() or 0

        distance_col = ST_Distance(Business.location, user_point).label("distance_meters")
        data_result = await db.execute(
            select(Business, distance_col)
            .options(*_EAGER)
            .where(*base_filters)
            .order_by(
                _TIER_ORDER,
                Business.is_featured.desc(),
                Business.average_rating.desc(),
                distance_col,
            )
            .offset(skip)
            .limit(limit)
        )

        business_list = []
        for business, distance_meters in data_result.all():
            business.distance_km = distance_meters / 1000.0
            business_list.append(business)

        return business_list, total

    async def get_by_category_radius(
        self,
        db: AsyncSession,
        *,
        category: str,
        latitude: float,
        longitude: float,
        radius_meters: float = DEFAULT_RADIUS_METERS,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Business]:
        """Get businesses by category within radius."""
        businesses, _ = await self.get_nearby_businesses(
            db,
            latitude=latitude,
            longitude=longitude,
            radius_meters=radius_meters,
            category=category,
            skip=skip,
            limit=limit,
        )
        return businesses


business_crud = CRUDBusiness(Business)