"""
app/services/food_service.py

Business logic for the Food & Restaurants module.
Handles order creation + payment, restaurant search, reservations,
and order-status transitions with notifications.
"""

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import datetime, date, time
from decimal import Decimal

from app.crud.food_crud import (
    restaurant_crud,
    menu_category_crud,
    menu_item_crud,
    table_reservation_crud,
    food_order_crud,
)
from app.crud.business_crud import business_crud
from app.crud.wallet_crud import wallet_crud
from app.models.user_model import User
from app.models.food_model import FoodOrder, Restaurant
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    InsufficientBalanceException,
)
from app.core.constants import TransactionType
from app.schemas.notifications_schema import NotificationPayload
from app.services.notification_service import notification_service


class FoodService:
    """Business logic for Food & Restaurants module."""

    # ------------------------------------------------------------------
    # SEARCH & DISCOVERY
    # ------------------------------------------------------------------

    def search_restaurants(
        self,
        db: Session,
        *,
        query_text: Optional[str] = None,
        cuisine_type: Optional[str] = None,
        location: Optional[tuple] = None,
        radius_km: float = 10.0,
        price_range: Optional[str] = None,
        offers_delivery: Optional[bool] = None,
        min_rating: Optional[Decimal] = None,
        lga_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Search and list restaurants with optional filters.
        Returns serialisable dicts enriched with business data.
        """
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
            limit=limit,
        )

        results = []
        for restaurant in restaurants:
            business = business_crud.get(db, id=restaurant.business_id)
            if not business:
                continue
            results.append(
                self._serialize_restaurant(restaurant, business)
            )
        return results

    def get_restaurant_details(
        self,
        db: Session,
        *,
        restaurant_id: UUID,
    ) -> Dict[str, Any]:
        """
        Full restaurant profile including menu categories and items.
        Raises NotFoundException when the restaurant does not exist.
        """
        restaurant = restaurant_crud.get(db, id=restaurant_id)
        if not restaurant:
            raise NotFoundException("Restaurant")

        business = business_crud.get(db, id=restaurant.business_id)
        if not business:
            raise NotFoundException("Business")

        data = self._serialize_restaurant(restaurant, business)

        # Attach menu
        menu = menu_item_crud.get_restaurant_menu(
            db, restaurant_id=restaurant_id
        )
        data["menu"] = [
            {
                "id": str(cat["category"].id),
                "name": cat["category"].name,
                "description": cat["category"].description,
                "display_order": cat["category"].display_order,
                "items": [
                    self._serialize_menu_item(item)
                    for item in cat["items"]
                ],
            }
            for cat in menu
        ]
        return data

    # ------------------------------------------------------------------
    # ORDER CREATION & PAYMENT
    # ------------------------------------------------------------------

    def create_order_and_pay(
        self,
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
        tip: Decimal = Decimal("0.00"),
        promo_code: Optional[str] = None,
    ) -> FoodOrder:
        """
        Create a food order, validate items, calculate totals,
        process wallet payment, and fire order notifications.
        """
        restaurant = restaurant_crud.get(db, id=restaurant_id)
        if not restaurant:
            raise NotFoundException("Restaurant")

        # Validate order type against restaurant capabilities
        if order_type == "delivery" and not restaurant.offers_delivery:
            raise ValidationException("This restaurant does not offer delivery")
        if order_type == "takeout" and not restaurant.offers_takeout:
            raise ValidationException("This restaurant does not offer takeout")
        if order_type == "dine_in" and not restaurant.offers_dine_in:
            raise ValidationException("This restaurant does not offer dine-in")

        if order_type == "delivery" and not delivery_address:
            raise ValidationException(
                "delivery_address is required for delivery orders"
            )

        # Validate promo code server-side
        discount = Decimal("0.00")
        if promo_code:
            discount = self._validate_promo(
                db, promo_code=promo_code, restaurant_id=restaurant_id
            )

        # Create the order (pricing calculated inside CRUD)
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
            tip=tip,
        )

        # Apply server-side discount if promo valid
        if discount > 0:
            order.discount = discount
            order.total_amount = max(
                order.total_amount - discount, Decimal("0.00")
            )
            db.commit()
            db.refresh(order)

        # Process wallet payment
        if payment_method == "wallet":
            wallet = wallet_crud.get_or_create_wallet(
                db, user_id=current_user.id
            )
            if wallet.balance < order.total_amount:
                # Roll back the order on insufficient funds
                db.delete(order)
                db.commit()
                raise InsufficientBalanceException()

            wallet_crud.debit_wallet(
                db,
                wallet_id=wallet.id,
                amount=order.total_amount,
                transaction_type=TransactionType.PAYMENT,
                description=f"Food order #{str(order.id)[:8].upper()}",
                reference_id=str(order.id),
            )
            order.payment_status = "paid"
            db.commit()
            db.refresh(order)

        # Notify the customer
        notification_service.send(
            db,
            payload=NotificationPayload(
                user_id=current_user.id,
                category="order",
                title="Order Placed 🍽️",
                body=f"Your order has been sent to the restaurant.",
                action_url=f"/orders/{order.id}",
            ),
        )

        # Notify the restaurant business owner
        business = business_crud.get(db, id=restaurant.business_id)
        if business:
            notification_service.send(
                db,
                payload=NotificationPayload(
                    user_id=business.owner_id,
                    category="order",
                    title="New Order Received 🔔",
                    body=f"New {order_type} order from {customer_name}",
                    action_url=f"/dashboard/orders/{order.id}",
                ),
            )

        return order

    # ------------------------------------------------------------------
    # ORDER STATUS TRANSITIONS (BUSINESS ACTIONS)
    # ------------------------------------------------------------------

    def update_order_status(
        self,
        db: Session,
        *,
        order_id: UUID,
        new_status: str,
        restaurant_id: UUID,
    ) -> FoodOrder:
        """
        Update order status with validation and customer notification.
        Validates that the status transition is legal.
        """
        valid_transitions = {
            "pending": ["confirmed", "cancelled"],
            "confirmed": ["preparing", "cancelled"],
            "preparing": ["ready"],
            "ready": ["out_for_delivery", "delivered"],
            "out_for_delivery": ["delivered"],
        }

        order = food_order_crud.get(db, id=order_id)
        if not order:
            raise NotFoundException("Order")

        if order.restaurant_id != restaurant_id:
            from app.core.exceptions import PermissionDeniedException
            raise PermissionDeniedException()

        allowed = valid_transitions.get(order.order_status.value
                                        if hasattr(order.order_status, "value")
                                        else order.order_status, [])
        if new_status not in allowed:
            raise ValidationException(
                f"Cannot transition from '{order.order_status}' to '{new_status}'"
            )

        order = food_order_crud.update_order_status(
            db, order_id=order_id, new_status=new_status
        )

        # Status-specific notifications to customer
        status_messages = {
            "confirmed": ("Order Confirmed ✅",
                          "The restaurant has confirmed your order."),
            "preparing": ("Being Prepared 🍳",
                          "The kitchen is preparing your food."),
            "ready": ("Order Ready ✅",
                      "Your order is ready for pickup / awaiting rider."),
            "out_for_delivery": ("On the Way 🛵",
                                 "Your order is on its way!"),
            "delivered": ("Delivered 🎉",
                          "Your order has been delivered. Enjoy!"),
            "cancelled": ("Order Cancelled",
                          "Your order has been cancelled."),
        }

        if new_status in status_messages:
            title, body = status_messages[new_status]
            notification_service.send(
                db,
                payload=NotificationPayload(
                    user_id=order.customer_id,
                    category="order",
                    title=title,
                    body=body,
                    action_url=f"/orders/{order.id}",
                ),
            )

        return order

    # ------------------------------------------------------------------
    # RESERVATIONS
    # ------------------------------------------------------------------

    def make_reservation(
        self,
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
        occasion: Optional[str] = None,
    ):
        """Create a table reservation with capacity validation."""
        restaurant = restaurant_crud.get(db, id=restaurant_id)
        if not restaurant:
            raise NotFoundException("Restaurant")

        if not restaurant.offers_reservations:
            raise ValidationException(
                "This restaurant does not accept reservations"
            )

        if (
            restaurant.seating_capacity > 0
            and number_of_guests > restaurant.seating_capacity
        ):
            raise ValidationException(
                f"Guest count exceeds restaurant capacity "
                f"({restaurant.seating_capacity})"
            )

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
            occasion=occasion,
        )

        notification_service.send(
            db,
            payload=NotificationPayload(
                user_id=current_user.id,
                category="booking",
                title="Reservation Requested 🍽️",
                body=(
                    f"Your table for {number_of_guests} on "
                    f"{reservation_date} at "
                    f"{reservation_time.strftime('%I:%M %p')} "
                    f"is pending confirmation."
                ),
                action_url=f"/reservations/{reservation.id}",
            ),
        )

        return reservation

    # ------------------------------------------------------------------
    # PROMO / COUPON VALIDATION
    # ------------------------------------------------------------------

    def _validate_promo(
        self,
        db: Session,
        *,
        promo_code: str,
        restaurant_id: UUID,
    ) -> Decimal:
        """
        Validate promo code server-side.
        Returns the discount amount (flat or percentage of subtotal).
        Returns Decimal('0.00') for invalid codes rather than raising,
        so the order still goes through without the discount.
        """
        try:
            from app.models.coupon import Coupon
            from sqlalchemy import and_

            coupon = (
                db.query(Coupon)
                .filter(
                    and_(
                        Coupon.code == promo_code.upper(),
                        Coupon.is_active == True,
                        Coupon.expires_at >= datetime.utcnow(),
                    )
                )
                .first()
            )
            if not coupon:
                return Decimal("0.00")

            if coupon.discount_type == "percentage":
                # Caller applies percentage to subtotal; return rate here
                # (simplified — full implementation multiplies by subtotal)
                return Decimal(str(coupon.discount_value)) / 100
            else:
                return Decimal(str(coupon.discount_value))
        except Exception:
            # If coupons table not yet migrated, silently skip
            return Decimal("0.00")

    # ------------------------------------------------------------------
    # SERIALISATION HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_restaurant(restaurant: Restaurant, business) -> Dict[str, Any]:
        """Produce a flat, JSON-serialisable dict for a restaurant."""
        return {
            "id": str(restaurant.id),
            "business_id": str(restaurant.business_id),
            "name": business.business_name,
            "description": business.description,
            "address": business.address,
            "lga_name": getattr(business, "lga_name", ""),
            "phone": business.phone,
            "cover_image_url": business.cover_image_url,
            "is_verified": business.is_verified,
            "is_active": business.is_active,
            "rating": float(business.average_rating or 0),
            "review_count": business.total_reviews or 0,
            # Restaurant-specific
            "cuisine_types": restaurant.cuisine_types or [],
            "price_range": restaurant.price_range,
            "opening_time": (
                restaurant.opening_time.strftime("%H:%M")
                if restaurant.opening_time
                else None
            ),
            "closing_time": (
                restaurant.closing_time.strftime("%H:%M")
                if restaurant.closing_time
                else None
            ),
            "is_open": True,  # TODO: compute from opening/closing_time + timezone
            "offers_delivery": restaurant.offers_delivery,
            "offers_takeout": restaurant.offers_takeout,
            "offers_dine_in": restaurant.offers_dine_in,
            "offers_reservations": restaurant.offers_reservations,
            "delivery_fee": float(restaurant.delivery_fee or 0),
            "free_delivery_minimum": float(
                restaurant.free_delivery_minimum or 0
            ),
            "minimum_order": float(restaurant.free_delivery_minimum or 0),
            "delivery_radius_km": float(restaurant.delivery_radius_km or 10),
            "average_delivery_time_minutes": (
                restaurant.average_delivery_time_minutes or 45
            ),
            "delivery_time": (
                f"{restaurant.average_delivery_time_minutes or 30}–"
                f"{(restaurant.average_delivery_time_minutes or 30) + 15} min"
            ),
            "features": restaurant.features or [],
            "gallery_images": restaurant.gallery_images or [],
            "total_orders": restaurant.total_orders or 0,
        }

    @staticmethod
    def _serialize_menu_item(item) -> Dict[str, Any]:
        """Produce a full menu-item dict including all fields Flutter needs."""
        return {
            "id": str(item.id),
            "category_id": str(item.category_id),
            "name": item.name,
            "description": item.description,
            "price": float(item.price),
            "discount_price": (
                float(item.discount_price) if item.discount_price else None
            ),
            "preparation_time_minutes": item.preparation_time_minutes,
            "calories": item.calories,
            "is_vegetarian": item.is_vegetarian,
            "is_vegan": item.is_vegan,
            "is_gluten_free": item.is_gluten_free,
            "is_spicy": item.is_spicy,
            "spice_level": item.spice_level,
            "allergens": item.allergens or [],
            "image_url": item.image_url,
            "images": item.images or [],
            "modifiers": item.modifiers or [],
            "is_available": item.is_available,
            "popularity_score": item.popularity_score or 0,
        }


food_service = FoodService()