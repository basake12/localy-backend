from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import date, time, datetime
from decimal import Decimal

from app.crud.food import (
    restaurant_crud,
    menu_category_crud,
    menu_item_crud,
    table_reservation_crud,
    food_order_crud
)
from app.crud.wallet import wallet_crud
from app.crud.business import business_crud
from app.crud.delivery import delivery_crud
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    InsufficientBalanceException
)
from app.core.constants import TransactionType
from app.models.user import User
from app.models.food import FoodOrder


class FoodService:
    """Business logic for food operations"""

    @staticmethod
    def search_restaurants(
            db: Session,
            *,
            query_text: Optional[str] = None,
            cuisine_type: Optional[str] = None,
            location: Optional[tuple] = None,
            radius_km: float = 10.0,
            price_range: Optional[str] = None,
            offers_delivery: Optional[bool] = None,
            min_rating: Optional[Decimal] = None,
            skip: int = 0,
            limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Search restaurants with business info"""
        restaurants = restaurant_crud.search_restaurants(
            db,
            query_text=query_text,
            cuisine_type=cuisine_type,
            location=location,
            radius_km=radius_km,
            price_range=price_range,
            offers_delivery=offers_delivery,
            min_rating=min_rating,
            skip=skip,
            limit=limit
        )

        # Enrich with business info
        results = []
        for restaurant in restaurants:
            business = business_crud.get(db, id=restaurant.business_id)

            results.append({
                "restaurant": restaurant,
                "business": business
            })

        return results

    @staticmethod
    def get_restaurant_details(
            db: Session,
            *,
            restaurant_id: UUID
    ) -> Dict[str, Any]:
        """Get full restaurant details with menu"""
        restaurant = restaurant_crud.get(db, id=restaurant_id)
        if not restaurant:
            raise NotFoundException("Restaurant")

        business = business_crud.get(db, id=restaurant.business_id)
        menu = menu_item_crud.get_restaurant_menu(db, restaurant_id=restaurant_id)

        return {
            "restaurant": restaurant,
            "business": business,
            "menu": menu
        }

    @staticmethod
    def create_order_and_pay(
            db: Session,
            *,
            current_user: User,
            restaurant_id: UUID,
            order_type: str,
            items: List[Dict[str, Any]],
            customer_name: str,
            customer_phone: str,
            delivery_address: Optional[str] = None,
            delivery_location: Optional[tuple] = None,
            delivery_instructions: Optional[str] = None,
            special_instructions: Optional[str] = None,
            payment_method: str = "wallet",
            tip: Decimal = Decimal('0.00')
    ) -> FoodOrder:
        """Create order and process payment"""
        # Create order
        order = food_order_crud.create_food_order(
            db,
            restaurant_id=restaurant_id,
            customer_id=current_user.id,
            order_type=order_type,
            items=items,
            customer_name=customer_name,
            customer_phone=customer_phone,
            delivery_address=delivery_address,
            delivery_location=delivery_location,
            delivery_instructions=delivery_instructions,
            special_instructions=special_instructions,
            payment_method=payment_method,
            tip=tip
        )

        # Process payment
        if payment_method == "wallet":
            wallet = wallet_crud.get_or_create_wallet(db, user_id=current_user.id)

            # Check balance
            if wallet.balance < order.total_amount:
                # Cancel order
                order.order_status = "cancelled"
                order.cancellation_reason = "Insufficient wallet balance"
                db.commit()
                raise InsufficientBalanceException()

            # Debit wallet
            wallet_crud.debit_wallet(
                db,
                wallet_id=wallet.id,
                amount=order.total_amount,
                transaction_type=TransactionType.PAYMENT,
                description=f"Payment for food order {order.id}",
                reference_id=str(order.id)
            )

            # Update payment status
            order.payment_status = "paid"
            order.order_status = "confirmed"
            order.payment_reference = str(order.id)
            db.commit()
            db.refresh(order)

        # Create delivery if order type is delivery
        if order_type == "delivery" and delivery_location:
            restaurant = restaurant_crud.get(db, id=restaurant_id)
            business = business_crud.get(db, id=restaurant.business_id)

            # Get restaurant location (simplified - would extract from Geography field)
            # TODO: Extract actual coordinates from business.location

            # For now, skip automatic delivery creation
            # In production, this would create a delivery request

        return order

    @staticmethod
    def make_reservation(
            db: Session,
            *,
            current_user: User,
            restaurant_id: UUID,
            reservation_date: date,
            reservation_time: time,
            number_of_guests: int,
            customer_name: str,
            customer_phone: str,
            customer_email: Optional[str] = None,
            seating_preference: Optional[str] = None,
            special_requests: Optional[str] = None,
            occasion: Optional[str] = None
    ):
        """Create table reservation"""
        # Validate restaurant exists
        restaurant = restaurant_crud.get(db, id=restaurant_id)
        if not restaurant:
            raise NotFoundException("Restaurant")

        if not restaurant.offers_reservations:
            raise ValidationException("Restaurant does not accept reservations")

        # Check capacity
        if number_of_guests > restaurant.seating_capacity:
            raise ValidationException(f"Maximum capacity is {restaurant.seating_capacity} guests")

        # Create reservation
        reservation = table_reservation_crud.create_reservation(
            db,
            restaurant_id=restaurant_id,
            customer_id=current_user.id,
            reservation_date=reservation_date,
            reservation_time=reservation_time,
            number_of_guests=number_of_guests,
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_email=customer_email,
            seating_preference=seating_preference,
            special_requests=special_requests,
            occasion=occasion
        )

        # Update restaurant stats
        restaurant.total_reservations += 1
        db.commit()

        return reservation


food_service = FoodService()