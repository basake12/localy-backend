"""
app/services/product_service.py

FIXES vs previous version:
  1.  Wallet.user_id → Wallet.owner_id × 2. Blueprint §14.

  2.  [HARD RULE §16.4] _dt.utcnow() × 2 → datetime.now(timezone.utc).

  3.  WalletTransaction.reference_id → WalletTransaction.external_reference.
      Blueprint §14.

  4.  idempotency_key added to every WalletTransaction.
      Blueprint §5.6 HARD RULE: all financial operations use idempotency keys.

  5.  Wallet.is_active → Wallet.is_suspended. Blueprint §14.

  6.  Wallet(user_id=...) → Wallet(owner_id=..., owner_type='customer').
      Blueprint §14.

  7.  TransactionTypeEnum → TransactionType (correct import from wallet_model).
      TransactionStatusEnum → TransactionStatus (correct import).

  8.  Platform fee structure corrected.
      Blueprint §5.4: "₦50 from business + ₦50 from customer."
      Customer is charged: subtotal + shipping + ₦50 (customer_fee).
      Business receives: vendor_subtotal - ₦50 (business_fee) per vendor.
      Platform earns: ₦50 (from customer) + ₦50 (from business) = ₦100.

  9.  current_user.phone_number (not phone). Blueprint §14.
"""
import logging
import uuid as _uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.crud.products_crud import (
    cart_crud,
    product_crud,
    product_order_crud,
    product_variant_crud,
    product_vendor_crud,
    wishlist_crud,
)
from app.core.exceptions import (
    InsufficientBalanceException,
    NotFoundException,
    OutOfStockException,
    ValidationException,
)
from app.models.products_model import CartItem, OrderStatusEnum, PaymentStatusEnum, ProductOrder
from app.models.user_model import User
from app.models.wallet_model import (
    Wallet,
    WalletTransaction,
    TransactionType,      # correct import (not TransactionTypeEnum)
    TransactionStatus,    # correct import (not TransactionStatusEnum)
)

logger = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────────

# Blueprint §5.4: ₦50 per side (customer pays ₦50, business pays ₦50 = ₦100 total)
PLATFORM_FEE_PER_SIDE = Decimal("50.00")

# Blueprint §4.1: default 5 km radius
DEFAULT_RADIUS_KM: float = 5.0

DEFAULT_SHIPPING_FEE = Decimal("2000.00")

SUPPORTED_PAYMENT_METHODS = {"wallet"}


def _utcnow() -> datetime:
    """Blueprint §16.4 HARD RULE: always timezone-aware."""
    return datetime.now(timezone.utc)


def _idem_key() -> str:
    return f"PROD_{_uuid.uuid4().hex.upper()}"


