from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from uuid import UUID
from datetime import datetime, date, time, timezone
from decimal import Decimal
import secrets
import string

from app.crud.base_crud import CRUDBase
from app.models.food_model import (
    Restaurant, MenuCategory, MenuItem,
    TableReservation, FoodOrder, FoodOrderItem,
    CookingService, CookingBooking,
    OrderStatusEnum
)
from app.models.business_model import Business
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
)


class CRUDRestaurant(CRUDBase[Restaurant, dict, dict]):
    """CRUD for Restaurant"""

    def get_by_business_id(
            self,
            db: Session,
            *,
            business_id: UUID
    ) -> Optional[Restaurant]:
        """Get restaurant by business ID"""
        return db.query(Restaurant).filter(
            Restaurant.business_id == business_id
        ).first()

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
            limit: int = 20
    ) -> List[Restaurant]:
        """
        Search restaurants with filters.

        BLUEPRINT v2.0 COMPLIANCE:
        - Radius-based location search ONLY (default 5 km)
        - NO LGA filtering — completely removed
        - Uses PostGIS ST_DWithin for geo queries
        """
        query = db.query(Restaurant).join(Business)

        # Text search
        if query_text:
            search_filter = or_(
                Business.business_name.ilike(f"%{query_text}%"),
                Business.description.ilike(f"%{query_text}%")
            )
            query = query.filter(search_filter)

        # Cuisine filter
        if cuisine_type:
            query = query.filter(
                Restaurant.cuisine_types.contains([cuisine_type])
            )

        # BLUEPRINT v2.0: Location filter (radius-based ONLY, no LGA)
        if location:
            lat, lng = location
            # Convert km to meters for PostGIS
            radius_meters = radius_km * 1000
            point = func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)
            # FIX: Include restaurants whose business has no location set
            # ST_DWithin(NULL, ...) returns NULL → falsy → silently filters everything out
            query = query.filter(
                or_(
                    Business.location.is_(None),
                    func.ST_DWithin(Business.location, point, radius_meters)
                )
            )

        # Price range filter
        if price_range:
            query = query.filter(Restaurant.price_range == price_range)

        # Delivery filter
        if offers_delivery is not None:
            query = query.filter(Restaurant.offers_delivery == offers_delivery)

        # Rating filter
        if min_rating:
            query = query.filter(Business.average_rating >= min_rating)

        # Only active businesses
        query = query.filter(Business.is_active == True)

        return query.order_by(
            Business.average_rating.desc()
        ).offset(skip).limit(limit).all()


class CRUDMenuCategory(CRUDBase[MenuCategory, dict, dict]):
    """CRUD for MenuCategory"""

    def get_by_restaurant(
            self,
            db: Session,
            *,
            restaurant_id: UUID,
            active_only: bool = True
    ) -> List[MenuCategory]:
        """Get menu categories for restaurant"""
        query = db.query(MenuCategory).filter(
            MenuCategory.restaurant_id == restaurant_id
        )

        if active_only:
            query = query.filter(MenuCategory.is_active == True)

        return query.order_by(MenuCategory.display_order).all()


class CRUDMenuItem(CRUDBase[MenuItem, dict, dict]):
    """CRUD for MenuItem"""

    def get_by_category(
            self,
            db: Session,
            *,
            category_id: UUID,
            available_only: bool = True
    ) -> List[MenuItem]:
        """Get menu items for category"""
        query = db.query(MenuItem).filter(
            MenuItem.category_id == category_id
        )

        if available_only:
            query = query.filter(MenuItem.is_available == True)

        return query.order_by(MenuItem.display_order).all()

    def get_restaurant_menu(
            self,
            db: Session,
            *,
            restaurant_id: UUID
    ) -> List[Dict[str, Any]]:
        """Get full menu with categories and items"""
        categories = db.query(MenuCategory).filter(
            and_(
                MenuCategory.restaurant_id == restaurant_id,
                MenuCategory.is_active == True
            )
        ).order_by(MenuCategory.display_order).all()

        menu = []
        for category in categories:
            items = self.get_by_category(
                db,
                category_id=category.id,
                available_only=True
            )

            menu.append({
                "category": category,
                "items": items
            })

        return menu

    def search_menu_items(
            self,
            db: Session,
            *,
            restaurant_id: UUID,
            query_text: Optional[str] = None,
            is_vegetarian: Optional[bool] = None,
            is_vegan: Optional[bool] = None,
            is_halal: Optional[bool] = None,
            is_gluten_free: Optional[bool] = None,
            max_price: Optional[Decimal] = None
    ) -> List[MenuItem]:
        """Search menu items with dietary filters"""
        query = db.query(MenuItem).join(MenuCategory).filter(
            and_(
                MenuCategory.restaurant_id == restaurant_id,
                MenuItem.is_available == True
            )
        )

        if query_text:
            query = query.filter(
                or_(
                    MenuItem.name.ilike(f"%{query_text}%"),
                    MenuItem.description.ilike(f"%{query_text}%")
                )
            )

        if is_vegetarian:
            query = query.filter(MenuItem.is_vegetarian == True)

        if is_vegan:
            query = query.filter(MenuItem.is_vegan == True)

        # BLUEPRINT v2.0: Halal dietary filter
        if is_halal:
            query = query.filter(MenuItem.is_halal == True)

        if is_gluten_free:
            query = query.filter(MenuItem.is_gluten_free == True)

        if max_price:
            query = query.filter(MenuItem.price <= max_price)

        return query.all()


