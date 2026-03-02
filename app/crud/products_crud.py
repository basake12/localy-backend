from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func, desc
from uuid import UUID
from decimal import Decimal

from app.crud.base_crud import CRUDBase
from app.models.products_model import (
    ProductVendor, Product, ProductVariant,
    ProductOrder, OrderItem, CartItem, Wishlist
)
from app.models.business_model import Business
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    OutOfStockException,
)


class CRUDProductVendor(CRUDBase[ProductVendor, dict, dict]):

    def get_by_business_id(self, db: Session, *, business_id: UUID) -> Optional[ProductVendor]:
        return db.query(ProductVendor).filter(
            ProductVendor.business_id == business_id
        ).first()


class CRUDProduct(CRUDBase[Product, dict, dict]):

    def get_by_vendor(
            self, db: Session, *, vendor_id: UUID,
            skip: int = 0, limit: int = 50, active_only: bool = True
    ) -> List[Product]:
        query = db.query(Product).filter(Product.vendor_id == vendor_id)
        if active_only:
            query = query.filter(Product.is_active == True)
        return query.offset(skip).limit(limit).all()

    def search_products(
            self, db: Session, *,
            query_text: Optional[str] = None,
            category: Optional[str] = None,
            subcategory: Optional[str] = None,
            brand: Optional[str] = None,
            min_price: Optional[Decimal] = None,
            max_price: Optional[Decimal] = None,
            in_stock_only: bool = False,
            location: Optional[Tuple[float, float]] = None,
            radius_km: float = 10.0,
            sort_by: str = "created_at",
            skip: int = 0,
            limit: int = 20
    ) -> List[Product]:
        # FIX: Use joinedload to prevent N+1 queries — loads vendor + business in 1 query
        query = db.query(Product).options(
            joinedload(Product.vendor).joinedload(ProductVendor.business),
            joinedload(Product.variants)
        ).filter(Product.is_active == True)

        if query_text:
            query = query.filter(or_(
                Product.name.ilike(f"%{query_text}%"),
                Product.description.ilike(f"%{query_text}%"),
                Product.brand.ilike(f"%{query_text}%")
            ))

        if category:
            query = query.filter(Product.category == category)
        if subcategory:
            query = query.filter(Product.subcategory == subcategory)
        if brand:
            query = query.filter(Product.brand == brand)

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

        if in_stock_only:
            query = query.filter(Product.stock_quantity > 0)

        if location:
            lat, lng = location
            query = query.join(ProductVendor).join(Business).filter(
                func.ST_DWithin(
                    Business.location,
                    func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326),
                    radius_km * 1000
                )
            )

        if sort_by == "price_asc":
            query = query.order_by(func.coalesce(Product.sale_price, Product.base_price).asc())
        elif sort_by == "price_desc":
            query = query.order_by(func.coalesce(Product.sale_price, Product.base_price).desc())
        elif sort_by == "popular":
            query = query.order_by(Product.sales_count.desc())
        elif sort_by == "rating":
            query = query.order_by(Product.average_rating.desc())
        else:
            query = query.order_by(Product.created_at.desc())

        return query.offset(skip).limit(limit).all()

    def get_with_relations(self, db: Session, *, product_id: UUID) -> Optional[Product]:
        """Get product with all relationships preloaded — for detail page."""
        return db.query(Product).options(
            joinedload(Product.vendor).joinedload(ProductVendor.business),
            joinedload(Product.variants)
        ).filter(Product.id == product_id, Product.is_active == True).first()

    def increment_views(self, db: Session, *, product_id: UUID) -> None:
        db.query(Product).filter(Product.id == product_id).update(
            {Product.views_count: Product.views_count + 1}
        )
        db.commit()

    def check_stock(
            self, db: Session, *,
            product_id: UUID,
            variant_id: Optional[UUID] = None,
            quantity: int = 1
    ) -> bool:
        if variant_id:
            variant = db.query(ProductVariant).filter(
                ProductVariant.id == variant_id
            ).first()
            return variant is not None and variant.stock_quantity >= quantity
        else:
            product = self.get(db, id=product_id)
            return product is not None and product.stock_quantity >= quantity

    def reduce_stock(
            self, db: Session, *,
            product_id: UUID,
            variant_id: Optional[UUID] = None,
            quantity: int
    ) -> None:
        """Reduce stock. Caller is responsible for commit."""
        if variant_id:
            db.query(ProductVariant).filter(
                ProductVariant.id == variant_id
            ).update({ProductVariant.stock_quantity: ProductVariant.stock_quantity - quantity})
        else:
            db.query(Product).filter(Product.id == product_id).update(
                {Product.stock_quantity: Product.stock_quantity - quantity}
            )
        # FIX: Removed db.commit() — caller owns the transaction

    # FIX: Added restore_stock() — required by safe checkout flow
    def restore_stock(
            self, db: Session, *,
            product_id: UUID,
            variant_id: Optional[UUID] = None,
            quantity: int
    ) -> None:
        """Restore stock on order cancellation or payment failure. Caller commits."""
        if variant_id:
            db.query(ProductVariant).filter(
                ProductVariant.id == variant_id
            ).update({ProductVariant.stock_quantity: ProductVariant.stock_quantity + quantity})
        else:
            db.query(Product).filter(Product.id == product_id).update(
                {Product.stock_quantity: Product.stock_quantity + quantity}
            )

    def get_low_stock(
            self, db: Session, *, vendor_id: UUID
    ) -> List[Product]:
        """Products at or below low_stock_threshold for dashboard alerts."""
        return db.query(Product).filter(
            Product.vendor_id == vendor_id,
            Product.is_active == True,
            Product.stock_quantity <= Product.low_stock_threshold
        ).order_by(Product.stock_quantity.asc()).all()

    def get_vendor_analytics(
            self, db: Session, *, vendor_id: UUID
    ) -> Dict[str, Any]:
        """Aggregate analytics for vendor dashboard KPI cards."""
        total_products = db.query(func.count(Product.id)).filter(
            Product.vendor_id == vendor_id
        ).scalar() or 0

        active_products = db.query(func.count(Product.id)).filter(
            Product.vendor_id == vendor_id,
            Product.is_active == True
        ).scalar() or 0

        low_stock_count = db.query(func.count(Product.id)).filter(
            Product.vendor_id == vendor_id,
            Product.is_active == True,
            Product.stock_quantity <= Product.low_stock_threshold
        ).scalar() or 0

        # Revenue + order count from order items
        revenue_result = db.query(
            func.sum(OrderItem.total_price),
            func.count(func.distinct(OrderItem.order_id))
        ).filter(OrderItem.vendor_id == vendor_id).first()

        total_revenue = revenue_result[0] or Decimal('0.00')
        total_orders = revenue_result[1] or 0

        # Top 5 products by sales
        top_products = db.query(
            Product.id,
            Product.name,
            Product.sales_count,
            func.sum(OrderItem.total_price).label('revenue')
        ).join(OrderItem, OrderItem.product_id == Product.id).filter(
            Product.vendor_id == vendor_id
        ).group_by(Product.id, Product.name, Product.sales_count).order_by(
            desc('revenue')
        ).limit(5).all()

        return {
            "total_revenue": total_revenue,
            "total_orders": total_orders,
            "total_products": total_products,
            "active_products": active_products,
            "low_stock_count": low_stock_count,
            "top_products": [
                {
                    "product_id": row.id,
                    "name": row.name,
                    "sales_count": row.sales_count,
                    "revenue": row.revenue or Decimal('0.00')
                }
                for row in top_products
            ]
        }


