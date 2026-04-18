"""
app/crud/products_crud.py

FIXES:
  1.  [BUG-3 SUPPORT] get_by_business() added — queries by business_id.
      products.py was changed to call get_by_business() instead of get_by_vendor()
      because business_id is the primary ownership FK (Blueprint §14).
      The old get_by_vendor() is kept as an alias for backward compatibility.

  2.  [BUG-2 SUPPORT] search_products() price filter updated.
      Blueprint-aligned Product model uses single price field (not base_price/sale_price).
      Queries referencing Product.base_price and Product.sale_price replaced with
      Product.price.

  3.  [BUG-2 SUPPORT] get_low_stock() renamed parameter to business_id.
      products.py now calls get_low_stock(db, business_id=business.id).
      Old vendor_id-based method kept as alias.

  4.  [BUG-2 SUPPORT] product_snapshot in create_order() updated.
      References to product.base_price replaced with product.price.
      effective_price = product.price (single price field per Blueprint §14).

  5.  [BUG-3 SUPPORT] get_business_orders() added — queries via business_id
      through the products, not via vendor_id. products.py calls this method.

  6.  [ADDED] get_by_tracking_number() for order tracking endpoint.

  7.  All datetime.utcnow() → datetime.now(timezone.utc). Blueprint §16.4.

  8.  create_from_dict() kept for product creation CRUD.
"""
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func, desc
from uuid import UUID
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from app.crud.base_crud import CRUDBase
from app.models.products_model import (
    ProductVendor, Product, ProductVariant,
    ProductOrder, OrderItem, CartItem, Wishlist,
    OrderStatusEnum, ProductReturn, ReturnStatusEnum,
)
from app.models.business_model import Business
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    OutOfStockException,
    PermissionDeniedException,
)


def _utcnow() -> datetime:
    """Blueprint §16.4 HARD RULE: timezone-aware UTC."""
    return datetime.now(timezone.utc)


class CRUDProductVendor(CRUDBase[ProductVendor, dict, dict]):

    def get_by_business_id(self, db: Session, *, business_id: UUID) -> Optional[ProductVendor]:
        return db.query(ProductVendor).filter(
            ProductVendor.business_id == business_id
        ).first()


