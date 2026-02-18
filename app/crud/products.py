from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from uuid import UUID
from decimal import Decimal

from app.crud.base import CRUDBase
from app.models.products import (
    ProductVendor, Product, ProductVariant,
    ProductOrder, OrderItem, CartItem, Wishlist
)
from app.models.business import Business
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    OutOfStockException,
    InsufficientBalanceException
)


class CRUDProductVendor(CRUDBase[ProductVendor, dict, dict]):
    """CRUD for ProductVendor"""

    def get_by_business_id(self, db: Session, *, business_id: UUID) -> Optional[ProductVendor]:
        """Get vendor by business ID"""
        return db.query(ProductVendor).filter(
            ProductVendor.business_id == business_id
        ).first()


class CRUDProduct(CRUDBase[Product, dict, dict]):
    """CRUD for Product"""

    def get_by_vendor(
            self,
            db: Session,
            *,
            vendor_id: UUID,
            skip: int = 0,
            limit: int = 50,
            active_only: bool = True
    ) -> List[Product]:
        """Get products by vendor"""
        query = db.query(Product).filter(Product.vendor_id == vendor_id)

        if active_only:
            query = query.filter(Product.is_active == True)

        return query.offset(skip).limit(limit).all()

    def search_products(
            self,
            db: Session,
            *,
            query_text: Optional[str] = None,
            category: Optional[str] = None,
            subcategory: Optional[str] = None,
            brand: Optional[str] = None,
            min_price: Optional[Decimal] = None,
            max_price: Optional[Decimal] = None,
            in_stock_only: bool = False,
            location: Optional[tuple] = None,
            radius_km: float = 10.0,
            sort_by: str = "created_at",
            skip: int = 0,
            limit: int = 20
    ) -> List[Product]:
        """
        Search products with filters
        """
        query = db.query(Product).filter(Product.is_active == True)

        # Text search
        if query_text:
            search_filter = or_(
                Product.name.ilike(f"%{query_text}%"),
                Product.description.ilike(f"%{query_text}%"),
                Product.brand.ilike(f"%{query_text}%")
            )
            query = query.filter(search_filter)

        # Category filters
        if category:
            query = query.filter(Product.category == category)

        if subcategory:
            query = query.filter(Product.subcategory == subcategory)

        if brand:
            query = query.filter(Product.brand == brand)

        # Price range
        if min_price:
            query = query.filter(
                or_(
                    Product.sale_price >= min_price,
                    and_(Product.sale_price == None, Product.base_price >= min_price)
                )
            )

        if max_price:
            query = query.filter(
                or_(
                    Product.sale_price <= max_price,
                    and_(Product.sale_price == None, Product.base_price <= max_price)
                )
            )

        # Stock filter
        if in_stock_only:
            query = query.filter(Product.stock_quantity > 0)

        # Location filter (via vendor's business)
        if location:
            lat, lng = location
            query = query.join(ProductVendor).join(Business).filter(
                func.ST_DWithin(
                    Business.location,
                    func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326),
                    radius_km * 1000
                )
            )

        # Sorting
        if sort_by == "price_asc":
            query = query.order_by(
                func.coalesce(Product.sale_price, Product.base_price).asc()
            )
        elif sort_by == "price_desc":
            query = query.order_by(
                func.coalesce(Product.sale_price, Product.base_price).desc()
            )
        elif sort_by == "popular":
            query = query.order_by(Product.sales_count.desc())
        elif sort_by == "rating":
            query = query.order_by(Product.average_rating.desc())
        else:
            query = query.order_by(Product.created_at.desc())

        return query.offset(skip).limit(limit).all()

    def increment_views(self, db: Session, *, product_id: UUID) -> None:
        """Increment product views"""
        db.query(Product).filter(Product.id == product_id).update(
            {Product.views_count: Product.views_count + 1}
        )
        db.commit()

    def check_stock(
            self,
            db: Session,
            *,
            product_id: UUID,
            variant_id: Optional[UUID] = None,
            quantity: int = 1
    ) -> bool:
        """Check if product/variant has sufficient stock"""
        if variant_id:
            variant = db.query(ProductVariant).filter(
                ProductVariant.id == variant_id
            ).first()
            return variant and variant.stock_quantity >= quantity
        else:
            product = self.get(db, id=product_id)
            return product and product.stock_quantity >= quantity

    def reduce_stock(
            self,
            db: Session,
            *,
            product_id: UUID,
            variant_id: Optional[UUID] = None,
            quantity: int
    ) -> None:
        """Reduce product stock after order"""
        if variant_id:
            db.query(ProductVariant).filter(
                ProductVariant.id == variant_id
            ).update({
                ProductVariant.stock_quantity: ProductVariant.stock_quantity - quantity
            })
        else:
            db.query(Product).filter(Product.id == product_id).update({
                Product.stock_quantity: Product.stock_quantity - quantity
            })
        db.commit()


class CRUDProductVariant(CRUDBase[ProductVariant, dict, dict]):
    """CRUD for ProductVariant"""

    def get_by_product(
            self,
            db: Session,
            *,
            product_id: UUID
    ) -> List[ProductVariant]:
        """Get all variants for a product"""
        return db.query(ProductVariant).filter(
            ProductVariant.product_id == product_id,
            ProductVariant.is_active == True
        ).all()


