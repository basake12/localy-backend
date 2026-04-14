"""
app/services/location_service.py

Location and geocoding services with PostGIS spatial queries.

Per Blueprint v2.0 Section 3: "Location is radius-based (default 5 km). 
No local-government-area filtering."

All discovery queries use PostGIS ST_DWithin for strict radius filtering.
Distance display uses ST_Distance for accurate "X.X km away" labels.
"""
import logging
from typing import Optional, Tuple, Dict, Any, List
from uuid import UUID

import httpx
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from geoalchemy2 import Geography
from geoalchemy2.shape import to_shape, from_shape
from shapely.geometry import Point

from app.config import settings
from app.core.utils import calculate_distance
from app.core.constants import (
    DEFAULT_RADIUS_METERS,
    MIN_RADIUS_METERS,
    MAX_RADIUS_METERS
)

logger = logging.getLogger(__name__)

_GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_DISTANCE_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"

# Delivery pricing defaults
_DEFAULT_BASE_FEE_NGN: float = 500.0
_DEFAULT_PER_KM_NGN: float = 50.0


class LocationService:
    """Location service with PostGIS spatial query support."""

    def __init__(self) -> None:
        self._api_key: str = getattr(settings, "GOOGLE_MAPS_API_KEY", "")
        if not self._api_key:
            logger.warning(
                "GOOGLE_MAPS_API_KEY is not set — geocoding will use "
                "Haversine fallback only."
            )

    # ================================================================
    # GEOCODING
    # ================================================================

    async def geocode_address(self, address: str) -> Optional[Tuple[float, float]]:
        """
        Convert a text address to (latitude, longitude).
        Returns None if the API key is absent or the lookup fails.
        """
        if not self._api_key:
            return None

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    _GEOCODING_URL,
                    params={"address": address, "key": self._api_key},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.error(f"Geocoding request failed: {exc}")
            return None

        if data.get("status") == "OK" and data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            return loc["lat"], loc["lng"]

        logger.warning(
            f"Geocoding returned status={data.get('status')} "
            f"for address='{address}'"
        )
        return None

    async def reverse_geocode(
        self, latitude: float, longitude: float
    ) -> Optional[str]:
        """
        Convert coordinates to a formatted address string.
        Returns None on failure.
        """
        if not self._api_key:
            return None

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    _GEOCODING_URL,
                    params={
                        "latlng": f"{latitude},{longitude}",
                        "key": self._api_key,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.error(f"Reverse geocoding request failed: {exc}")
            return None

        if data.get("status") == "OK" and data.get("results"):
            return data["results"][0]["formatted_address"]

        return None

    # ================================================================
    # POSTGIS SPATIAL QUERIES
    # ================================================================

    def create_point(self, latitude: float, longitude: float) -> Geography:
        """
        Create a PostGIS Geography point from lat/lng.
        
        Returns a Geography object suitable for storing in the 
        businesses.location column.
        """
        point = Point(longitude, latitude)  # Note: PostGIS is (lng, lat)
        return from_shape(point, srid=4326)

    def calculate_distance_meters(
        self,
        db: Session,
        point1: Geography,
        point2: Geography
    ) -> float:
        """
        Calculate distance in meters between two Geography points using ST_Distance.
        
        Returns precise spheroid distance accounting for Earth's curvature.
        """
        query = db.query(
            func.ST_Distance(point1, point2).label("distance")
        )
        result = query.first()
        return result.distance if result else 0.0

    def calculate_distance_km(
        self,
        db: Session,
        point1: Geography,
        point2: Geography
    ) -> float:
        """Calculate distance in kilometers (convenience wrapper)."""
        return self.calculate_distance_meters(db, point1, point2) / 1000.0

    def is_within_radius(
        self,
        db: Session,
        center: Geography,
        target: Geography,
        radius_meters: float
    ) -> bool:
        """
        Check if target point is within radius_meters of center point.
        
        Uses ST_DWithin which is optimized with GIST spatial index.
        """
        query = db.query(
            func.ST_DWithin(center, target, radius_meters).label("within")
        )
        result = query.first()
        return result.within if result else False

    # ================================================================
    # DISTANCE MATRIX (Google Maps API)
    # ================================================================

    async def get_distance_matrix(
        self,
        origins: list[Tuple[float, float]],
        destinations: list[Tuple[float, float]],
    ) -> Optional[Dict[str, Any]]:
        """
        Return driving distance and duration between origin and destination.
        
        Falls back to Haversine straight-line distance when API key is absent.
        """
        if not origins or not destinations:
            return None

        # Haversine fallback
        if not self._api_key:
            dist = calculate_distance(
                origins[0][0], origins[0][1],
                destinations[0][0], destinations[0][1],
            )
            eta_minutes = int(dist * 3)  # rough: ~20 km/h urban average
            return {
                "distance_km": dist,
                "duration_minutes": eta_minutes,
                "distance_text": f"{dist:.2f} km",
                "duration_text": f"{eta_minutes} mins",
            }

        # Google Distance Matrix API
        origins_str = "|".join(f"{lat},{lng}" for lat, lng in origins)
        destinations_str = "|".join(f"{lat},{lng}" for lat, lng in destinations)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    _DISTANCE_URL,
                    params={
                        "origins": origins_str,
                        "destinations": destinations_str,
                        "key": self._api_key,
                        "mode": "driving",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.error(f"Distance matrix request failed: {exc}")
            return None

        if data.get("status") == "OK" and data.get("rows"):
            element = data["rows"][0]["elements"][0]
            if element.get("status") == "OK":
                return {
                    "distance_km": element["distance"]["value"] / 1000,
                    "duration_minutes": element["duration"]["value"] // 60,
                    "distance_text": element["distance"]["text"],
                    "duration_text": element["duration"]["text"],
                }

        logger.warning(f"Distance matrix returned status={data.get('status')}")
        return None

    # ================================================================
    # DELIVERY FEE CALCULATION
    # ================================================================

    def calculate_delivery_fee(
        self,
        distance_km: float,
        base_fee: float = _DEFAULT_BASE_FEE_NGN,
        per_km_rate: float = _DEFAULT_PER_KM_NGN,
    ) -> float:
        """
        Calculate delivery fee (₦) from distance.
        
        base_fee    — flat charge regardless of distance (default ₦500)
        per_km_rate — incremental charge per km (default ₦50/km)
        """
        return base_fee + (distance_km * per_km_rate)


# Singleton
location_service = LocationService()