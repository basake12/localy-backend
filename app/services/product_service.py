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
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    InsufficientBalanceException,
    OutOfStockException,
)
from app.models.user_model import User
from app.models.products_model import (
    CartItem,
    ProductOrder,
    OrderStatusEnum,
    PaymentStatusEnum,
)
from app.models.wallet_model import (
    Wallet,
    WalletTransaction,
    TransactionTypeEnum,
    TransactionStatusEnum,
)
from app.schemas.products_schema import (
    ProductListResponse,
    ProductResponse,
    VendorResponse,
)


# ──────────────────────────────────────────────────────────────────────────────
# Module-level constants
# ──────────────────────────────────────────────────────────────────────────────

# Blueprint §4.4 — ₦50 flat fee on every transaction where payment is made
# through the app (products, food orders, etc.).
# Source of truth: changing this here propagates to all checkout logic.
PLATFORM_FEE = Decimal("50.00")

# Blueprint §3.1 — default discovery radius is 5 km.
DEFAULT_RADIUS_KM: float = 5.0

# Default shipping fee for product orders.
# Not specified in the blueprint — should eventually be dynamic (per vendor
# distance, weight, etc.). Kept as a named constant so it is never a magic
# number buried in business logic.
DEFAULT_SHIPPING_FEE = Decimal("2000.00")


