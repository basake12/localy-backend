"""
app/services/location_service.py

Location, geocoding, and PostGIS spatial query helpers.

Blueprint §4.1: "Location is radius-based exclusively. No LGA filtering."
Blueprint §4.3: ST_DWithin for radius filter, ST_Distance for display.
Blueprint §16.4: datetime.now(timezone.utc) — no datetime.utcnow() usage here.

No blueprint violations found in original.
Presented for completeness alongside corrected search section files.

IMPORTANT NOTE on geo query pattern:
  This service provides utility methods. All production DISCOVERY queries
  must use the PostGIS expressions in search_service.py (_dwithin_filter,
  _distance_m_expr) which run inside SQL — NOT the Python-side Haversine
  fallbacks in utils.py or the DB-round-trip methods in this file.

  Blueprint §4.3 canonical SQL:
    WHERE ST_DWithin(b.location::geography,
                     ST_MakePoint(:lng, :lat)::geography, :radius_m)
    ORDER BY ST_Distance(...) ASC
"""

import logging
from typing import Optional, Tuple, Dict, Any
from uuid import UUID

import httpx
from sqlalchemy.orm import Session
from sqlalchemy import func
from geoalchemy2 import Geography
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from app.config import settings
from app.core.utils import calculate_distance
from app.core.constants import (
    DEFAULT_RADIUS_METERS,
    MIN_RADIUS_METERS,
    MAX_RADIUS_METERS,
)

logger = logging.getLogger(__name__)

_GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_DISTANCE_URL  = "https://maps.googleapis.com/maps/api/distancematrix/json"

_DEFAULT_BASE_FEE_NGN: float = 500.0
_DEFAULT_PER_KM_NGN: float   = 50.0


class LocationService:
    """
    Location service with PostGIS spatial query helpers and Google Geocoding.
    Blueprint §4.1: radius-based discovery only. No LGA methods here or anywhere.
    """

    def __init__(self) -> None:
        self._api_key: str = getattr(settings, "GOOGLE_GEOCODING_API_KEY", "")
        if not self._api_key:
            logger.warning(
                "GOOGLE_GEOCODING_API_KEY not set — geocoding will fail. "
                "Required for Blueprint §3 Step 5a: geocode registered business address."
            )

    # ── Geocoding ─────────────────────────────────────────────────────────────

    async def geocode_address(self, address: str) -> Optional[Tuple[float, float]]:
        """
        Convert a text address to (latitude, longitude).
        Blueprint §3 Step 5a: "Address is geocoded immediately to PostGIS point
        via Google Geocoding API."
        Returns None if API key is absent or lookup fails.
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
            logger.error("Geocoding request failed: %s", exc)
            return None

        if data.get("status") == "OK" and data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            return loc["lat"], loc["lng"]

        logger.warning(
            "Geocoding status=%s for address='%s'",
            data.get("status"),
            address,
        )
        return None

    async def reverse_geocode(
        self,
        latitude: float,
        longitude: float,
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
                        "key":    self._api_key,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.error("Reverse geocoding request failed: %s", exc)
            return None

        if data.get("status") == "OK" and data.get("results"):
            return data["results"][0]["formatted_address"]

        return None

    # ── PostGIS helpers ────────────────────────────────────────────────────────

    def create_point(self, latitude: float, longitude: float) -> Geography:
        """
        Create a PostGIS Geography point from lat/lng for storing in DB.
        Blueprint §4.1: "Stored as PostGIS geography(POINT, 4326)."
        NOTE: Shapely Point takes (longitude, latitude) — PostGIS convention.
        """
        point = Point(longitude, latitude)
        return from_shape(point, srid=4326)

    def calculate_distance_meters(
        self,
        db: Session,
        point1: Geography,
        point2: Geography,
    ) -> float:
        """
        Calculate distance in metres between two Geography points via ST_Distance.
        Blueprint §4.2: "Distance calculation uses ST_Distance (PostGIS) in metres."
        """
        result = db.query(
            func.ST_Distance(point1, point2).label("distance")
        ).first()
        return result.distance if result else 0.0

    def calculate_distance_km(
        self,
        db: Session,
        point1: Geography,
        point2: Geography,
    ) -> float:
        """Calculate distance in kilometres (metres / 1000)."""
        return self.calculate_distance_meters(db, point1, point2) / 1000.0

    # ── Distance Matrix (Google Maps) ─────────────────────────────────────────

    async def get_distance_matrix(
        self,
        origins: list[Tuple[float, float]],
        destinations: list[Tuple[float, float]],
    ) -> Optional[Dict[str, Any]]:
        """
        Return driving distance and duration between origin and destination.
        Falls back to Haversine straight-line when API key is absent.
        Used for delivery fee estimation, NOT for discovery radius filtering.
        """
        if not origins or not destinations:
            return None

        # Haversine fallback when no API key
        if not self._api_key:
            dist = calculate_distance(
                origins[0][0], origins[0][1],
                destinations[0][0], destinations[0][1],
            )
            eta_minutes = int(dist * 3)  # rough: ~20 km/h urban average
            return {
                "distance_km":   dist,
                "duration_minutes": eta_minutes,
                "distance_text": f"{dist:.2f} km",
                "duration_text": f"{eta_minutes} mins",
            }

        origins_str      = "|".join(f"{lat},{lng}" for lat, lng in origins)
        destinations_str = "|".join(f"{lat},{lng}" for lat, lng in destinations)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    _DISTANCE_URL,
                    params={
                        "origins":      origins_str,
                        "destinations": destinations_str,
                        "key":          self._api_key,
                        "mode":         "driving",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.error("Distance matrix request failed: %s", exc)
            return None

        if data.get("status") == "OK" and data.get("rows"):
            element = data["rows"][0]["elements"][0]
            if element.get("status") == "OK":
                return {
                    "distance_km":      element["distance"]["value"] / 1000,
                    "duration_minutes": element["duration"]["value"] // 60,
                    "distance_text":    element["distance"]["text"],
                    "duration_text":    element["duration"]["text"],
                }

        logger.warning("Distance matrix status=%s", data.get("status"))
        return None

    # ── Delivery Fee Calculation ───────────────────────────────────────────────

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