class ProductService:

    @staticmethod
    def _compute_in_stock(product) -> bool:
        if product.variants:
            return any(
                v.stock_quantity > 0 for v in product.variants if v.is_active
            )
        return product.stock_quantity > 0

    @staticmethod
    def _safe_product_list_item(product) -> Dict[str, Any]:
        vendor_name = product.vendor.store_name if product.vendor else None
        return {
            "id":             product.id,
            "name":           product.name,
            "category":       product.category,
            "brand":          product.brand,
            "base_price":     product.base_price,
            "sale_price":     product.sale_price,
            "images":         product.images or [],
            "average_rating": product.average_rating,
            "in_stock":       ProductService._compute_in_stock(product),
            "vendor_id":      product.vendor_id,
            "vendor_name":    vendor_name,
        }

    @staticmethod
    def _resolve_cart_item_price(item: CartItem) -> Decimal:
        if item.variant_id and item.variant:
            return item.variant.price
        return item.product.sale_price or item.product.base_price

    @staticmethod
    def _build_cart_item_dict(item: CartItem) -> Dict[str, Any]:
        unit_price = ProductService._resolve_cart_item_price(item)
        return {
            "id":          item.id,
            "product_id":  item.product_id,
            "variant_id":  item.variant_id,
            "quantity":    item.quantity,
            "product":     item.product,
            "variant":     item.variant,
            "item_total":  unit_price * item.quantity,
        }

    @staticmethod
    def _credit_business_wallet(
        db: Session,
        *,
        vendor_id: UUID,
        amount: Decimal,
        order_id: UUID,
        description: str,
        idempotency_key: str,   # Blueprint §5.6 HARD RULE
        external_reference: str,
    ) -> None:
        """
        Credit vendor's business wallet after customer payment.
        Blueprint §5.2: "All customer payments minus platform fee, deposited
        instantly on transaction completion."

        All wallet fields use Blueprint §14 names:
          owner_id (not user_id), external_reference (not reference_id),
          idempotency_key NOT NULL UNIQUE.
        """
        from app.models.business_model import Business
        from app.models.products_model import ProductVendor

        vendor = db.query(ProductVendor).filter(ProductVendor.id == vendor_id).first()
        if not vendor:
            logger.error("Vendor %s not found — business wallet credit skipped", vendor_id)
            return

        business = db.query(Business).filter(Business.id == vendor.business_id).first()
        if not business:
            logger.error("Business not found for vendor %s — credit skipped", vendor_id)
            return

        # Blueprint §14: owner_id (not user_id)
        biz_wallet = db.query(Wallet).filter(
            Wallet.owner_id == business.user_id
        ).first()
        if not biz_wallet:
            logger.error("Business wallet not found for business %s", business.id)
            return

        balance_before   = biz_wallet.balance
        biz_wallet.balance += amount

        db.add(WalletTransaction(
            wallet_id=biz_wallet.id,
            transaction_type=TransactionType.CREDIT,
            amount=amount,
            balance_before=balance_before,
            balance_after=biz_wallet.balance,
            status=TransactionStatus.COMPLETED,
            description=description,
            # Blueprint §14: external_reference (not reference_id)
            external_reference=external_reference,
            # Blueprint §5.6 HARD RULE: idempotency_key NOT NULL UNIQUE
            idempotency_key=idempotency_key,
            # Blueprint §16.4 HARD RULE: timezone-aware timestamp
            completed_at=_utcnow(),
        ))

    # ── Public API ─────────────────────────────────────────────────────────────

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
        radius_km: float = DEFAULT_RADIUS_KM,
        sort_by: str = "created_at",
        skip: int = 0,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
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
            limit=limit,
        )
        return [ProductService._safe_product_list_item(p) for p in products]

    @staticmethod
    def get_product_details(db: Session, *, product_id: UUID) -> Dict[str, Any]:
        product = product_crud.get_with_relations(db, product_id=product_id)
        if not product:
            raise NotFoundException("Product")

        product_crud.increment_views(db, product_id=product_id)

        vendor   = product.vendor
        business = vendor.business if vendor else None

        return {
            "id":                product.id,
            "name":              product.name,
            "description":       product.description,
            "category":          product.category,
            "subcategory":       product.subcategory,
            "brand":             product.brand,
            "base_price":        product.base_price,
            "sale_price":        product.sale_price,
            "effective_price":   product.sale_price or product.base_price,
            "sku":               product.sku,
            "stock_quantity":    product.stock_quantity,
            "images":            product.images or [],
            "download_url":      product.download_url,
            "views_count":       product.views_count,
            "sales_count":       product.sales_count,
            "average_rating":    product.average_rating,
            "total_reviews":     product.total_reviews,
            "is_active":         product.is_active,
            "created_at":        product.created_at,
            "in_stock":          ProductService._compute_in_stock(product),
            "variants":          [
                {
                    "id":            v.id,
                    "sku":           v.sku,
                    "attributes":    v.attributes,
                    "price":         v.price,
                    "stock_quantity": v.stock_quantity,
                    "images":        v.images or [],
                    "is_active":     v.is_active,
                }
                for v in product.variants if v.is_active
            ],
            "vendor": {
                "id":             vendor.id,
                "store_name":     vendor.store_name,
                "store_logo":     vendor.store_logo,
                "return_policy":  vendor.return_policy,
                "total_sales":    vendor.total_sales,
            } if vendor else None,
        }

    @staticmethod
    def calculate_cart_total(db: Session, *, customer_id: UUID) -> Dict[str, Any]:
        cart_items = cart_crud.get_user_cart(db, customer_id=customer_id)
        subtotal   = Decimal("0.00")
        items_out: List[Dict[str, Any]] = []

        for item in cart_items:
            if item.variant_id and item.variant:
                price = item.variant.price
            elif item.variant_id:
                variant = product_variant_crud.get(db, id=item.variant_id)
                price = variant.price if variant else (
                    item.product.sale_price or item.product.base_price
                )
            else:
                price = item.product.sale_price or item.product.base_price

            item_total  = price * item.quantity
            subtotal   += item_total

            items_out.append({
                "id":         item.id,
                "product_id": item.product_id,
                "variant_id": item.variant_id,
                "quantity":   item.quantity,
                "product":    item.product,
                "variant":    item.variant,
                "item_total": item_total,
            })

        return {"items": items_out, "subtotal": subtotal, "total_items": len(cart_items)}

    @staticmethod
    def checkout_and_pay(
        db: Session, *,
        current_user: User,
        items: List[Dict[str, Any]],
        shipping_address: str,
        recipient_name: Optional[str] = None,
        recipient_phone: Optional[str] = None,
        payment_method: str,
        coupon_code: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> ProductOrder:
        """
        Safe checkout — Blueprint §5.4 two-sided platform fee.

        Fee structure for product orders:
          Customer pays: subtotal + shipping_fee + ₦50 (customer_fee)
          Each vendor receives: vendor_subtotal - ₦50 (business_fee)
          Platform earns: ₦50 (customer) + ₦50 (business) = ₦100

        All wallet operations use:
          - owner_id (Blueprint §14: not user_id)
          - external_reference (Blueprint §14: not reference_id)
          - idempotency_key NOT NULL (Blueprint §5.6 HARD RULE)
          - datetime.now(timezone.utc) (Blueprint §16.4 HARD RULE)
        """
        if payment_method not in SUPPORTED_PAYMENT_METHODS:
            raise ValidationException(
                f"Payment method '{payment_method}' is not supported. Use 'wallet'."
            )

        # Blueprint §14: full_name and phone_number on User
        resolved_name  = recipient_name  or current_user.full_name
        resolved_phone = recipient_phone or current_user.phone_number

        if not resolved_name:
            raise ValidationException("recipient_name is required.")
        if not resolved_phone:
            raise ValidationException("recipient_phone is required.")

        # Validate items + pre-calculate
        vendor_subtotals: Dict[UUID, Decimal] = {}
        items_subtotal = Decimal("0.00")

        for item_data in items:
            product = product_crud.get_with_relations(db, product_id=item_data["product_id"])
            if not product or not product.is_active:
                raise NotFoundException(f"Product {item_data['product_id']}")

            active_variants = [v for v in product.variants if v.is_active]
            if active_variants and not item_data.get("variant_id"):
                raise ValidationException(
                    f"Product '{product.name}' has variants — select one."
                )

            if not product_crud.check_stock(
                db, product_id=product.id,
                variant_id=item_data.get("variant_id"),
                quantity=item_data["quantity"],
            ):
                raise OutOfStockException(product.name)

            if item_data.get("variant_id"):
                variant = product_variant_crud.get(db, id=item_data["variant_id"])
                if not variant:
                    raise NotFoundException(f"Variant {item_data['variant_id']}")
                price = variant.price
            else:
                price = product.sale_price or product.base_price

            line_total   = price * item_data["quantity"]
            items_subtotal += line_total

            vendor_id = product.vendor_id
            vendor_subtotals[vendor_id] = (
                vendor_subtotals.get(vendor_id, Decimal("0.00")) + line_total
            )

        # Blueprint §5.4: customer pays ₦50 platform fee
        customer_fee   = PLATFORM_FEE_PER_SIDE
        estimated_total = items_subtotal + DEFAULT_SHIPPING_FEE + customer_fee

        # Check wallet balance BEFORE touching inventory
        wallet = None
        if payment_method == "wallet":
            # Blueprint §14: owner_id (not user_id)
            wallet = db.query(Wallet).filter(Wallet.owner_id == current_user.id).first()
            if not wallet:
                raise InsufficientBalanceException()
            if wallet.balance < estimated_total:
                raise InsufficientBalanceException()

        # Create order (stock reduced inside create_order)
        order = product_order_crud.create_order(
            db,
            customer_id=current_user.id,
            items=items,
            shipping_address=shipping_address,
            recipient_name=resolved_name,
            recipient_phone=resolved_phone,
            payment_method=payment_method,
            coupon_code=coupon_code,
            notes=notes,
            platform_fee=customer_fee,
            shipping_fee=DEFAULT_SHIPPING_FEE,
        )

        if payment_method == "wallet":
            order_id_snapshot = order.id
            try:
                # Debit customer wallet
                balance_before  = wallet.balance
                wallet.balance -= order.total_amount

                customer_idem = _idem_key()
                db.add(WalletTransaction(
                    wallet_id=wallet.id,
                    transaction_type=TransactionType.PAYMENT,
                    amount=order.total_amount,
                    balance_before=balance_before,
                    balance_after=wallet.balance,
                    status=TransactionStatus.COMPLETED,
                    description=f"Payment for order #{str(order.id)[:8].upper()}",
                    # Blueprint §14: external_reference
                    external_reference=f"prod_debit_{order.id}",
                    # Blueprint §5.6 HARD RULE
                    idempotency_key=customer_idem,
                    # Blueprint §16.4 HARD RULE
                    completed_at=_utcnow(),
                ))

                # Credit each vendor (vendor_subtotal - business_fee ₦50)
                business_fee = PLATFORM_FEE_PER_SIDE
                for v_id, vendor_amount in vendor_subtotals.items():
                    credit_amount = max(vendor_amount - business_fee, Decimal("0.00"))
                    ProductService._credit_business_wallet(
                        db,
                        vendor_id=v_id,
                        amount=credit_amount,
                        order_id=order.id,
                        description=f"Sale payment order #{str(order.id)[:8].upper()}",
                        idempotency_key=_idem_key(),
                        external_reference=f"prod_credit_{v_id}_{order.id}",
                    )

            except (InsufficientBalanceException, ValidationException):
                raise
            except Exception as exc:
                logger.error(
                    "Checkout payment failed for order %s: %s",
                    order_id_snapshot, exc, exc_info=True,
                )
                db.rollback()

                # Restore stock
                for item_data in items:
                    product_crud.restore_stock(
                        db,
                        product_id=item_data["product_id"],
                        variant_id=item_data.get("variant_id"),
                        quantity=item_data["quantity"],
                    )

                cancelled_order = product_order_crud.get(db, id=order_id_snapshot)
                if cancelled_order:
                    cancelled_order.order_status = OrderStatusEnum.CANCELLED
                db.commit()

                raise ValidationException(
                    "Payment failed — order cancelled and stock restored."
                ) from exc

            order.payment_status  = PaymentStatusEnum.PAID
            order.order_status    = OrderStatusEnum.PROCESSING
            db.commit()
            db.refresh(order)

        return order


product_service = ProductService()