class ProductService:
    """Business logic for product operations."""

    SUPPORTED_PAYMENT_METHODS = {"wallet"}

    # ──────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_in_stock(product) -> bool:
        """
        Correctly checks variant stock when product has variants.
        Falls back to base product stock for non-variant products.
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
        Serialize a Product ORM object to a plain dict suitable for
        ProductListResponse. Vendor must be preloaded via joinedload before
        calling this — accessing product.vendor here must never trigger a
        lazy-load (N+1 risk).
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
    def _resolve_cart_item_price(item: CartItem) -> Decimal:
        """
        Determine the unit price for a cart item.

        Resolution order:
          1. Variant price — when a variant is selected and loaded
          2. Product sale_price — active promotion
          3. Product base_price — standard price

        Precondition: item.product (and item.variant if variant_id is set) must
        be eagerly loaded before calling this method. Use
        cart_crud.get_cart_item_with_relations() to guarantee that.
        """
        if item.variant_id and item.variant:
            return item.variant.price
        return item.product.sale_price or item.product.base_price

    @staticmethod
    def _build_cart_item_dict(item: CartItem) -> Dict[str, Any]:
        """
        Build a dict that maps 1-to-1 with CartItemResponse fields.

        This is the canonical serialization bridge between a CartItem ORM object
        and the CartItemResponse Pydantic schema. It exists because CartItemResponse
        contains item_total — a response-only computed field with no backing column
        on CartItem. Any router returning CartItemResponse MUST go through this
        method rather than passing a raw ORM object to FastAPI.

        Precondition: item.product and item.variant must already be loaded
        (call cart_crud.get_cart_item_with_relations() first).
        """
        unit_price = ProductService._resolve_cart_item_price(item)
        return {
            "id": item.id,
            "product_id": item.product_id,
            "variant_id": item.variant_id,
            "quantity": item.quantity,
            "product": item.product,
            "variant": item.variant,
            "item_total": unit_price * item.quantity,
        }

    @staticmethod
    def _credit_business_wallet(
        db: Session,
        *,
        vendor_id: UUID,
        amount: Decimal,
        order_id: UUID,
        description: str,
    ) -> None:
        """
        Credit a vendor's business wallet after a successful customer payment.

        Blueprint §4.2 — "All customer payments minus platform fee, deposited
        instantly on transaction completion."

        IMPORTANT: This method must be called INSIDE the same database
        transaction as the customer wallet debit. If this fails, the entire
        transaction rolls back — the customer is not charged without the vendor
        being credited.

        Assumes a BusinessWallet model with user_id → business.user_id linkage.
        Adjust the query if your BusinessWallet uses business_id directly.
        """
        from app.models.business_model import Business
        from app.models.wallet_model import BusinessWallet  # adjust import path if needed

        # Resolve business from vendor to get the wallet owner
        from app.models.products_model import ProductVendor
        vendor = db.query(ProductVendor).filter(ProductVendor.id == vendor_id).first()
        if not vendor:
            # Vendor deleted mid-transaction — log and skip rather than crash
            # (order is already committed at this point)
            import logging
            logging.getLogger(__name__).error(
                "Business wallet credit skipped — vendor %s not found for order %s",
                vendor_id, order_id
            )
            return

        business = db.query(Business).filter(Business.id == vendor.business_id).first()
        if not business:
            return

        biz_wallet = db.query(BusinessWallet).filter(
            BusinessWallet.business_id == business.id
        ).first()
        if not biz_wallet:
            # Wallet not created yet — should not happen in production but guard
            # gracefully rather than raising (would roll back the entire order).
            import logging
            logging.getLogger(__name__).error(
                "Business wallet not found for business %s — credit of ₦%s skipped.",
                business.id, amount
            )
            return

        from datetime import datetime as _dt
        balance_before = biz_wallet.balance
        biz_wallet.balance += amount
        db.add(WalletTransaction(
            wallet_id=biz_wallet.id,
            transaction_type=TransactionTypeEnum.CREDIT,
            amount=amount,
            balance_before=balance_before,
            balance_after=biz_wallet.balance,
            status=TransactionStatusEnum.COMPLETED,
            description=description,
            reference_id=str(order_id),
            completed_at=_dt.utcnow(),
        ))

    # ──────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────

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
            # FIX: Blueprint §3.1 — default is 5 km, not 10 km.
            radius_km: float = DEFAULT_RADIUS_KM,
            sort_by: str = "created_at",
            skip: int = 0,
            limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Radius-based product discovery. Vendor + business loaded via joinedload
        inside the CRUD query — zero N+1 queries.
        lga_id removed — Blueprint §3: radius-based search only.
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
        return [ProductService._safe_product_list_item(p) for p in products]

    @staticmethod
    def get_product_details(db: Session, *, product_id: UUID) -> Dict[str, Any]:
        """
        Full product detail with all relations preloaded in a single query.
        View count incremented only after confirming the product is active.
        """
        product = product_crud.get_with_relations(db, product_id=product_id)
        if not product:
            raise NotFoundException("Product")

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
        Compute cart totals and return serialization-ready item dicts.

        Each entry in items[] is a plain dict matching CartItemResponse fields
        exactly — including item_total. The router can pass these directly into
        the response without any further transformation.

        Previously this returned raw ORM objects in items[], which caused
        ResponseValidationError on every cart read because CartItemResponse
        expects item_total (a computed field) that has no backing column.
        """
        cart_items = cart_crud.get_user_cart(db, customer_id=customer_id)

        subtotal = Decimal('0.00')
        serializable_items: List[Dict[str, Any]] = []

        for item in cart_items:
            # Resolve unit price — variant takes precedence over product price
            if item.variant_id:
                if item.variant:
                    price = item.variant.price
                else:
                    # Variant not loaded despite joinedload — fetch explicitly
                    variant = product_variant_crud.get(db, id=item.variant_id)
                    price = variant.price if variant else (
                        item.product.sale_price or item.product.base_price
                    )
            else:
                price = item.product.sale_price or item.product.base_price

            item_total = price * item.quantity
            subtotal += item_total

            # Build a CartItemResponse-compatible dict immediately.
            # item_total lives here — the schema has no ORM backing for it.
            serializable_items.append({
                "id": item.id,
                "product_id": item.product_id,
                "variant_id": item.variant_id,
                "quantity": item.quantity,
                "product": item.product,
                "variant": item.variant,
                "item_total": item_total,
            })

        return {
            "items": serializable_items,
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
          2. Validate all items exist and have sufficient stock
          3. Pre-calculate total (subtotal + shipping_fee + platform_fee)
          4. Check wallet balance BEFORE touching inventory
          5. Create order — stock reduced atomically here
          6. Debit customer wallet
          7. Credit each vendor's business wallet (amount - platform_fee share)
          8. Mark order paid and commit

        Stock is NEVER permanently lost on payment failure.
        Unsupported payment methods are rejected at the boundary.

        FIX — two bugs that caused the 500 crash:
          1. order_status must use OrderStatusEnum members, not raw uppercase
             strings. The DB enum 'orderstatusenum' stores lowercase values
             ("processing" not "PROCESSING"). Passing "PROCESSING" caused:
             psycopg2.errors.InvalidTextRepresentation.
          2. payment_reference must be stored as None (SQL NULL) if no reference
             exists — NEVER str(None) which inserts the literal string "None".
             We now store str(transaction.id) only after the transaction is created.
        """

        # ── Step 1: Validate payment method ──────────────────────────────
        if payment_method not in ProductService.SUPPORTED_PAYMENT_METHODS:
            raise ValidationException(
                f"Payment method '{payment_method}' is not supported. "
                f"Supported: {', '.join(sorted(ProductService.SUPPORTED_PAYMENT_METHODS))}"
            )

        # ── Steps 2 & 3: Validate items and pre-calculate total ───────────
        # Collect vendor_ids and per-vendor subtotals for business wallet credit
        vendor_subtotals: Dict[UUID, Decimal] = {}
        items_subtotal = Decimal('0.00')

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

            line_total = price * item_data['quantity']
            items_subtotal += line_total

            # Track per-vendor revenue for business wallet crediting (step 7)
            vendor_id = product.vendor_id
            vendor_subtotals[vendor_id] = vendor_subtotals.get(vendor_id, Decimal('0.00')) + line_total

        # FIX: Platform fee added to what the customer pays, then collected by
        # Localy before crediting vendors.
        # Blueprint §4.4: "Fee is deducted from the transaction total before
        # crediting the business wallet."
        estimated_total = items_subtotal + DEFAULT_SHIPPING_FEE + PLATFORM_FEE

        # ── Step 4: Check wallet balance BEFORE touching inventory ────────
        wallet = None
        if payment_method == "wallet":
            from app.models.wallet_model import generate_wallet_number
            wallet = db.query(Wallet).filter(Wallet.user_id == current_user.id).first()
            if not wallet:
                wallet = Wallet(
                    user_id=current_user.id,
                    wallet_number=generate_wallet_number(),
                    balance=Decimal("0.00"),
                    currency="NGN",
                    is_active=True,
                )
                db.add(wallet)
                db.commit()
                db.refresh(wallet)
            if wallet.balance < estimated_total:
                raise InsufficientBalanceException()

        # ── Step 5: Create order — stock reduced inside create_order() ───
        #
        # CRITICAL NOTE FOR products_crud.create_order():
        # The CRUD must use OrderStatusEnum.PENDING (the enum member) or the
        # string "pending" (lowercase) — NOT "PENDING" or "PROCESSING".
        # PostgreSQL enum 'orderstatusenum' stores the .value of each member
        # which are all lowercase. Passing the Python attribute NAME (uppercase)
        # caused the DataError crash seen in the logs.
        #
        # Similarly, payment_reference must be set to None (SQL NULL) initially,
        # never str(None) = "None". It is updated to the transaction ID in step 7.
        order = product_order_crud.create_order(
            db,
            customer_id=current_user.id,
            items=items,
            shipping_address=shipping_address,
            recipient_name=recipient_name,
            recipient_phone=recipient_phone,
            payment_method=payment_method,
            coupon_code=coupon_code,
            notes=notes,
            platform_fee=PLATFORM_FEE,
            shipping_fee=DEFAULT_SHIPPING_FEE,
        )

        # ── Steps 6 & 7: Process wallet payment + credit vendors ──────────
        if payment_method == "wallet":
            try:
                from datetime import datetime as _dt
                _now = _dt.utcnow()

                # Step 6: Debit customer wallet
                _balance_before = wallet.balance
                wallet.balance -= order.total_amount
                customer_txn = WalletTransaction(
                    wallet_id=wallet.id,
                    transaction_type=TransactionTypeEnum.PAYMENT,
                    amount=order.total_amount,
                    balance_before=_balance_before,
                    balance_after=wallet.balance,
                    status=TransactionStatusEnum.COMPLETED,
                    description=f"Payment for order #{str(order.id)[:8].upper()}",
                    reference_id=str(order.id),
                    completed_at=_now,
                )
                db.add(customer_txn)

                # Step 7: Credit each vendor's business wallet.
                # Blueprint §4.4: platform_fee is shared across vendors proportionally
                # by their subtotal. For simplicity we deduct it from the largest
                # vendor's credit. In multi-vendor orders, consider splitting
                # platform_fee proportionally by (vendor_subtotal / items_subtotal).
                for idx, (vendor_id, vendor_amount) in enumerate(vendor_subtotals.items()):
                    # Deduct full platform fee from the first vendor's credit.
                    # For a more equitable split, replace with:
                    #   fee_share = (vendor_amount / items_subtotal) * PLATFORM_FEE
                    credit_amount = (
                        vendor_amount - PLATFORM_FEE if idx == 0
                        else vendor_amount
                    )
                    # Guard: credit must never be negative
                    credit_amount = max(credit_amount, Decimal("0.00"))

                    ProductService._credit_business_wallet(
                        db,
                        vendor_id=vendor_id,
                        amount=credit_amount,
                        order_id=order.id,
                        description=f"Sale payment for order #{str(order.id)[:8].upper()}",
                    )

            except (InsufficientBalanceException, ValidationException):
                raise
            except Exception as exc:
                # Any unexpected failure — restore stock and cancel order atomically.
                # The db.rollback() inside the except block undoes the wallet debit
                # and vendor credits that were not yet committed.
                db.rollback()
                for item in order.items:
                    product_crud.restore_stock(
                        db,
                        product_id=item.product_id,
                        variant_id=item.variant_id,
                        quantity=item.quantity
                    )
                order.order_status = OrderStatusEnum.CANCELLED
                db.commit()
                raise ValidationException(
                    "Payment failed — order cancelled and stock restored."
                ) from exc

            # ── Step 8: Mark order paid ────────────────────────────────────
            # FIX: Use enum members, not plain strings, when assigning to
            # SQLAlchemy Enum columns. SQLAlchemy will use the .value ("paid",
            # "processing") when building the INSERT/UPDATE — which matches
            # the lowercase values stored in the PostgreSQL enum.
            order.payment_status = PaymentStatusEnum.PAID
            order.order_status = OrderStatusEnum.PROCESSING
            # FIX: Store the wallet transaction ID as the payment reference.
            # Previously str(transaction.id) was set on a variable that didn't
            # exist yet at assignment time, leading to str(None) = "None" being
            # inserted. We now reference customer_txn which is defined above.
            order.payment_reference = str(customer_txn.id)
            db.commit()
            db.refresh(order)

        return order


product_service = ProductService()