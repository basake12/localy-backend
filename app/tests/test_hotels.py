# tests/test_hotels.py
import pytest
from fastapi.testclient import TestClient
from datetime import date, timedelta

def test_hotel_search(client: TestClient):
    """Test hotel search"""
    response = client.post(
        "/api/v1/hotels/search",
        json={
            "location": {"latitude": 9.0765, "longitude": 7.3986},
            "radius_km": 10,
            "check_in_date": str(date.today() + timedelta(days=7)),
            "check_out_date": str(date.today() + timedelta(days=10))
        }
    )
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_create_booking(client: TestClient, customer_token: str, room_type_id: str):
    """Test booking creation"""
    response = client.post(
        "/api/v1/hotels/bookings",
        headers={"Authorization": f"Bearer {customer_token}"},
        json={
            "room_type_id": room_type_id,
            "check_in_date": str(date.today() + timedelta(days=7)),
            "check_out_date": str(date.today() + timedelta(days=10)),
            "number_of_rooms": 1,
            "number_of_guests": 2,
            "add_ons": [],
            "special_requests": "High floor please"
        }
    )
    assert response.status_code == 201
    assert response.json()["success"] is True