"""
app/services/food_service.py

Business logic for the Food & Restaurants module.

BLUEPRINT v2.0 COMPLIANCE:
- Radius-based search ONLY (no LGA filtering)
- Platform fee ₦50 deducted before crediting business wallet
- Wallet payment integration
- Order status transitions with notifications
- Halal dietary filter support

FIXES:
  - business_crud.get() / get_by_user_id() are async (AsyncCRUDBase).
    Replaced with direct db.query(Business) throughout — food module is sync.
  - wallet_crud.* are async (AsyncCRUDBase). Replaced with sync helpers:
    _get_or_create_wallet_sync, _debit_wallet_sync, _credit_wallet_sync.
  - business.owner_id does not exist on Business model — replaced with business.user_id.
  - cooking booking payment: cooking_service_crud.get(id=service.restaurant_id) was
    using the wrong CRUD class — fixed to restaurant_crud.get(id=service.restaurant_id).
  - TransactionType.ORDER_PAYMENT / BOOKING_PAYMENT don't exist in TransactionTypeEnum —
    replaced with TransactionTypeEnum.PAYMENT / CREDIT from wallet_model.
  - All notification_service.send() calls wrapped in try/except — non-fatal.
    A Redis/Celery outage must never roll back a completed, paid order.
"""

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_
from uuid import UUID
from datetime import datetime, date, time
from decimal import Decimal
import logging
import secrets

from app.crud.food_crud import (
    restaurant_crud,
    menu_category_crud,
    menu_item_crud,
    table_reservation_crud,
    food_order_crud,
    cooking_service_crud,
    cooking_booking_crud,
)
from app.models.user_model import User
from app.models.business_model import Business
from app.models.food_model import FoodOrder, Restaurant
from app.models.wallet_model import (
    Wallet,
    WalletTransaction,
    TransactionTypeEnum,
    TransactionStatusEnum,
)
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    InsufficientBalanceException,
)
from app.schemas.notifications_schema import NotificationPayload
from app.services.notification_service import notification_service

logger = logging.getLogger(__name__)