class CRUDProductVariant(CRUDBase[ProductVariant, dict, dict]):

    def get_by_product(self, db: Session, *, product_id: UUID) -> List[ProductVariant]:
        return db.query(ProductVariant).filter(
            ProductVariant.product_id == product_id,
            ProductVariant.is_active == True
        ).all()


class CRUDCart(CRUDBase[CartItem, dict, dict]):

    def get_user_cart(self, db: Session, *, customer_id: UUID) -> List[CartItem]:
        return db.query(CartItem).options(
            joinedload(CartItem.product).joinedload(Product.vendor),
            joinedload(CartItem.variant)
        ).filter(CartItem.customer_id == customer_id).all()

    def add_to_cart(
            self, db: Session, *,
            customer_id: UUID, product_id: UUID,
            variant_id: Optional[UUID] = None, quantity: int = 1
    ) -> CartItem:
        existing = db.query(CartItem).filter(and_(
            CartItem.customer_id == customer_id,
            CartItem.product_id == product_id,
            CartItem.variant_id == variant_id
        )).first()

        if existing:
            existing.quantity += quantity
            db.commit()
            db.refresh(existing)
            return existing

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

    def update_quantity(self, db: Session, *, cart_item_id: UUID, quantity: int) -> CartItem:
        cart_item = self.get(db, id=cart_item_id)
        if not cart_item:
            raise NotFoundException("Cart item")
        cart_item.quantity = quantity
        db.commit()
        db.refresh(cart_item)
        return cart_item

    def clear_cart(self, db: Session, *, customer_id: UUID) -> None:
        db.query(CartItem).filter(CartItem.customer_id == customer_id).delete()
        db.commit()