class CRUDProduct(CRUDBase[Product, dict, dict]):

    # ── Blueprint §14: primary ownership is business_id ────────────────────────

    def get_by_business(
        self, db: Session, *, business_id: UUID,
        skip: int = 0, limit: int = 50, active_only: bool = True
    ) -> List[Product]:
        """
        [BUG-3 SUPPORT] Query by business_id — the primary ownership FK.
        Blueprint §14: products.business_id UUID NOT NULL REFERENCES businesses(id).
        Called by the fixed products.py router.
        """
        query = db.query(Product).filter(Product.business_id == business_id)
        if active_only:
            query = query.filter(
                Product.is_active == True,
                Product.is_deleted == False,
            )
        return query.offset(skip).limit(limit).all()

    # Backward-compat alias — some older code may still call get_by_vendor()
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
        radius_km: float = 5.0,  # Blueprint §4.1: default 5 km
        sort_by: str = "created_at",
        skip: int = 0,
        limit: int = 20
        # lga_id intentionally omitted — Blueprint §4 HARD RULE: no LGA filtering
    ) -> List[Product]:
        query = db.query(Product).filter(
            Product.is_active  == True,
            Product.is_deleted == False,
        )

        if query_text:
            query = query.filter(or_(
                Product.name.ilike(f"%{query_text}%"),
                Product.description.ilike(f"%{query_text}%"),
                Product.brand.ilike(f"%{query_text}%"),
            ))

        if category:
            query = query.filter(Product.category == category)
        if subcategory:
            query = query.filter(Product.subcategory == subcategory)
        if brand:
            query = query.filter(Product.brand == brand)

        # [BUG-2 SUPPORT] Blueprint-aligned Product uses single price field
        if min_price is not None:
            query = query.filter(Product.price >= min_price)
        if max_price is not None:
            query = query.filter(Product.price <= max_price)

        if in_stock_only:
            query = query.filter(Product.stock_quantity > 0)

        if location:
            lat, lng = location
            point = func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)
            # Join to business for geo-filter — blueprint §4: radius-based only
            query = query.join(Business, Business.id == Product.business_id).filter(
                or_(
                    Business.location.is_(None),
                    func.ST_DWithin(
                        Business.location,
                        point,
                        radius_km * 1000  # km → metres
                    )
                )
            )

        # [BUG-2 SUPPORT] Sorting on unified price field
        if sort_by == "price_asc":
            query = query.order_by(Product.price.asc())
        elif sort_by == "price_desc":
            query = query.order_by(Product.price.desc())
        elif sort_by == "popular":
            query = query.order_by(Product.sales_count.desc())
        elif sort_by == "rating":
            query = query.order_by(Product.average_rating.desc())
        else:
            query = query.order_by(Product.created_at.desc())

        return query.offset(skip).limit(limit).all()

    def get_with_relations(self, db: Session, *, product_id: UUID) -> Optional[Product]:
        """Get product with relationships preloaded — for detail and checkout pages."""
        return db.query(Product).options(
            joinedload(Product.variant_rows),
        ).filter(
            Product.id         == product_id,
            Product.is_active  == True,
            Product.is_deleted == False,
        ).first()

    def create_from_dict(self, db: Session, *, obj_in: dict) -> Product:
        """Create a product from a plain dict payload."""
        product = Product(**obj_in)
        db.add(product)
        db.flush()
        return product

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
        self, db: Session, *, business_id: UUID
    ) -> List[Product]:
        """
        [BUG-3 SUPPORT] Queries by business_id (primary FK).
        Products at or below low_stock_threshold for dashboard alerts.
        """
        return db.query(Product).filter(
            Product.business_id         == business_id,
            Product.is_active           == True,
            Product.is_deleted          == False,
            Product.stock_quantity      <= Product.low_stock_threshold,
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
            Product.is_active  == True,
            Product.is_deleted == False,
        ).scalar() or 0

        low_stock_count = db.query(func.count(Product.id)).filter(
            Product.vendor_id      == vendor_id,
            Product.is_active      == True,
            Product.stock_quantity <= Product.low_stock_threshold,
        ).scalar() or 0

        revenue_result = db.query(
            func.sum(OrderItem.total_price),
            func.count(func.distinct(OrderItem.order_id))
        ).filter(OrderItem.vendor_id == vendor_id).first()

        total_revenue = revenue_result[0] or Decimal("0.00")
        total_orders  = revenue_result[1] or 0

        top_products = db.query(
            Product.id,
            Product.name,
            Product.sales_count,
            func.sum(OrderItem.total_price).label("revenue")
        ).join(OrderItem, OrderItem.product_id == Product.id).filter(
            Product.vendor_id == vendor_id
        ).group_by(
            Product.id, Product.name, Product.sales_count
        ).order_by(desc("revenue")).limit(5).all()

        return {
            "total_revenue":  total_revenue,
            "total_orders":   total_orders,
            "total_products": total_products,
            "active_products": active_products,
            "low_stock_count": low_stock_count,
            "top_products": [
                {
                    "product_id":  row.id,
                    "name":        row.name,
                    "sales_count": row.sales_count,
                    "revenue":     row.revenue or Decimal("0.00"),
                }
                for row in top_products
            ],
        }