class CRUDTableReservation(CRUDBase[TableReservation, dict, dict]):
    """CRUD for TableReservation"""

    def _generate_confirmation_code(self, db: Session) -> str:
        """Generate a cryptographically safe unique confirmation code."""
        alphabet = string.ascii_uppercase + string.digits
        while True:
            code = ''.join(secrets.choice(alphabet) for _ in range(8))
            existing = db.query(TableReservation).filter(
                TableReservation.confirmation_code == code
            ).first()
            if not existing:
                return code

    def create_reservation(
            self,
            db: Session,
            *,
            restaurant_id: UUID,
            customer_id: UUID,
            reservation_date: date,
            reservation_time: time,
            number_of_guests: int,
            customer_name: str,
            customer_phone: str,
            customer_email: Optional[str] = None,
            seating_preference: Optional[str] = None,
            special_requests: Optional[str] = None,
            occasion: Optional[str] = None,
            deposit_amount: Decimal = Decimal('0.00')
    ) -> TableReservation:
        """Create a table reservation with confirmation code"""
        confirmation_code = self._generate_confirmation_code(db)

        reservation = TableReservation(
            restaurant_id=restaurant_id,
            customer_id=customer_id,
            reservation_date=reservation_date,
            reservation_time=reservation_time,
            number_of_guests=number_of_guests,
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_email=customer_email,
            seating_preference=seating_preference,
            special_requests=special_requests,
            occasion=occasion,
            confirmation_code=confirmation_code,
            deposit_amount=deposit_amount,
            deposit_paid=False
        )

        db.add(reservation)
        db.commit()
        db.refresh(reservation)

        return reservation

    def get_restaurant_reservations(
            self,
            db: Session,
            *,
            restaurant_id: UUID,
            status: Optional[str] = None,
            reservation_date: Optional[date] = None,
            skip: int = 0,
            limit: int = 50
    ) -> List[TableReservation]:
        """Get restaurant reservations with filters"""
        query = db.query(TableReservation).filter(
            TableReservation.restaurant_id == restaurant_id
        )

        if status:
            query = query.filter(TableReservation.status == status)

        if reservation_date:
            query = query.filter(TableReservation.reservation_date == reservation_date)

        return query.order_by(
            TableReservation.reservation_date.desc(),
            TableReservation.reservation_time.desc()
        ).offset(skip).limit(limit).all()