class CRUDProductOrder(CRUDBase[ProductOrder, dict, dict]):

    def create_order(
            self, db: Session, *,
            customer_id: UUID,
            items: List[Dict[str, Any]],
            shipping_address: str,
            recipient_name: str,
            recipient_phone: str,
            payment_method: str,
            coupon_code: Optional[str] = None,
            notes: Optional[str] = None
    ) -> ProductOrder:
        subtotal = Decimal('0.00')
        order_items_data = []

        for item_data in items:
            product_id = item_data['product_id']
            variant_id = item_data.get('variant_id')
            quantity = item_data['quantity']

            product = product_crud.get(db, id=product_id)
            if not product or not product.is_active:
                raise NotFoundException(f"Product {product_id}")

            if not product_crud.check_stock(
                    db, product_id=product_id, variant_id=variant_id, quantity=quantity):
                raise OutOfStockException(product.name)

            if variant_id:
                variant = db.query(ProductVariant).filter(
                    ProductVariant.id == variant_id
                ).first()
                if not variant:
                    raise NotFoundException(f"Variant {variant_id}")
                unit_price = variant.price
                # FIX: Safe product_snapshot using explicit dict (not __dict__ which crashes)
                product_snapshot = {
                    "id": str(product.id),
                    "name": product.name,
                    "category": product.category,
                    "brand": product.brand,
                    "base_price": str(product.base_price),
                    "effective_price": str(unit_price),
                    "images": product.images[:1] if product.images else [],
                    "variant_attributes": variant.attributes,
                    "variant_sku": variant.sku,
                }
            else:
                unit_price = product.sale_price or product.base_price
                product_snapshot = {
                    "id": str(product.id),
                    "name": product.name,
                    "category": product.category,
                    "brand": product.brand,
                    "base_price": str(product.base_price),
                    "effective_price": str(unit_price),
                    "images": product.images[:1] if product.images else [],
                    "sku": product.sku,
                }

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

        shipping_fee = Decimal('2000.00')  # TODO: calculate from distance
        tax = Decimal('0.00')
        discount = Decimal('0.00')
        total_amount = subtotal + shipping_fee + tax - discount

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
            coupon_code=coupon_code,
            notes=notes
        )
        db.add(order)
        db.flush()  # get order.id without committing

        for item_data in order_items_data:
            order_item = OrderItem(order_id=order.id, **item_data)
            db.add(order_item)
            # FIX: reduce_stock no longer commits — all part of the same transaction
            product_crud.reduce_stock(
                db,
                product_id=item_data['product_id'],
                variant_id=item_data.get('variant_id'),
                quantity=item_data['quantity']
            )

        # FIX: Single commit for entire order creation — atomic
        db.commit()
        db.refresh(order)
        return order

    def get_customer_orders(
            self, db: Session, *,
            customer_id: UUID, skip: int = 0, limit: int = 20
    ) -> List[ProductOrder]:
        return db.query(ProductOrder).options(
            joinedload(ProductOrder.items)
        ).filter(
            ProductOrder.customer_id == customer_id
        ).order_by(ProductOrder.created_at.desc()).offset(skip).limit(limit).all()

    def get_vendor_orders(
            self, db: Session, *,
            vendor_id: UUID,
            status: Optional[str] = None,
            skip: int = 0, limit: int = 50
    ) -> List[ProductOrder]:
        query = db.query(ProductOrder).options(
            joinedload(ProductOrder.items)
        ).join(OrderItem).filter(OrderItem.vendor_id == vendor_id)

        if status:
            query = query.filter(ProductOrder.order_status == status)

        return query.distinct().order_by(
            ProductOrder.created_at.desc()
        ).offset(skip).limit(limit).all()

    def update_order_status(
            self, db: Session, *,
            order_id: UUID,
            new_status: str,
            tracking_number: Optional[str] = None,
            estimated_delivery=None
    ) -> ProductOrder:
        """Generic status update with optional tracking info."""
        order = self.get(db, id=order_id)
        if not order:
            raise NotFoundException("Order")

        order.order_status = new_status

        if tracking_number:
            order.tracking_number = tracking_number
        if estimated_delivery:
            order.estimated_delivery = estimated_delivery

        from datetime import datetime, timezone
        if new_status == "delivered":
            order.delivered_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(order)
        return order

    def cancel_order(
            self, db: Session, *, order_id: UUID, customer_id: Optional[UUID] = None
    ) -> ProductOrder:
        """Cancel order and restore stock."""
        order = self.get(db, id=order_id)
        if not order:
            raise NotFoundException("Order")

        if customer_id and order.customer_id != customer_id:
            from app.core.exceptions import PermissionDeniedException
            raise PermissionDeniedException()

        if order.order_status in ["delivered", "cancelled", "refunded"]:
            raise ValidationException(f"Cannot cancel order in '{order.order_status}' status")

        # Restore stock for all items
        for item in order.items:
            product_crud.restore_stock(
                db,
                product_id=item.product_id,
                variant_id=item.variant_id,
                quantity=item.quantity
            )

        order.order_status = "cancelled"
        db.commit()
        db.refresh(order)
        return order

    def get_by_tracking_number(
            self, db: Session, *, tracking_number: str
    ) -> Optional[ProductOrder]:
        return db.query(ProductOrder).filter(
            ProductOrder.tracking_number == tracking_number
        ).first()