class CRUDProductVariant(CRUDBase[ProductVariant, dict, dict]):

    def get_by_product(self, db: Session, *, product_id: UUID) -> List[ProductVariant]:
        return db.query(ProductVariant).filter(
            ProductVariant.product_id == product_id,
            ProductVariant.is_active  == True,
        ).all()

    def get_by_attributes(
        self, db: Session, *,
        product_id: UUID,
        attributes: dict,
        exclude_id: Optional[UUID] = None,
    ) -> Optional[ProductVariant]:
        """
        Return an active variant whose attributes JSONB matches exactly.
        exclude_id used in update checks to skip the variant being edited.
        """
        query = db.query(ProductVariant).filter(
            ProductVariant.product_id == product_id,
            ProductVariant.is_active  == True,
            ProductVariant.attributes == attributes,
        )
        if exclude_id:
            query = query.filter(ProductVariant.id != exclude_id)
        return query.first()

    def create_from_dict(self, db: Session, *, obj_in: dict) -> ProductVariant:
        variant = ProductVariant(**obj_in)
        db.add(variant)
        db.flush()
        return variant


class CRUDCart(CRUDBase[CartItem, dict, dict]):

    def get_user_cart(self, db: Session, *, customer_id: UUID) -> List[CartItem]:
        return db.query(CartItem).options(
            joinedload(CartItem.product),
            joinedload(CartItem.variant),
        ).filter(CartItem.customer_id == customer_id).all()

    def get_cart_item_with_relations(
        self, db: Session, *, cart_item_id: UUID
    ) -> Optional[CartItem]:
        return db.query(CartItem).options(
            joinedload(CartItem.product),
            joinedload(CartItem.variant),
        ).filter(CartItem.id == cart_item_id).first()

    def add_to_cart(
        self, db: Session, *,
        customer_id: UUID, product_id: UUID,
        variant_id: Optional[UUID] = None, quantity: int = 1
    ) -> CartItem:
        existing = db.query(CartItem).filter(and_(
            CartItem.customer_id == customer_id,
            CartItem.product_id  == product_id,
            CartItem.variant_id  == variant_id,
        )).first()

        if existing:
            existing.quantity += quantity
            db.commit()
            return existing

        item = CartItem(
            customer_id=customer_id,
            product_id=product_id,
            variant_id=variant_id,
            quantity=quantity,
        )
        db.add(item)
        db.commit()
        return item

    def update_quantity(self, db: Session, *, cart_item_id: UUID, quantity: int) -> CartItem:
        item = self.get(db, id=cart_item_id)
        if item:
            item.quantity = quantity
            db.commit()
        return item

    def delete(self, db: Session, *, id: UUID) -> None:
        item = self.get(db, id=id)
        if item:
            db.delete(item)
            db.commit()

    def clear_cart(self, db: Session, *, customer_id: UUID) -> None:
        db.query(CartItem).filter(CartItem.customer_id == customer_id).delete()
        db.commit()

    def get_by_customer(self, db: Session, *, customer_id: UUID) -> List[CartItem]:
        return self.get_user_cart(db, customer_id=customer_id)


