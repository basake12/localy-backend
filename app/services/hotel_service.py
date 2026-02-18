from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import date
from decimal import Decimal

from app.crud.hotels import hotel_crud, room_type_crud, hotel_booking_crud
from app.crud.business import business_crud
from app.core.exceptions import NotFoundException, PermissionDeniedException
from app.models.user import User


class HotelService:
    """Business logic for hotel operations"""

    @staticmethod
    def search_hotels(
            db: Session,
            *,
            location: Optional[tuple] = None,
            radius_km: float = 10.0,
            check_in: Optional[date] = None,
            check_out: Optional[date] = None,
            guests: int = 1,
            rooms: int = 1,
            star_rating: Optional[int] = None,
            facilities: Optional[List[str]] = None,
            min_price: Optional[Decimal] = None,
            max_price: Optional[Decimal] = None,
            skip: int = 0,
            limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Search hotels with availability check
        """
        hotels = hotel_crud.search_hotels(
            db,
            skip=skip,
            limit=limit,
            location=location,
            radius_km=radius_km,
            star_rating=star_rating,
            facilities=facilities,
            min_price=min_price,
            max_price=max_price
        )

        results = []
        for hotel in hotels:
            hotel_data = {
                "hotel": hotel,
                "business": hotel.business,
                "available_room_types": []
            }

            # If dates provided, check availability
            if check_in and check_out:
                available_rooms = room_type_crud.get_available_room_types(
                    db,
                    hotel_id=hotel.id,
                    check_in=check_in,
                    check_out=check_out,
                    number_of_rooms=rooms,
                    number_of_guests=guests
                )
                hotel_data["available_room_types"] = available_rooms
            else:
                hotel_data["room_types"] = hotel.room_types

            results.append(hotel_data)

        return results

    @staticmethod
    def create_booking(
            db: Session,
            *,
            current_user: User,
            hotel_id: UUID,
            room_type_id: UUID,
            check_in: date,
            check_out: date,
            number_of_rooms: int,
            number_of_guests: int,
            add_ons: List[dict],
            special_requests: Optional[str] = None
    ):
        """Create a new booking"""
        return hotel_booking_crud.create_booking(
            db,
            hotel_id=hotel_id,
            room_type_id=room_type_id,
            customer_id=current_user.id,
            check_in=check_in,
            check_out=check_out,
            number_of_rooms=number_of_rooms,
            number_of_guests=number_of_guests,
            add_ons=add_ons,
            special_requests=special_requests
        )


hotel_service = HotelService()