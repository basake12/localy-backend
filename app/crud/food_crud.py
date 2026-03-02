from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from uuid import UUID
from datetime import datetime, date, time
from decimal import Decimal
import random
import string

from app.crud.base_crud import CRUDBase
from app.models.food_model import (
    Restaurant, MenuCategory, MenuItem,
    TableReservation, FoodOrder, FoodOrderItem,
    OrderStatusEnum
)
from app.models.business_model import Business
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    OutOfStockException
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
            radius_km: float = 10.0,
            price_range: Optional[str] = None,
            offers_delivery: Optional[bool] = None,
            min_rating: Optional[Decimal] = None,
            skip: int = 0,
            limit: int = 20
    ) -> List[Restaurant]:
        """Search restaurants with filters"""
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

        # Location filter
        if location:
            lat, lng = location
            query = query.filter(
                func.ST_DWithin(
                    Business.location,
                    func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326),
                    radius_km * 1000
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
            max_price: Optional[Decimal] = None
    ) -> List[MenuItem]:
        """Search menu items"""
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

        if max_price:
            query = query.filter(MenuItem.price <= max_price)

        return query.all()


class CRUDTableReservation(CRUDBase[TableReservation, dict, dict]):
    """CRUD for TableReservation"""

    def _generate_confirmation_code(self, db: Session) -> str:
        """Generate unique confirmation code"""
        while True:
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

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
            occasion: Optional[str] = None
    ) -> TableReservation:
        """Create a table reservation"""
        # Generate confirmation code
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
            confirmation_code=confirmation_code
        )

        db.add(reservation)
        db.commit()
        db.refresh(reservation)

        return reservation

    def get_customer_reservations(
            self,
            db: Session,
            *,
            customer_id: UUID,
            skip: int = 0,
            limit: int = 20
    ) -> List[TableReservation]:
        """Get customer reservations"""
        return db.query(TableReservation).filter(
            TableReservation.customer_id == customer_id
        ).order_by(
            TableReservation.reservation_date.desc(),
            TableReservation.reservation_time.desc()
        ).offset(skip).limit(limit).all()

    def get_restaurant_reservations(
            self,
            db: Session,
            *,
            restaurant_id: UUID,
            reservation_date: Optional[date] = None,
            status: Optional[str] = None,
            skip: int = 0,
            limit: int = 50
    ) -> List[TableReservation]:
        """Get restaurant reservations"""
        query = db.query(TableReservation).filter(
            TableReservation.restaurant_id == restaurant_id
        )

        if reservation_date:
            query = query.filter(TableReservation.reservation_date == reservation_date)

        if status:
            query = query.filter(TableReservation.status == status)

        return query.order_by(
            TableReservation.reservation_date,
            TableReservation.reservation_time
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
            special_instructions: Optional[str] = None,
            payment_method: str = "wallet",
            tip: Decimal = Decimal('0.00')
    ) -> FoodOrder:
        """Create a food order"""
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
                'item_snapshot': menu_item.__dict__,
                'selected_modifiers': selected_modifiers,
                'special_instructions': item_data.get('special_instructions')
            })

        # Calculate fees
        delivery_fee = Decimal('0.00')
        if order_type == "delivery":
            delivery_fee = restaurant.delivery_fee

        service_charge = subtotal * Decimal('0.05')  # 5% service charge
        tax = Decimal('0.00')
        discount = Decimal('0.00')
        total_amount = subtotal + delivery_fee + service_charge + tax + tip - discount

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
            'tax': tax,
            'discount': discount,
            'tip': tip,
            'total_amount': total_amount,
            'payment_method': payment_method,
            'special_instructions': special_instructions,
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

        # Create order items
        for item_data in order_items_data:
            order_item = FoodOrderItem(
                order_id=order.id,
                **item_data
            )
            db.add(order_item)

            # Update menu item popularity
            menu_item = menu_item_crud.get(db, id=item_data['menu_item_id'])
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
        query = db.query(FoodOrder).filter(
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
        """Update order status"""
        order = self.get(db, id=order_id)
        if not order:
            raise NotFoundException("Order")

        order.order_status = new_status

        # Update timestamps
        if new_status == OrderStatusEnum.CONFIRMED:
            order.confirmed_at = datetime.utcnow()
        elif new_status == OrderStatusEnum.PREPARED:
            order.prepared_at = datetime.utcnow()
        elif new_status == OrderStatusEnum.DELIVERED:
            order.delivered_at = datetime.utcnow()

            # Update restaurant stats
            restaurant = restaurant_crud.get(db, id=order.restaurant_id)
            restaurant.total_orders += 1

        db.commit()
        db.refresh(order)

        return order


# Singleton instances
restaurant_crud = CRUDRestaurant(Restaurant)
menu_category_crud = CRUDMenuCategory(MenuCategory)
menu_item_crud = CRUDMenuItem(MenuItem)
table_reservation_crud = CRUDTableReservation(TableReservation)
food_order_crud = CRUDFoodOrder(FoodOrder)