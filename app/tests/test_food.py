import pytest
from fastapi.testclient import TestClient
from datetime import date, time, timedelta


def test_search_restaurants(client: TestClient):
    """Test restaurant search"""
    response = client.post(
        "/api/v1/food/restaurants/search",
        json={
            "cuisine_type": "nigerian",
            "location": {"latitude": 9.0765, "longitude": 7.3986},
            "radius_km": 10
        }
    )
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_create_restaurant(client: TestClient, business_token: str):
    """Test restaurant creation"""
    response = client.post(
        "/api/v1/food/restaurants",
        headers={"Authorization": f"Bearer {business_token}"},
        json={
            "cuisine_types": ["nigerian"],
            "offers_delivery": True,
            "delivery_fee": 1000.00
        }
    )
    assert response.status_code == 201
    assert response.json()["success"] is True


def test_create_menu_item(client: TestClient, business_token: str, category_id: str):
    """Test menu item creation"""
    response = client.post(
        "/api/v1/food/menu/items",
        headers={"Authorization": f"Bearer {business_token}"},
        json={
            "category_id": category_id,
            "name": "Jollof Rice",
            "price": 3500.00,
            "preparation_time_minutes": 25
        }
    )
    assert response.status_code == 201
    assert response.json()["success"] is True


def test_create_reservation(client: TestClient, customer_token: str, restaurant_id: str):
    """Test table reservation"""
    reservation_date = (date.today() + timedelta(days=7)).isoformat()

    response = client.post(
        "/api/v1/food/reservations",
        headers={"Authorization": f"Bearer {customer_token}"},
        json={
            "restaurant_id": restaurant_id,
            "reservation_date": reservation_date,
            "reservation_time": "19:00",
            "number_of_guests": 4,
            "customer_name": "John Doe",
            "customer_phone": "+2348012345678"
        }
    )
    assert response.status_code == 201
    assert response.json()["success"] is True


def test_create_food_order(client: TestClient, customer_token: str, menu_item_id: str):
    """Test food order creation"""
    response = client.post(
        "/api/v1/food/orders",
        headers={"Authorization": f"Bearer {customer_token}"},
        json={
            "restaurant_id": "uuid",
            "order_type": "delivery",
            "items": [
                {
                    "menu_item_id": menu_item_id,
                    "quantity": 2,
                    "selected_modifiers": []
                }
            ],
            "delivery_address": "123 Main St, Abuja",
            "customer_name": "John Doe",
            "customer_phone": "+2348012345678",
            "payment_method": "wallet"
        }
    )
    assert response.status_code == 201
    assert response.json()["success"] is True