class CRUDFoodOrder(CRUDBase[FoodOrder, dict, dict]):
    """CRUD for FoodOrder"""

    def create_food_order(
            self,
            db: Session,
            *,
            restaurant_id: UUID,
            customer_id: UUID,
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
            tip: Decimal = Decimal('0.00')
    ) -> FoodOrder:
        """
        Create a food order with platform fee calculation.

        BLUEPRINT v2.0 COMPLIANCE:
        - Platform fee: ₦50 flat fee on all food orders
        - Fee added to total_amount before payment
        """
        # Get restaurant
        restaurant = restaurant_crud.get(db, id=restaurant_id)
        if not restaurant:
            raise NotFoundException("Restaurant")

        # Calculate pricing
        subtotal = Decimal('0.00')
        order_items_data = []

        for item_data in items:
            menu_item_id = item_data['menu_item_id']
            quantity = item_data['quantity']
            selected_modifiers = item_data.get('selected_modifiers', [])

            # Get menu item
            menu_item = menu_item_crud.get(db, id=menu_item_id)
            if not menu_item or not menu_item.is_available:
                raise NotFoundException(f"Menu item {menu_item_id}")

            # Calculate price
            unit_price = menu_item.discount_price or menu_item.price

            # Add modifier prices
            for modifier in selected_modifiers:
                if 'price' in modifier:
                    unit_price += Decimal(str(modifier['price']))

            item_total = unit_price * quantity
            subtotal += item_total

            order_items_data.append({
                'menu_item_id': menu_item_id,
                'item_name': menu_item.name,
                'quantity': quantity,
                'unit_price': unit_price,
                'total_price': item_total,
                'item_snapshot': menu_item.to_snapshot(),
                'selected_modifiers': selected_modifiers,
                'special_instructions': item_data.get('special_instructions')
            })

        # Calculate fees
        delivery_fee = Decimal('0.00')
        if order_type == "delivery":
            # Wrap in Decimal — SQLAlchemy may return Numeric columns as float,
            # mixing float with Decimal in arithmetic raises TypeError.
            delivery_fee = Decimal(str(restaurant.delivery_fee or 0))

        # FIX: Blueprint §4.4 — ₦50 flat fee ONLY. No percentage service charge.
        # The old 5% service_charge stacked on top of the ₦50 platform fee was
        # undocumented and not specified in the blueprint. Removed entirely.
        # service_charge is kept as Decimal("0.00") so the DB column is populated.
        service_charge = Decimal('0.00')

        # BLUEPRINT v2.0: Platform fee — ₦50 flat fee on all food orders
        platform_fee = Decimal('50.00')

        tax = Decimal('0.00')
        discount = Decimal('0.00')
        # Ensure tip is Decimal — schema allows float passthrough from JSON
        tip = Decimal(str(tip or 0))
        total_amount = subtotal + delivery_fee + platform_fee + tax + tip - discount

        # Create order
        order_data = {
            'restaurant_id': restaurant_id,
            'customer_id': customer_id,
            'order_type': order_type,
            'customer_name': customer_name,
            'customer_phone': customer_phone,
            'subtotal': subtotal,
            'delivery_fee': delivery_fee,
            'service_charge': service_charge,
            'platform_fee': platform_fee,
            'tax': tax,
            'discount': discount,
            'tip': tip,
            'total_amount': total_amount,
            'payment_method': payment_method,
            'special_instructions': special_instructions,
            'scheduled_delivery_time': scheduled_delivery_time,
            'group_order_id': group_order_id,
            'is_group_order_host': is_group_order_host,
            'estimated_preparation_time': restaurant.average_preparation_time_minutes
        }

        if order_type == "delivery":
            order_data['delivery_address'] = delivery_address
            order_data['delivery_instructions'] = delivery_instructions

            if delivery_location:
                from geoalchemy2.elements import WKTElement
                lat, lng = delivery_location
                order_data['delivery_location'] = WKTElement(
                    f"POINT({lng} {lat})",
                    srid=4326
                )

        order = FoodOrder(**order_data)
        db.add(order)
        db.flush()

        # Create order items and update popularity
        for item_data in order_items_data:
            order_item = FoodOrderItem(
                order_id=order.id,
                **item_data
            )
            db.add(order_item)

            # Update menu item popularity score
            menu_item = menu_item_crud.get(db, id=item_data['menu_item_id'])
            if menu_item:
                menu_item.popularity_score += item_data['quantity']

        db.commit()
        db.refresh(order)

        return order

    def get_customer_orders(
            self,
            db: Session,
            *,
            customer_id: UUID,
            skip: int = 0,
            limit: int = 20
    ) -> List[FoodOrder]:
        """Get customer food orders"""
        return db.query(FoodOrder).options(
            joinedload(FoodOrder.items)
        ).filter(
            FoodOrder.customer_id == customer_id
        ).order_by(
            FoodOrder.created_at.desc()
        ).offset(skip).limit(limit).all()

    def get_restaurant_orders(
            self,
            db: Session,
            *,
            restaurant_id: UUID,
            status: Optional[str] = None,
            skip: int = 0,
            limit: int = 50
    ) -> List[FoodOrder]:
        """Get restaurant orders"""
        query = db.query(FoodOrder).options(
            joinedload(FoodOrder.items)
        ).filter(
            FoodOrder.restaurant_id == restaurant_id
        )

        if status:
            query = query.filter(FoodOrder.order_status == status)

        return query.order_by(
            FoodOrder.created_at.desc()
        ).offset(skip).limit(limit).all()

    def update_order_status(
            self,
            db: Session,
            *,
            order_id: UUID,
            new_status: str
    ) -> FoodOrder:
        """Update order status with timestamp tracking"""
        order = self.get(db, id=order_id)
        if not order:
            raise NotFoundException("Order")

        order.order_status = new_status

        # Update timestamps
        if new_status == OrderStatusEnum.CONFIRMED:
            order.confirmed_at = datetime.now(timezone.utc)
        elif new_status == OrderStatusEnum.PREPARING:
            order.prepared_at = datetime.now(timezone.utc)
        elif new_status == OrderStatusEnum.DELIVERED:
            order.delivered_at = datetime.now(timezone.utc)

            # Update restaurant stats
            restaurant = restaurant_crud.get(db, id=order.restaurant_id)
            if restaurant:
                restaurant.total_orders += 1

        db.commit()
        db.refresh(order)

        return order

    def cancel_order(
            self,
            db: Session,
            *,
            order_id: UUID,
            customer_id: UUID,
            reason: Optional[str] = None
    ) -> FoodOrder:
        """Cancel a customer's food order with ownership check and status guard"""
        from app.core.exceptions import PermissionDeniedException

        order = self.get(db, id=order_id)
        if not order:
            raise NotFoundException("Order")
        if order.customer_id != customer_id:
            raise PermissionDeniedException()
        if order.order_status not in [
            OrderStatusEnum.PENDING, OrderStatusEnum.CONFIRMED
        ]:
            raise ValidationException(
                "Orders can only be cancelled when pending or confirmed"
            )

        order.order_status = OrderStatusEnum.CANCELLED
        order.cancellation_reason = reason
        order.cancelled_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(order)
        return order