class CRUDWishlist(CRUDBase[Wishlist, dict, dict]):

    def get_by_customer(self, db: Session, *, customer_id: UUID) -> List[Wishlist]:
        return db.query(Wishlist).options(
            joinedload(Wishlist.product)
        ).filter(Wishlist.customer_id == customer_id).all()

    def add(self, db: Session, *, customer_id: UUID, product_id: UUID) -> Wishlist:
        existing = db.query(Wishlist).filter(
            Wishlist.customer_id == customer_id,
            Wishlist.product_id == product_id
        ).first()
        if existing:
            return existing  # Already wishlisted — idempotent

        item = Wishlist(customer_id=customer_id, product_id=product_id)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def remove(self, db: Session, *, customer_id: UUID, product_id: UUID) -> bool:
        deleted = db.query(Wishlist).filter(
            Wishlist.customer_id == customer_id,
            Wishlist.product_id == product_id
        ).delete()
        db.commit()
        return deleted > 0

    def is_wishlisted(
            self, db: Session, *, customer_id: UUID, product_id: UUID
    ) -> bool:
        return db.query(Wishlist).filter(
            Wishlist.customer_id == customer_id,
            Wishlist.product_id == product_id
        ).first() is not None


# ─── Singletons ───
product_vendor_crud = CRUDProductVendor(ProductVendor)
product_crud = CRUDProduct(Product)
product_variant_crud = CRUDProductVariant(ProductVariant)
cart_crud = CRUDCart(CartItem)
product_order_crud = CRUDProductOrder(ProductOrder)
wishlist_crud = CRUDWishlist(Wishlist)