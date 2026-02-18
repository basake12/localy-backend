"""
Location and geocoding services.
"""
import requests
from typing import Optional, Tuple, Dict, Any
from app.config import settings
from app.core.utils import calculate_distance


class LocationService:
    """Location services using Google Maps API."""

    def __init__(self):
        self.api_key = getattr(settings, 'GOOGLE_MAPS_API_KEY', '')
        self.geocoding_url = "https://maps.googleapis.com/maps/api/geocode/json"
        self.distance_url = "https://maps.googleapis.com/maps/api/distancematrix/json"

    def geocode_address(self, address: str) -> Optional[Tuple[float, float]]:
        """
        Convert address to coordinates.

        Returns:
            (latitude, longitude) or None
        """
        if not self.api_key:
            return None

        params = {
            "address": address,
            "key": self.api_key
        }

        response = requests.get(self.geocoding_url, params=params)
        data = response.json()

        if data.get("status") == "OK" and data.get("results"):
            location = data["results"][0]["geometry"]["location"]
            return location["lat"], location["lng"]

        return None

    def reverse_geocode(self, latitude: float, longitude: float) -> Optional[str]:
        """
        Convert coordinates to address.

        Returns:
            Formatted address string or None
        """
        if not self.api_key:
            return None

        params = {
            "latlng": f"{latitude},{longitude}",
            "key": self.api_key
        }

        response = requests.get(self.geocoding_url, params=params)
        data = response.json()

        if data.get("status") == "OK" and data.get("results"):
            return data["results"][0]["formatted_address"]

        return None

    def get_distance_matrix(
            self,
            origins: list[Tuple[float, float]],
            destinations: list[Tuple[float, float]]
    ) -> Optional[Dict[str, Any]]:
        """
        Get distance and duration between multiple points.

        Returns:
            {
                "distance_km": float,
                "duration_minutes": int,
                "distance_text": str,
                "duration_text": str
            }
        """
        if not self.api_key:
            # Fallback to simple calculation
            if origins and destinations:
                dist = calculate_distance(
                    origins[0][0], origins[0][1],
                    destinations[0][0], destinations[0][1]
                )
                return {
                    "distance_km": dist,
                    "duration_minutes": int(dist * 3),  # Rough estimate
                    "distance_text": f"{dist:.2f} km",
                    "duration_text": f"{int(dist * 3)} mins"
                }
            return None

        # Format coordinates for API
        origins_str = "|".join([f"{lat},{lng}" for lat, lng in origins])
        destinations_str = "|".join([f"{lat},{lng}" for lat, lng in destinations])

        params = {
            "origins": origins_str,
            "destinations": destinations_str,
            "key": self.api_key,
            "mode": "driving"
        }

        response = requests.get(self.distance_url, params=params)
        data = response.json()

        if data.get("status") == "OK" and data.get("rows"):
            element = data["rows"][0]["elements"][0]
            if element.get("status") == "OK":
                return {
                    "distance_km": element["distance"]["value"] / 1000,
                    "duration_minutes": element["duration"]["value"] // 60,
                    "distance_text": element["distance"]["text"],
                    "duration_text": element["duration"]["text"]
                }

        return None

    def calculate_delivery_fee(
            self,
            distance_km: float,
            base_fee: float = 500,
            per_km_rate: float = 50
    ) -> float:
        """Calculate delivery fee based on distance."""
        return base_fee + (distance_km * per_km_rate)


# Singleton instance
location_service = LocationService()
