from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from uuid import UUID
from decimal import Decimal

from app.crud.products import (
    product_crud,
    product_vendor_crud,
    product_variant_crud,
    cart_crud,
    product_order_crud
)
from app.crud.wallet import wallet_crud
from app.crud.business import business_crud
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    InsufficientBalanceException,
    OutOfStockException
)
from app.core.constants import TransactionType
from app.models.user import User
from app.models.products import ProductOrder


class ProductService:
    """Business logic for product operations"""

    @staticmethod
    def search_products(
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
    ) -> List[Dict[str, Any]]:
        """Search products with vendor info"""
        products = product_crud.search_products(
            db,
            query_text=query_text,
            category=category,
            subcategory=subcategory,
            brand=brand,
            min_price=min_price,
            max_price=max_price,
            in_stock_only=in_stock_only,
            location=location,
            radius_km=radius_km,
            sort_by=sort_by,
            skip=skip,
            limit=limit
        )

        # Enrich with vendor info
        results = []
        for product in products:
            vendor = product_vendor_crud.get(db, id=product.vendor_id)
            business = business_crud.get(db, id=vendor.business_id) if vendor else None

            results.append({
                "product": product,
                "vendor": vendor,
                "business": business,
                "in_stock": product.stock_quantity > 0,
                "effective_price": product.sale_price or product.base_price
            })

        return results

    @staticmethod
    def get_product_details(
            db: Session,
            *,
            product_id: UUID
    ) -> Dict[str, Any]:
        """Get full product details with variants"""
        product = product_crud.get(db, id=product_id)
        if not product:
            raise NotFoundException("Product")

        # Increment view count
        product_crud.increment_views(db, product_id=product_id)

        # Get variants
        variants = product_variant_crud.get_by_product(db, product_id=product_id)

        # Get vendor info
        vendor = product_vendor_crud.get(db, id=product.vendor_id)
        business = business_crud.get(db, id=vendor.business_id) if vendor else None

        return {
            "product": product,
            "variants": variants,
            "vendor": vendor,
            "business": business,
            "in_stock": product.stock_quantity > 0,
            "effective_price": product.sale_price or product.base_price
        }

    @staticmethod
    def calculate_cart_total(
            db: Session,
            *,
            customer_id: UUID
    ) -> Dict[str, Any]:
        """Calculate cart totals"""
        cart_items = cart_crud.get_user_cart(db, customer_id=customer_id)

        subtotal = Decimal('0.00')
        items_detail = []

        for item in cart_items:
            # Get effective price
            if item.variant_id:
                variant = item.variant
                price = variant.price
            else:
                product = item.product
                price = product.sale_price or product.base_price

            item_total = price * item.quantity
            subtotal += item_total

            items_detail.append({
                "cart_item": item,
                "unit_price": price,
                "item_total": item_total
            })

        return {
            "items": items_detail,
            "subtotal": subtotal,
            "total_items": len(cart_items)
        }

    @staticmethod
    def checkout_and_pay(
            db: Session,
            *,
            current_user: User,
            items: List[Dict[str, Any]],
            shipping_address: str,
            recipient_name: str,
            recipient_phone: str,
            payment_method: str,
            notes: Optional[str] = None
    ) -> ProductOrder:
        """
        Create order and process payment

        Supports wallet payment only for now
        """
        # Create order
        order = product_order_crud.create_order(
            db,
            customer_id=current_user.id,
            items=items,
            shipping_address=shipping_address,
            recipient_name=recipient_name,
            recipient_phone=recipient_phone,
            payment_method=payment_method,
            notes=notes
        )

        # Process payment
        if payment_method == "wallet":
            # Get customer wallet
            wallet = wallet_crud.get_or_create_wallet(db, user_id=current_user.id)

            # Check balance
            if wallet.balance < order.total_amount:
                # Cancel order
                order.order_status = "cancelled"
                db.commit()
                raise InsufficientBalanceException()

            # Debit wallet
            wallet_crud.debit_wallet(
                db,
                wallet_id=wallet.id,
                amount=order.total_amount,
                transaction_type=TransactionType.PAYMENT,
                description=f"Payment for order {order.id}",
                reference_id=str(order.id)
            )

            # Update order status
            order.payment_status = "paid"
            order.order_status = "processing"
            order.payment_reference = str(order.id)
            db.commit()
            db.refresh(order)

        # TODO: Create delivery request

        return order


product_service = ProductService()