class FoodService:
    """Business logic for Food & Restaurants module."""

    # ------------------------------------------------------------------
    # SYNC WALLET HELPERS
    # wallet_crud uses AsyncCRUDBase — all its methods are coroutines.
    # Food service is sync so we implement lean sync equivalents here,
    # consistent with the pattern used in subscription_service.py.
    # ------------------------------------------------------------------

    @staticmethod
    def _get_or_create_wallet_sync(db: Session, *, user_id: UUID) -> Wallet:
        """Get wallet by user, creating one if it doesn't exist (sync)."""
        from app.models.wallet_model import generate_wallet_number
        wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()
        if not wallet:
            wallet = Wallet(
                user_id=user_id,
                wallet_number=generate_wallet_number(),
                balance=Decimal("0.00"),
                currency="NGN",
                is_active=True,
            )
            db.add(wallet)
            db.commit()
            db.refresh(wallet)
        return wallet

    @staticmethod
    def _debit_wallet_sync(
        db: Session,
        *,
        wallet_id: UUID,
        amount: Decimal,
        description: str,
        reference_id: Optional[str] = None,
    ) -> WalletTransaction:
        """Debit a wallet synchronously. Raises InsufficientBalanceException if low."""
        wallet = db.query(Wallet).filter(Wallet.id == wallet_id).first()
        if not wallet:
            raise NotFoundException("Wallet")
        if wallet.balance < amount:
            raise InsufficientBalanceException()

        balance_before = wallet.balance
        wallet.balance -= amount

        txn = WalletTransaction(
            wallet_id=wallet_id,
            transaction_type=TransactionTypeEnum.PAYMENT,
            amount=amount,
            balance_before=balance_before,
            balance_after=wallet.balance,
            status=TransactionStatusEnum.COMPLETED,
            description=description,
            reference_id=reference_id or f"FOOD_{secrets.token_hex(6).upper()}",
            completed_at=datetime.utcnow(),
        )
        db.add(txn)
        return txn  # caller commits

    @staticmethod
    def _credit_wallet_sync(
        db: Session,
        *,
        wallet_id: UUID,
        amount: Decimal,
        description: str,
        reference_id: Optional[str] = None,
    ) -> WalletTransaction:
        """Credit a wallet synchronously."""
        wallet = db.query(Wallet).filter(Wallet.id == wallet_id).first()
        if not wallet:
            raise NotFoundException("Wallet")

        balance_before = wallet.balance
        wallet.balance += amount

        txn = WalletTransaction(
            wallet_id=wallet_id,
            transaction_type=TransactionTypeEnum.CREDIT,
            amount=amount,
            balance_before=balance_before,
            balance_after=wallet.balance,
            status=TransactionStatusEnum.COMPLETED,
            description=description,
            reference_id=reference_id or f"FOOD_CR_{secrets.token_hex(6).upper()}",
            completed_at=datetime.utcnow(),
        )
        db.add(txn)
        return txn  # caller commits

    # ------------------------------------------------------------------
    # SEARCH & DISCOVERY (Radius-based ONLY — NO LGA)
    # ------------------------------------------------------------------

    def search_restaurants(
        self,
        db: Session,
        *,
        query_text: Optional[str] = None,
        cuisine_type: Optional[str] = None,
        location: Optional[tuple] = None,
        radius_km: float = 5.0,
        price_range: Optional[str] = None,
        offers_delivery: Optional[bool] = None,
        min_rating: Optional[Decimal] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Search and list restaurants with optional filters.

        BLUEPRINT v2.0 COMPLIANCE:
        - Radius-based ONLY (default 5 km)
        - NO LGA parameter or filtering
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
            # FIX: business_crud.get() is async — use direct sync query
            business = db.query(Business).filter(
                Business.id == restaurant.business_id
            ).first()
            if not business:
                continue
            results.append(self._serialize_restaurant(restaurant, business))
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

        # FIX: direct sync query
        business = db.query(Business).filter(
            Business.id == restaurant.business_id
        ).first()
        if not business:
            raise NotFoundException("Business")

        data = self._serialize_restaurant(restaurant, business)

        # Attach menu
        menu = menu_item_crud.get_restaurant_menu(db, restaurant_id=restaurant_id)
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
    # ORDER CREATION & PAYMENT (with Platform Fee)
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
        scheduled_delivery_time: Optional[datetime] = None,
        group_order_id: Optional[UUID] = None,
        is_group_order_host: bool = False,
        special_instructions: Optional[str] = None,
        payment_method: str = "wallet",
        tip: Decimal = Decimal("0.00"),
        promo_code: Optional[str] = None,
    ) -> FoodOrder:
        """
        Create a food order, validate items, calculate totals,
        process wallet payment, deduct platform fee, and credit business wallet.

        BLUEPRINT v2.0 COMPLIANCE:
        - Platform fee ₦50 deducted from total before crediting business
        - Customer pays full amount (including platform fee)
        - Business receives (total_amount - platform_fee)
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

        # Create the order (pricing + platform fee calculated in CRUD)
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
            scheduled_delivery_time=scheduled_delivery_time,
            group_order_id=group_order_id,
            is_group_order_host=is_group_order_host,
            special_instructions=special_instructions,
            payment_method=payment_method,
            tip=tip,
        )

        # FIX: Apply promo AFTER order creation — percentage coupons need the
        # order subtotal to compute the correct discount amount. Validating
        # before the order existed meant the function had no total to work with,
        # causing percentage coupons to return a fraction (0.20) that was
        # subtracted directly as ₦0.20 instead of computing e.g. 20% × ₦10,000.
        if promo_code:
            discount = self._validate_promo(
                db,
                promo_code=promo_code,
                restaurant_id=restaurant_id,
                order_subtotal=order.subtotal,  # percentage applied to item subtotal only
            )
            if discount > 0:
                order.discount = discount
                order.total_amount = max(
                    order.total_amount - discount, Decimal("0.00")
                )
                db.commit()
                db.refresh(order)

        # Process wallet payment (customer pays full amount)
        if payment_method == "wallet":
            # FIX: use sync wallet helpers — wallet_crud is async
            customer_wallet = self._get_or_create_wallet_sync(
                db, user_id=current_user.id
            )
            if customer_wallet.balance < order.total_amount:
                # Roll back the order on insufficient funds
                db.delete(order)
                db.commit()
                raise InsufficientBalanceException()

            # Debit customer wallet
            self._debit_wallet_sync(
                db,
                wallet_id=customer_wallet.id,
                amount=order.total_amount,
                description=f"Food order #{str(order.id)[:8].upper()}",
                reference_id=str(order.id),
            )

            # BLUEPRINT v2.0: Credit business wallet (total - platform fee)
            # FIX: business_crud.get() is async — direct sync query
            # FIX: business.owner_id doesn't exist — use business.user_id
            business = db.query(Business).filter(
                Business.id == restaurant.business_id
            ).first()
            if business:
                business_wallet = self._get_or_create_wallet_sync(
                    db, user_id=business.user_id
                )
                amount_to_credit = order.total_amount - order.platform_fee
                self._credit_wallet_sync(
                    db,
                    wallet_id=business_wallet.id,
                    amount=amount_to_credit,
                    description=(
                        f"Food order #{str(order.id)[:8].upper()} "
                        f"(after ₦{float(order.platform_fee)} platform fee)"
                    ),
                    reference_id=f"BIZ_{order.id}",
                )

            order.payment_status = "paid"
            db.commit()
            db.refresh(order)

        # Notifications are non-fatal — a Redis/Celery outage must never
        # roll back a completed, paid order.
        try:
            notification_service.send(
                db,
                payload=NotificationPayload(
                    user_id=current_user.id,
                    category="order",
                    title="Order Placed 🍽️",
                    body="Your order has been sent to the restaurant.",
                    action_url=f"/orders/{order.id}",
                ),
            )
            # Notify restaurant owner
            _biz = db.query(Business).filter(
                Business.id == restaurant.business_id
            ).first()
            if _biz:
                notification_service.send(
                    db,
                    payload=NotificationPayload(
                        user_id=_biz.user_id,
                        category="order",
                        title="New Order Received 🔔",
                        body=f"New {order_type} order from {customer_name}",
                        action_url=f"/dashboard/orders/{order.id}",
                    ),
                )
        except Exception as exc:
            logger.warning("Order notification failed (non-fatal): %s", exc)

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
            "pending":          ["confirmed", "cancelled"],
            "confirmed":        ["preparing", "cancelled"],
            "preparing":        ["ready"],
            "ready":            ["out_for_delivery", "delivered"],
            "out_for_delivery": ["delivered"],
        }

        order = food_order_crud.get(db, id=order_id)
        if not order:
            raise NotFoundException("Order")

        if order.restaurant_id != restaurant_id:
            from app.core.exceptions import PermissionDeniedException
            raise PermissionDeniedException()

        allowed = valid_transitions.get(
            order.order_status.value
            if hasattr(order.order_status, "value")
            else order.order_status,
            [],
        )
        if new_status not in allowed:
            raise ValidationException(
                f"Cannot transition from '{order.order_status}' to '{new_status}'"
            )

        order = food_order_crud.update_order_status(
            db, order_id=order_id, new_status=new_status
        )

        # Status-specific notifications to customer
        status_messages = {
            "confirmed":        ("Order Confirmed ✅",    "The restaurant has confirmed your order."),
            "preparing":        ("Being Prepared 🍳",     "The kitchen is preparing your food."),
            "ready":            ("Order Ready ✅",         "Your order is ready for pickup / awaiting rider."),
            "out_for_delivery": ("On the Way 🛵",          "Your order is on its way!"),
            "delivered":        ("Delivered 🎉",           "Your order has been delivered. Enjoy!"),
            "cancelled":        ("Order Cancelled",        "Your order has been cancelled."),
        }

        if new_status in status_messages:
            title, body = status_messages[new_status]
            try:
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
            except Exception as exc:
                logger.warning("Status notification failed (non-fatal): %s", exc)

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
        deposit_amount: Decimal = Decimal("0.00"),
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
            deposit_amount=deposit_amount,
        )

        # Process deposit payment if required
        if deposit_amount > 0:
            # FIX: sync wallet helpers
            customer_wallet = self._get_or_create_wallet_sync(
                db, user_id=current_user.id
            )
            if customer_wallet.balance < deposit_amount:
                raise InsufficientBalanceException()

            self._debit_wallet_sync(
                db,
                wallet_id=customer_wallet.id,
                amount=deposit_amount,
                description=f"Reservation deposit - {restaurant_id}",
                reference_id=str(reservation.id),
            )
            reservation.deposit_paid = True
            db.commit()
            db.refresh(reservation)

        try:
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
        except Exception as exc:
            logger.warning("Reservation notification failed (non-fatal): %s", exc)

        return reservation

    # ------------------------------------------------------------------
    # COOKING SERVICES (Catering, Private Chef, Meal Prep, Classes)
    # ------------------------------------------------------------------

    def create_cooking_booking(
        self,
        db: Session,
        *,
        current_user: User,
        service_id: UUID,
        event_date: date,
        event_time: time,
        number_of_guests: int,
        event_address: str,
        event_location: Optional[tuple] = None,
        menu_requirements: Optional[str] = None,
        dietary_restrictions: Optional[List[str]] = None,
        special_requests: Optional[str] = None,
    ):
        """Create cooking service booking with payment."""
        service = cooking_service_crud.get(db, id=service_id)
        if not service:
            raise NotFoundException("CookingService")

        if not service.is_active:
            raise ValidationException("This service is not currently available")

        if number_of_guests < service.min_guests:
            raise ValidationException(
                f"Minimum {service.min_guests} guests required"
            )

        if service.max_guests and number_of_guests > service.max_guests:
            raise ValidationException(
                f"Maximum {service.max_guests} guests allowed"
            )

        # Create booking (platform fee calculated in CRUD)
        booking = cooking_booking_crud.create_booking(
            db,
            service_id=service_id,
            customer_id=current_user.id,
            event_date=event_date,
            event_time=event_time,
            number_of_guests=number_of_guests,
            event_address=event_address,
            event_location=event_location,
            menu_requirements=menu_requirements,
            dietary_restrictions=dietary_restrictions,
            special_requests=special_requests,
        )

        # FIX: sync wallet helpers
        customer_wallet = self._get_or_create_wallet_sync(
            db, user_id=current_user.id
        )
        if customer_wallet.balance < booking.total_price:
            db.delete(booking)
            db.commit()
            raise InsufficientBalanceException()

        # Debit customer
        self._debit_wallet_sync(
            db,
            wallet_id=customer_wallet.id,
            amount=booking.total_price,
            description=f"Cooking service booking #{str(booking.id)[:8].upper()}",
            reference_id=str(booking.id),
        )

        # Credit business (total - platform fee)
        # FIX: was cooking_service_crud.get(id=service.restaurant_id) — wrong CRUD class
        # FIX: business.owner_id → business.user_id
        restaurant = restaurant_crud.get(db, id=service.restaurant_id)
        if restaurant:
            business = db.query(Business).filter(
                Business.id == restaurant.business_id
            ).first()
            if business:
                business_wallet = self._get_or_create_wallet_sync(
                    db, user_id=business.user_id
                )
                amount_to_credit = booking.total_price - booking.platform_fee
                self._credit_wallet_sync(
                    db,
                    wallet_id=business_wallet.id,
                    amount=amount_to_credit,
                    description=(
                        f"Cooking booking #{str(booking.id)[:8].upper()} "
                        f"(after ₦{float(booking.platform_fee)} platform fee)"
                    ),
                    reference_id=f"BIZ_{booking.id}",
                )

        booking.payment_status = "paid"
        db.commit()
        db.refresh(booking)

        return booking

    # ------------------------------------------------------------------
    # PROMO / COUPON VALIDATION
    # ------------------------------------------------------------------

    def _validate_promo(
        self,
        db: Session,
        *,
        promo_code: str,
        restaurant_id: UUID,
        order_subtotal: Optional[Decimal] = None,
    ) -> Decimal:
        """
        Validate promo code server-side and return the flat discount amount.

        FIX: Percentage coupons previously returned `discount_value / 100`
        (a fraction like 0.20) which was then subtracted from the order total
        as ₦0.20 instead of computing 20% × subtotal = ₦2,000.

        Returns a flat Naira amount to subtract from `order.total_amount`.
        Returns Decimal('0.00') for invalid/expired codes or when
        order_subtotal is missing for a percentage coupon.
        """
        try:
            from app.models.coupon_model import Coupon

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
                # FIX: compute the actual Naira discount from the subtotal.
                # Without order_subtotal we cannot calculate the amount — skip.
                if order_subtotal is None or order_subtotal <= 0:
                    return Decimal("0.00")
                rate = Decimal(str(coupon.discount_value)) / 100   # e.g. 0.20
                return (order_subtotal * rate).quantize(Decimal("0.01"))
            else:
                # Flat amount coupon — return as-is
                return Decimal(str(coupon.discount_value))
        except Exception:
            return Decimal("0.00")

    # ------------------------------------------------------------------
    # SERIALISATION HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_restaurant(restaurant: Restaurant, business: Business) -> Dict[str, Any]:
        """
        Produce a flat, JSON-serialisable dict for a restaurant.

        BLUEPRINT v2.0 COMPLIANCE:
        - NO lga_name field (removed)
        - Added virtual_tour_url
        - Added real_time_wait_minutes
        """
        return {
            "id":                            str(restaurant.id),
            "business_id":                   str(restaurant.business_id),
            "name":                          business.business_name,
            "description":                   business.description,
            "address":                       business.address,
            "phone":                         getattr(business, "business_phone", None),
            "cover_image_url":               getattr(business, "logo", None),
            "is_verified":                   business.is_verified,
            "is_active":                     business.is_active,
            "rating":                        float(business.average_rating or 0),
            "review_count":                  business.total_reviews or 0,
            # Restaurant-specific fields
            "cuisine_types":                 restaurant.cuisine_types or [],
            "price_range":                   restaurant.price_range,
            "opening_time":                  (
                restaurant.opening_time.strftime("%H:%M")
                if restaurant.opening_time else None
            ),
            "closing_time":                  (
                restaurant.closing_time.strftime("%H:%M")
                if restaurant.closing_time else None
            ),
            "is_open":                       True,  # TODO: compute from opening/closing_time + timezone
            "offers_delivery":               restaurant.offers_delivery,
            "offers_takeout":                restaurant.offers_takeout,
            "offers_dine_in":                restaurant.offers_dine_in,
            "offers_reservations":           restaurant.offers_reservations,
            "delivery_fee":                  float(restaurant.delivery_fee or 0),
            "free_delivery_minimum":         float(restaurant.free_delivery_minimum or 0),
            "minimum_order":                 float(restaurant.free_delivery_minimum or 0),
            "delivery_radius_km":            float(restaurant.delivery_radius_km or 10),
            "average_delivery_time_minutes": (
                restaurant.average_delivery_time_minutes or 45
            ),
            "delivery_time":                 (
                f"{restaurant.average_delivery_time_minutes or 30}–"
                f"{(restaurant.average_delivery_time_minutes or 30) + 15} min"
            ),
            # BLUEPRINT v2.0: New fields
            "virtual_tour_url":              restaurant.virtual_tour_url,
            "real_time_wait_minutes":        restaurant.real_time_wait_minutes,
            "features":                      restaurant.features or [],
            "gallery_images":                restaurant.gallery_images or [],
            "total_orders":                  restaurant.total_orders or 0,
        }

    @staticmethod
    def _serialize_menu_item(item) -> Dict[str, Any]:
        """Produce a full menu-item dict including all fields Flutter needs."""
        return {
            "id":                       str(item.id),
            "category_id":              str(item.category_id),
            "name":                     item.name,
            "description":              item.description,
            "price":                    float(item.price),
            "discount_price":           (
                float(item.discount_price) if item.discount_price else None
            ),
            "preparation_time_minutes": item.preparation_time_minutes,
            "calories":                 item.calories,
            "is_vegetarian":            item.is_vegetarian,
            "is_vegan":                 item.is_vegan,
            "is_gluten_free":           item.is_gluten_free,
            "is_halal":                 item.is_halal,
            "is_spicy":                 item.is_spicy,
            "spice_level":              item.spice_level,
            "allergens":                item.allergens or [],
            "image_url":                item.image_url,
            "images":                   item.images or [],
            "modifiers":                item.modifiers or [],
            "is_available":             item.is_available,
            "popularity_score":         item.popularity_score or 0,
        }


food_service = FoodService()