from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from uuid import UUID
from decimal import Decimal

from app.crud.products_crud import (
    product_crud,
    product_vendor_crud,
    product_variant_crud,
    cart_crud,
    product_order_crud,
    wishlist_crud,
)
from app.crud.wallet_crud import wallet_crud
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    InsufficientBalanceException,
    OutOfStockException,
)
from app.core.constants import TransactionType
from app.models.user_model import User
from app.models.products_model import ProductOrder
from app.schemas.products_schema import (
    ProductListResponse,
    ProductResponse,
    VendorResponse,
)


class ProductService:
    """Business logic for product operations."""

    # Payment methods that are currently active
    SUPPORTED_PAYMENT_METHODS = ["wallet"]

    @staticmethod
    def _compute_in_stock(product) -> bool:
        """
        FIX: Correctly checks variant stock when product has variants.
        Previously only checked base product.stock_quantity, which is always
        0 for variant-based products, making them appear out of stock.
        """
        if product.variants:
            return any(
                v.stock_quantity > 0
                for v in product.variants
                if v.is_active
            )
        return product.stock_quantity > 0

    @staticmethod
    def _safe_product_list_item(product) -> Dict[str, Any]:
        """
        FIX: Serialize product to a plain dict for list responses.
        Prevents raw ORM objects from being returned — which causes lazy-load
        explosions and potential data leakage of unrelated relationships.
        Vendor must be preloaded via joinedload before calling this.
        """
        vendor_name = None
        if product.vendor:
            vendor_name = product.vendor.store_name

        return {
            "id": product.id,
            "name": product.name,
            "category": product.category,
            "brand": product.brand,
            "base_price": product.base_price,
            "sale_price": product.sale_price,
            "images": product.images or [],
            "average_rating": product.average_rating,
            "in_stock": ProductService._compute_in_stock(product),
            "vendor_id": product.vendor_id,
            "vendor_name": vendor_name,
        }

    @staticmethod
    def search_products(
            db: Session, *,
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
        """
        FIX: vendor + business now loaded via joinedload inside search_products()
        CRUD query — zero N+1 queries. Previously fired 2 extra DB queries per
        product (41 total for a page of 20).
        """
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

        # FIX: Serialize to plain dicts — no raw ORM objects returned
        return [ProductService._safe_product_list_item(p) for p in products]

    @staticmethod
    def get_product_details(db: Session, *, product_id: UUID) -> Dict[str, Any]:
        """
        Get full product details with all relations preloaded.
        FIX: Uses get_with_relations() which eager-loads vendor + variants in 1 query.
        FIX: is_active checked before incrementing view count.
        """
        # FIX: get_with_relations already filters is_active=True
        product = product_crud.get_with_relations(db, product_id=product_id)
        if not product:
            raise NotFoundException("Product")

        # FIX: Increment views only AFTER confirming product is active
        product_crud.increment_views(db, product_id=product_id)

        vendor = product.vendor
        business = vendor.business if vendor else None

        return {
            "id": product.id,
            "name": product.name,
            "description": product.description,
            "category": product.category,
            "subcategory": product.subcategory,
            "brand": product.brand,
            "product_type": product.product_type,
            "base_price": product.base_price,
            "sale_price": product.sale_price,
            "effective_price": product.sale_price or product.base_price,
            "sku": product.sku,
            "stock_quantity": product.stock_quantity,
            "low_stock_threshold": product.low_stock_threshold,
            "specifications": product.specifications or {},
            "images": product.images or [],
            "videos": product.videos or [],
            "download_url": product.download_url,
            "views_count": product.views_count,
            "sales_count": product.sales_count,
            "average_rating": product.average_rating,
            "total_reviews": product.total_reviews,
            "is_active": product.is_active,
            "created_at": product.created_at,
            "in_stock": ProductService._compute_in_stock(product),
            "variants": [
                {
                    "id": v.id,
                    "sku": v.sku,
                    "attributes": v.attributes,
                    "price": v.price,
                    "stock_quantity": v.stock_quantity,
                    "images": v.images or [],
                    "is_active": v.is_active,
                }
                for v in product.variants if v.is_active
            ],
            "vendor": {
                "id": vendor.id,
                "store_name": vendor.store_name,
                "store_logo": vendor.store_logo,
                "store_banner": vendor.store_banner,
                "return_policy": vendor.return_policy,
                "average_rating": getattr(business, 'average_rating', None),
                "total_sales": vendor.total_sales,
            } if vendor else None,
        }

    @staticmethod
    def calculate_cart_total(db: Session, *, customer_id: UUID) -> Dict[str, Any]:
        """
        FIX: Added None guard on variant relationship.
        FIX: Cart items loaded with joinedload in get_user_cart().
        """
        cart_items = cart_crud.get_user_cart(db, customer_id=customer_id)

        subtotal = Decimal('0.00')
        items_detail = []

        for item in cart_items:
            # FIX: Safe variant price access with explicit fallback
            if item.variant_id:
                if item.variant:
                    price = item.variant.price
                else:
                    # Variant relationship not loaded — fetch explicitly
                    variant = product_variant_crud.get(db, id=item.variant_id)
                    price = variant.price if variant else (
                        item.product.sale_price or item.product.base_price
                    )
            else:
                price = item.product.sale_price or item.product.base_price

            item_total = price * item.quantity
            subtotal += item_total

            items_detail.append({
                "cart_item": item,
                "unit_price": price,
                "item_total": item_total,
            })

        return {
            "items": items_detail,
            "subtotal": subtotal,
            "total_items": len(cart_items),
        }

    @staticmethod
    def checkout_and_pay(
            db: Session, *,
            current_user: User,
            items: List[Dict[str, Any]],
            shipping_address: str,
            recipient_name: str,
            recipient_phone: str,
            payment_method: str,
            coupon_code: Optional[str] = None,
            notes: Optional[str] = None
    ) -> ProductOrder:
        """
        Safe checkout flow. Order of operations is critical:
          1. Validate payment method is supported
          2. Validate all items exist and have stock
          3. Pre-calculate total
          4. Check wallet balance
          5. Create order (stock reduced here)
          6. Debit wallet — if this fails, restore stock and cancel
          7. Mark order paid and commit

        FIX: Stock is NEVER permanently lost on payment failure.
        FIX: Non-wallet payment methods raise an error immediately instead of
             silently creating an unpaid order.
        FIX: payment_reference now stores wallet transaction ID, not order ID.
        """

        # ── Step 1: Validate payment method first ────────────────────────
        if payment_method not in ProductService.SUPPORTED_PAYMENT_METHODS:
            raise ValidationException(
                f"Payment method '{payment_method}' is not supported. "
                f"Supported: {', '.join(ProductService.SUPPORTED_PAYMENT_METHODS)}"
            )

        # ── Step 2 & 3: Validate items and pre-calculate total ────────────
        # We do this BEFORE creating the order so we never touch stock
        # if the payment will fail.
        estimated_total = Decimal('0.00')
        for item_data in items:
            product = product_crud.get(db, id=item_data['product_id'])
            if not product or not product.is_active:
                raise NotFoundException(f"Product {item_data['product_id']}")

            if not product_crud.check_stock(
                    db,
                    product_id=product.id,
                    variant_id=item_data.get('variant_id'),
                    quantity=item_data['quantity']
            ):
                raise OutOfStockException(product.name)

            if item_data.get('variant_id'):
                variant = product_variant_crud.get(db, id=item_data['variant_id'])
                if not variant:
                    raise NotFoundException(f"Variant {item_data['variant_id']}")
                price = variant.price
            else:
                price = product.sale_price or product.base_price

            estimated_total += price * item_data['quantity']

        shipping_fee = Decimal('2000.00')
        estimated_total += shipping_fee

        # ── Step 4: Check wallet balance BEFORE touching inventory ────────
        if payment_method == "wallet":
            wallet = wallet_crud.get_or_create_wallet(db, user_id=current_user.id)
            if wallet.balance < estimated_total:
                raise InsufficientBalanceException()
                # No stock touched — safe to raise here

        # ── Step 5: Create order — stock reduced inside create_order() ───
        order = product_order_crud.create_order(
            db,
            customer_id=current_user.id,
            items=items,
            shipping_address=shipping_address,
            recipient_name=recipient_name,
            recipient_phone=recipient_phone,
            payment_method=payment_method,
            coupon_code=coupon_code,
            notes=notes
        )

        # ── Step 6: Process payment ───────────────────────────────────────
        if payment_method == "wallet":
            try:
                transaction = wallet_crud.debit_wallet(
                    db,
                    wallet_id=wallet.id,
                    amount=order.total_amount,
                    transaction_type=TransactionType.PAYMENT,
                    description=f"Payment for order #{str(order.id)[:8].upper()}",
                    reference_id=str(order.id)
                )
            except Exception as exc:
                # FIX: Debit failed — restore stock and cancel order atomically
                for item in order.items:
                    product_crud.restore_stock(
                        db,
                        product_id=item.product_id,
                        variant_id=item.variant_id,
                        quantity=item.quantity
                    )
                order.order_status = "cancelled"
                db.commit()
                raise ValidationException(
                    "Payment failed — order cancelled and stock restored."
                ) from exc

            # ── Step 7: Mark order paid ───────────────────────────────────
            order.payment_status = "paid"
            order.order_status = "processing"
            # FIX: Store the wallet transaction ID (not the order ID)
            order.payment_reference = str(transaction.id)
            db.commit()
            db.refresh(order)

        # ── Step 8: Trigger delivery creation ────────────────────────────
        # TODO: delivery_service.create_from_order(db, order=order)
        # Implement once delivery_service.py is reviewed

        return order


product_service = ProductService()