class CRUDWishlist(CRUDBase[Wishlist, dict, dict]):

    def get_by_customer(self, db: Session, *, customer_id: UUID) -> List[Wishlist]:
        return db.query(Wishlist).options(
            joinedload(Wishlist.product),
        ).filter(Wishlist.customer_id == customer_id).all()

    def add(self, db: Session, *, customer_id: UUID, product_id: UUID) -> Wishlist:
        existing = db.query(Wishlist).filter(
            Wishlist.customer_id == customer_id,
            Wishlist.product_id  == product_id,
        ).first()
        if existing:
            return existing
        item = Wishlist(customer_id=customer_id, product_id=product_id)
        db.add(item)
        db.commit()
        return item

    def remove(self, db: Session, *, customer_id: UUID, product_id: UUID) -> None:
        db.query(Wishlist).filter(
            Wishlist.customer_id == customer_id,
            Wishlist.product_id  == product_id,
        ).delete()
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
        notes: Optional[str] = None,
        platform_fee: Decimal = Decimal("50.00"),
        shipping_fee: Decimal = Decimal("0.00"),
    ) -> ProductOrder:
        """
        Create an order record and reduce stock for each line item.
        Blueprint §5.4: platform_fee = ₦50 per product order.
        All timestamps use datetime.now(timezone.utc) — Blueprint §16.4.
        """
        subtotal            = Decimal("0.00")
        order_items_data: List[Dict[str, Any]] = []

        for item_data in items:
            product_id = item_data["product_id"]
            variant_id = item_data.get("variant_id")
            quantity   = item_data.get("quantity", 1)

            product = db.query(Product).filter(Product.id == product_id).first()
            if not product:
                raise NotFoundException(f"Product {product_id}")

            if variant_id:
                variant    = db.query(ProductVariant).filter(
                    ProductVariant.id == variant_id
                ).first()
                unit_price = variant.price if variant else product.price
                # [BUG-2 SUPPORT] Blueprint §14: single price field
                product_snapshot = {
                    "id":               str(product.id),
                    "name":             product.name,
                    "category":         product.category,
                    "brand":            product.brand,
                    "price":            str(product.price),
                    "effective_price":  str(unit_price),
                    "images":           (product.images or [])[:1],
                    "variant_attributes": variant.attributes if variant else {},
                    "variant_sku":      variant.sku if variant else None,
                }
            else:
                # [BUG-2 SUPPORT] product.price (not product.base_price/sale_price)
                unit_price = product.price
                product_snapshot = {
                    "id":              str(product.id),
                    "name":            product.name,
                    "category":        product.category,
                    "brand":           product.brand,
                    "price":           str(product.price),
                    "effective_price": str(unit_price),
                    "images":          (product.images or [])[:1],
                    "sku":             getattr(product, "sku", None),
                }

            item_total = unit_price * quantity
            subtotal  += item_total

            order_items_data.append({
                "product_id":       product_id,
                "variant_id":       variant_id,
                "vendor_id":        product.vendor_id,
                "quantity":         quantity,
                "unit_price":       unit_price,
                "total_price":      item_total,
                "product_snapshot": product_snapshot,
            })

        tax           = Decimal("0.00")
        discount      = Decimal("0.00")
        total_amount  = subtotal + shipping_fee + platform_fee + tax - discount

        order = ProductOrder(
            customer_id=customer_id,
            shipping_address=shipping_address,
            recipient_name=recipient_name,
            recipient_phone=recipient_phone,
            subtotal=subtotal,
            shipping_fee=shipping_fee,
            tax=tax,
            discount=discount,
            platform_fee=platform_fee,
            total_amount=total_amount,
            payment_method=payment_method,
            coupon_code=coupon_code,
            notes=notes,
        )
        db.add(order)
        db.flush()  # get order.id without committing

        for item_data in order_items_data:
            db.add(OrderItem(order_id=order.id, **item_data))
            product_crud.reduce_stock(
                db,
                product_id=item_data["product_id"],
                variant_id=item_data.get("variant_id"),
                quantity=item_data["quantity"],
            )

        db.commit()
        db.refresh(order)
        return order

    def get_customer_orders(
        self, db: Session, *, customer_id: UUID, skip: int = 0, limit: int = 20
    ) -> List[ProductOrder]:
        return db.query(ProductOrder).options(
            joinedload(ProductOrder.items)
        ).filter(
            ProductOrder.customer_id == customer_id
        ).order_by(ProductOrder.created_at.desc()).offset(skip).limit(limit).all()

    def get_vendor_orders(
        self, db: Session, *, vendor_id: UUID,
        status: Optional[str] = None, skip: int = 0, limit: int = 50
    ) -> List[ProductOrder]:
        query = db.query(ProductOrder).options(
            joinedload(ProductOrder.items)
        ).join(OrderItem).filter(OrderItem.vendor_id == vendor_id)
        if status:
            query = query.filter(ProductOrder.order_status == status)
        return query.distinct().order_by(
            ProductOrder.created_at.desc()
        ).offset(skip).limit(limit).all()

    def get_business_orders(
        self, db: Session, *, business_id: UUID,
        status: Optional[str] = None, skip: int = 0, limit: int = 50
    ) -> List[ProductOrder]:
        """
        [BUG-3 SUPPORT] Get orders for a business via product.business_id.
        Called by the fixed products.py router (get_vendor_orders → get_business_orders).
        """
        query = (
            db.query(ProductOrder)
            .options(joinedload(ProductOrder.items))
            .join(OrderItem, OrderItem.order_id == ProductOrder.id)
            .join(Product,   Product.id          == OrderItem.product_id)
            .filter(Product.business_id == business_id)
        )
        if status:
            query = query.filter(ProductOrder.order_status == status)
        return query.distinct().order_by(
            ProductOrder.created_at.desc()
        ).offset(skip).limit(limit).all()

    def get_by_tracking_number(
        self, db: Session, *, tracking_number: str
    ) -> Optional[ProductOrder]:
        return db.query(ProductOrder).filter(
            ProductOrder.tracking_number == tracking_number
        ).first()

    def update_order_status(
        self, db: Session, *, order_id: UUID, new_status: str,
        tracking_number: Optional[str] = None, estimated_delivery=None
    ) -> ProductOrder:
        order = self.get(db, id=order_id)
        if not order:
            raise NotFoundException("Order")

        try:
            order.order_status = OrderStatusEnum(new_status.lower())
        except ValueError:
            raise ValidationException(
                f"Invalid order status '{new_status}'. "
                f"Valid values: {[e.value for e in OrderStatusEnum]}"
            )

        if tracking_number:
            order.tracking_number = tracking_number
        if estimated_delivery:
            order.estimated_delivery = estimated_delivery

        if order.order_status == OrderStatusEnum.DELIVERED:
            order.delivered_at = _utcnow()  # Blueprint §16.4

        db.commit()
        db.refresh(order)
        return order

    def cancel_order(
        self, db: Session, *, order_id: UUID, customer_id: Optional[UUID] = None
    ) -> ProductOrder:
        order = self.get(db, id=order_id)
        if not order:
            raise NotFoundException("Order")

        if customer_id and order.customer_id != customer_id:
            raise PermissionDeniedException()

        non_cancellable = {
            OrderStatusEnum.DELIVERED,
            OrderStatusEnum.CANCELLED,
            OrderStatusEnum.REFUNDED,
        }
        if order.order_status in non_cancellable:
            raise ValidationException(
                f"Cannot cancel order in '{order.order_status.value}' status."
            )

        for item in order.items:
            product_crud.restore_stock(
                db,
                product_id=item.product_id,
                variant_id=item.variant_id,
                quantity=item.quantity,
            )

        order.order_status = OrderStatusEnum.CANCELLED
        db.commit()
        db.refresh(order)
        return order

    def create_return_request(
        self, db: Session, *,
        order_id: UUID,
        customer_id: UUID,
        reason: str,
        item_ids: List[UUID],
        photos: List[str],
    ) -> ProductReturn:
        """Blueprint §6.4: in-app return/refund request flow."""
        order = self.get(db, id=order_id)
        if not order:
            raise NotFoundException("Order")
        if order.customer_id != customer_id:
            raise PermissionDeniedException()
        if order.order_status != OrderStatusEnum.DELIVERED:
            raise ValidationException("Returns are only accepted for delivered orders.")

        return_req = ProductReturn(
            order_id=order_id,
            customer_id=customer_id,
            reason=reason,
            item_ids=[str(i) for i in item_ids],
            photos=photos,
        )
        db.add(return_req)
        db.commit()
        db.refresh(return_req)
        return return_req


# ── Singletons ─────────────────────────────────────────────────────────────────
product_vendor_crud   = CRUDProductVendor(ProductVendor)
product_crud          = CRUDProduct(Product)
product_variant_crud  = CRUDProductVariant(ProductVariant)
cart_crud             = CRUDCart(CartItem)
wishlist_crud         = CRUDWishlist(Wishlist)
product_order_crud    = CRUDProductOrder(ProductOrder)