class CRUDCart(CRUDBase[CartItem, dict, dict]):
    """CRUD for Shopping Cart"""

    def get_user_cart(
            self,
            db: Session,
            *,
            customer_id: UUID
    ) -> List[CartItem]:
        """Get user's cart items"""
        return db.query(CartItem).options(
            joinedload(CartItem.product),
            joinedload(CartItem.variant)
        ).filter(
            CartItem.customer_id == customer_id
        ).all()

    def add_to_cart(
            self,
            db: Session,
            *,
            customer_id: UUID,
            product_id: UUID,
            variant_id: Optional[UUID] = None,
            quantity: int = 1
    ) -> CartItem:
        """Add item to cart or update quantity"""
        # Check if item exists
        existing_item = db.query(CartItem).filter(
            and_(
                CartItem.customer_id == customer_id,
                CartItem.product_id == product_id,
                CartItem.variant_id == variant_id
            )
        ).first()

        if existing_item:
            existing_item.quantity += quantity
            db.commit()
            db.refresh(existing_item)
            return existing_item
        else:
            cart_item = CartItem(
                customer_id=customer_id,
                product_id=product_id,
                variant_id=variant_id,
                quantity=quantity
            )
            db.add(cart_item)
            db.commit()
            db.refresh(cart_item)
            return cart_item

    def update_quantity(
            self,
            db: Session,
            *,
            cart_item_id: UUID,
            quantity: int
    ) -> CartItem:
        """Update cart item quantity"""
        cart_item = self.get(db, id=cart_item_id)
        if not cart_item:
            raise NotFoundException("Cart item")

        cart_item.quantity = quantity
        db.commit()
        db.refresh(cart_item)
        return cart_item

    def clear_cart(self, db: Session, *, customer_id: UUID) -> None:
        """Clear user's cart"""
        db.query(CartItem).filter(
            CartItem.customer_id == customer_id
        ).delete()
        db.commit()


class CRUDProductOrder(CRUDBase[ProductOrder, dict, dict]):
    """CRUD for ProductOrder"""

    def create_order(
            self,
            db: Session,
            *,
            customer_id: UUID,
            items: List[Dict[str, Any]],
            shipping_address: str,
            recipient_name: str,
            recipient_phone: str,
            payment_method: str,
            notes: Optional[str] = None
    ) -> ProductOrder:
        """
        Create order from cart items

        Args:
            items: List of {product_id, variant_id, quantity}
        """
        # Validate all items and calculate total
        subtotal = Decimal('0.00')
        order_items_data = []

        for item_data in items:
            product_id = item_data['product_id']
            variant_id = item_data.get('variant_id')
            quantity = item_data['quantity']

            # Get product
            product = product_crud.get(db, id=product_id)
            if not product or not product.is_active:
                raise NotFoundException(f"Product {product_id}")

            # Check stock
            if not product_crud.check_stock(
                    db, product_id=product_id, variant_id=variant_id, quantity=quantity
            ):
                raise OutOfStockException(product.name)

            # Get price
            if variant_id:
                variant = db.query(ProductVariant).get(variant_id)
                unit_price = variant.price
                product_snapshot = {
                    **product.__dict__,
                    'variant': variant.__dict__
                }
            else:
                unit_price = product.sale_price or product.base_price
                product_snapshot = product.__dict__

            item_total = unit_price * quantity
            subtotal += item_total

            order_items_data.append({
                'product_id': product_id,
                'variant_id': variant_id,
                'vendor_id': product.vendor_id,
                'quantity': quantity,
                'unit_price': unit_price,
                'total_price': item_total,
                'product_snapshot': product_snapshot
            })

        # Calculate fees
        shipping_fee = Decimal('2000.00')  # TODO: Calculate based on location
        tax = Decimal('0.00')
        discount = Decimal('0.00')
        total_amount = subtotal + shipping_fee + tax - discount

        # Create order
        order = ProductOrder(
            customer_id=customer_id,
            shipping_address=shipping_address,
            recipient_name=recipient_name,
            recipient_phone=recipient_phone,
            subtotal=subtotal,
            shipping_fee=shipping_fee,
            tax=tax,
            discount=discount,
            total_amount=total_amount,
            payment_method=payment_method,
            notes=notes
        )

        db.add(order)
        db.flush()

        # Create order items
        for item_data in order_items_data:
            order_item = OrderItem(
                order_id=order.id,
                **item_data
            )
            db.add(order_item)

            # Reduce stock
            product_crud.reduce_stock(
                db,
                product_id=item_data['product_id'],
                variant_id=item_data.get('variant_id'),
                quantity=item_data['quantity']
            )

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
    ) -> List[ProductOrder]:
        """Get customer orders"""
        return db.query(ProductOrder).options(
            joinedload(ProductOrder.items)
        ).filter(
            ProductOrder.customer_id == customer_id
        ).order_by(
            ProductOrder.created_at.desc()
        ).offset(skip).limit(limit).all()

    def get_vendor_orders(
            self,
            db: Session,
            *,
            vendor_id: UUID,
            skip: int = 0,
            limit: int = 50
    ) -> List[ProductOrder]:
        """Get orders containing vendor's products"""
        return db.query(ProductOrder).join(OrderItem).filter(
            OrderItem.vendor_id == vendor_id
        ).distinct().order_by(
            ProductOrder.created_at.desc()
        ).offset(skip).limit(limit).all()


# Singleton instances
product_vendor_crud = CRUDProductVendor(ProductVendor)
product_crud = CRUDProduct(Product)
product_variant_crud = CRUDProductVariant(ProductVariant)
cart_crud = CRUDCart(CartItem)
product_order_crud = CRUDProductOrder(ProductOrder)