class CRUDCookingService(CRUDBase[CookingService, dict, dict]):
    """CRUD for CookingService"""

    def get_by_restaurant(
            self,
            db: Session,
            *,
            restaurant_id: UUID,
            active_only: bool = True
    ) -> List[CookingService]:
        """Get cooking services for restaurant"""
        query = db.query(CookingService).filter(
            CookingService.restaurant_id == restaurant_id
        )

        if active_only:
            query = query.filter(CookingService.is_active == True)

        return query.all()


class CRUDCookingBooking(CRUDBase[CookingBooking, dict, dict]):
    """CRUD for CookingBooking"""

    def create_booking(
            self,
            db: Session,
            *,
            service_id: UUID,
            customer_id: UUID,
            event_date: date,
            event_time: time,
            number_of_guests: int,
            event_address: str,
            event_location: Optional[tuple] = None,
            menu_requirements: Optional[str] = None,
            dietary_restrictions: Optional[List[str]] = None,
            special_requests: Optional[str] = None
    ) -> CookingBooking:
        """
        Create cooking service booking.

        BLUEPRINT v2.0 COMPLIANCE:
        - Platform fee: ₦100 flat fee on bookings (services, health, hotels)
        """
        service = cooking_service_crud.get(db, id=service_id)
        if not service:
            raise NotFoundException("CookingService")

        # Calculate pricing
        base_price = service.base_price
        total_price = base_price

        if service.price_per_person:
            total_price = base_price + (service.price_per_person * number_of_guests)

        # BLUEPRINT v2.0: Platform fee — ₦100 for bookings
        platform_fee = Decimal('100.00')
        total_price += platform_fee

        booking_data = {
            'service_id': service_id,
            'customer_id': customer_id,
            'event_date': event_date,
            'event_time': event_time,
            'number_of_guests': number_of_guests,
            'event_address': event_address,
            'base_price': base_price,
            'total_price': total_price,
            'platform_fee': platform_fee,
            'menu_requirements': menu_requirements,
            'dietary_restrictions': dietary_restrictions or [],
            'special_requests': special_requests
        }

        if event_location:
            from geoalchemy2.elements import WKTElement
            lat, lng = event_location
            booking_data['event_location'] = WKTElement(
                f"POINT({lng} {lat})",
                srid=4326
            )

        booking = CookingBooking(**booking_data)
        db.add(booking)
        db.commit()
        db.refresh(booking)

        return booking


# Singleton instances
restaurant_crud = CRUDRestaurant(Restaurant)
menu_category_crud = CRUDMenuCategory(MenuCategory)
menu_item_crud = CRUDMenuItem(MenuItem)
table_reservation_crud = CRUDTableReservation(TableReservation)
food_order_crud = CRUDFoodOrder(FoodOrder)
cooking_service_crud = CRUDCookingService(CookingService)
cooking_booking_crud = CRUDCookingBooking(CookingBooking)