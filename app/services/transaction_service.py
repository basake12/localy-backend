"""
app/services/transaction_service.py

Unified payment transaction processor. Blueprint §5.4 / §5.6.

FIXES vs previous version:
  1.  [FINANCIAL ERROR — CRITICAL] Platform fee structure corrected.
      Blueprint §5.4:
        "Product/food orders: ₦50 flat — ₦50 from business + ₦50 from customer"
        "Debit customer wallet: total_amount (product price + customer fee)"
        "Credit business wallet: product_price - business_fee"
        "Credit platform_revenue_pool: customer_fee + business_fee"

      Previous code deducted ONE ₦50 from gross_amount — platform only
      earned ₦50 per product order (wrong). Platform must earn ₦100
      (₦50 from each side).

      Corrected function signature:
        process_payment(product_price, transaction_type, ...)
      where product_price is the BASE price (before fees). The function
      internally calculates customer_fee and business_fee and:
        - Debits customer: product_price + customer_fee
        - Credits business: product_price - business_fee
        - Records revenue: customer_fee + business_fee

  2.  [FINANCIAL INTEGRITY §5.6] idempotency_key generated and passed to
      every wallet_crud.credit_wallet() and debit_wallet() call.
      Blueprint §5.6 HARD RULE: "All external payment operations use
      idempotency keys."

  3.  [HARD RULE §16.4] All datetime.utcnow() replaced with
      datetime.now(timezone.utc).

  4.  Fee structure for tickets: ₦50 from customer ONLY (no business fee).
      Blueprint §5.4: "Ticket purchases: ₦50 flat — from customer only."

PLATFORM FEE STRUCTURE (Blueprint §5.4):
  Product / food orders:     customer_fee=₦50, business_fee=₦50, total=₦100
  Service / hotel / health:  customer_fee=₦100, business_fee=₦100, total=₦200
  Ticket purchases:          customer_fee=₦50, business_fee=₦0, total=₦50

  customer_total  = product_price + customer_fee    (what customer pays)
  business_net    = product_price - business_fee    (what business receives)
  platform_takes  = customer_fee + business_fee     (platform revenue)
  gross_amount    = customer_total                  (for PlatformRevenue record)
"""
import logging
import uuid as _uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Tuple
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.wallet_crud import (
    generate_idempotency_key,
    platform_revenue_crud,
    wallet_crud,
    wallet_transaction_crud,
)
from app.models.wallet_model import (
    PlatformRevenue,
    TransactionType,
    WalletTransaction,
)
from app.core.exceptions import (
    InsufficientBalanceException,
    NotFoundException,
    ValidationException,
)

logger = logging.getLogger(__name__)

# ─── Fee constants — Blueprint §5.4 ──────────────────────────────────────────
# Per-side fees (charged to BOTH customer and business independently).

STANDARD_FEE_PER_SIDE = Decimal("50")    # product/food: ₦50 each side
BOOKING_FEE_PER_SIDE  = Decimal("100")   # service/hotel/health: ₦100 each side
TICKET_FEE_CUSTOMER   = Decimal("50")    # tickets: ₦50 from customer only
TICKET_FEE_BUSINESS   = Decimal("0")     # tickets: no business fee


class TransactionService:
    """
    Centralized payment transaction processor.

    ALL payment flows MUST use process_payment() to ensure:
    1. Platform fees are calculated and charged correctly (both sides)
    2. Revenue is tracked with full audit trail
    3. Transactions are atomic (all-or-nothing)
    4. Idempotency is enforced with unique keys
    """

    # ═══════════════════════════════════════════════════════════════════════
    # FEE CALCULATION — Blueprint §5.4
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def get_fees(transaction_type: str) -> Tuple[Decimal, Decimal]:
        """
        Return (customer_fee, business_fee) for a given transaction type.

        Blueprint §5.4:
          Product / food:    (₦50, ₦50)
          Service / hotel / health / booking: (₦100, ₦100)
          Ticket:            (₦50, ₦0)
        """
        t = transaction_type.lower()

        # Ticket: customer only, no business fee
        if "ticket" in t:
            return TICKET_FEE_CUSTOMER, TICKET_FEE_BUSINESS

        # Bookings: ₦100 per side
        if any(kw in t for kw in ("hotel", "booking", "service", "health", "appointment")):
            return BOOKING_FEE_PER_SIDE, BOOKING_FEE_PER_SIDE

        # Standard (product, food order): ₦50 per side
        return STANDARD_FEE_PER_SIDE, STANDARD_FEE_PER_SIDE

    @staticmethod
    def get_fee_breakdown(
        product_price: Decimal,
        transaction_type: str,
    ) -> dict:
        """
        Calculate fee breakdown for display at checkout.
        Blueprint §5.4: "All fees shown transparently in checkout summary
        before user confirms."
        """
        customer_fee, business_fee = TransactionService.get_fees(transaction_type)
        return {
            "product_price":   product_price,
            "customer_fee":    customer_fee,
            "business_fee":    business_fee,
            "customer_total":  product_price + customer_fee,
            "business_net":    product_price - business_fee,
            "platform_total":  customer_fee + business_fee,
            "currency":        "NGN",
            "transaction_type": transaction_type,
        }

    # Backward-compat alias
    @staticmethod
    def calculate_platform_fee(transaction_type: str) -> Decimal:
        """Returns customer_fee (per-side). Use get_fees() for full breakdown."""
        customer_fee, _ = TransactionService.get_fees(transaction_type)
        return customer_fee

    # ═══════════════════════════════════════════════════════════════════════
    # PAYMENT PROCESSING — Blueprint §5.4 Middleware
    # ═══════════════════════════════════════════════════════════════════════

    async def process_payment(
        self,
        db: AsyncSession,
        *,
        customer_id: UUID,
        business_id: UUID,
        product_price: Decimal,        # BASE price (before any fees)
        transaction_type: str,
        description: str,
        reference: str,                # idempotency key (e.g. booking_id str)
        related_entity_id: Optional[UUID] = None,
        related_order_id: Optional[UUID] = None,
        related_booking_id: Optional[UUID] = None,
        metadata: Optional[dict] = None,
    ) -> Tuple[WalletTransaction, WalletTransaction, PlatformRevenue]:
        """
        Process a complete payment with Blueprint-correct two-sided fee deduction.

        Blueprint §5.4 Implementation:
          Step 1: customer_fee, business_fee = get_fees(transaction_type)
          Step 2: Debit customer wallet: product_price + customer_fee
          Step 3: Credit business wallet: product_price - business_fee
          Step 4: Credit platform_revenue_pool: customer_fee + business_fee
          Step 5: All in a single DB transaction — atomic. Rollback on failure.

        Args:
            product_price:    Base price of the product/service (before fees).
            transaction_type: "product_purchase" | "food_order" | "hotel_booking" |
                              "service_booking" | "health_appointment" | "ticket_sale"
            reference:        Unique idempotency key — safe to retry with same key.

        Returns:
            (customer_transaction, business_transaction, platform_revenue)
        """
        if product_price <= 0:
            raise ValidationException("Product price must be positive")

        # ── Idempotency check ─────────────────────────────────────────────────
        existing_revenue = await platform_revenue_crud.get_by_reference(
            db, reference=reference
        )
        if existing_revenue:
            logger.info("Duplicate payment (idempotent): %s", reference)
            customer_txn = await wallet_transaction_crud.get_by_idempotency_key(
                db, idempotency_key=reference
            )
            business_txn = await wallet_transaction_crud.get_by_idempotency_key(
                db, idempotency_key=f"{reference}_BUSINESS"
            )
            return customer_txn, business_txn, existing_revenue

        # ── Fee calculation — Blueprint §5.4 ──────────────────────────────────
        customer_fee, business_fee = self.get_fees(transaction_type)
        customer_total = product_price + customer_fee    # what customer pays
        business_net   = product_price - business_fee    # what business receives
        platform_total = customer_fee + business_fee     # platform revenue

        if business_net < 0:
            raise ValidationException(
                f"Product price ₦{product_price} is less than business fee ₦{business_fee}"
            )

        # ── Wallet lookup ─────────────────────────────────────────────────────
        customer_wallet = await wallet_crud.get_by_owner(db, owner_id=customer_id)
        if not customer_wallet:
            raise NotFoundException("Customer wallet not found")

        business_wallet = await wallet_crud.get_by_owner(db, owner_id=business_id)
        if not business_wallet:
            raise NotFoundException("Business wallet not found")

        try:
            # ── Step 2: Debit customer (product_price + customer_fee) ──────────
            customer_txn = await wallet_crud.debit_wallet(
                db,
                wallet_id=customer_wallet.id,
                amount=customer_total,
                transaction_type=TransactionType.PAYMENT,
                description=f"Payment: {description}",
                idempotency_key=reference,            # UNIQUE NOT NULL
                related_order_id=related_order_id,
                related_booking_id=related_booking_id,
                metadata={
                    **(metadata or {}),
                    "transaction_type": transaction_type,
                    "business_id":      str(business_id),
                    "product_price":    float(product_price),
                    "customer_fee":     float(customer_fee),
                    "customer_total":   float(customer_total),
                },
            )

            # ── Step 3: Credit business (product_price - business_fee) ─────────
            business_txn = await wallet_crud.credit_wallet(
                db,
                wallet_id=business_wallet.id,
                amount=business_net,
                transaction_type=TransactionType.CREDIT,
                description=f"Payment received: {description}",
                idempotency_key=f"{reference}_BUSINESS",    # UNIQUE NOT NULL
                related_order_id=related_order_id,
                related_booking_id=related_booking_id,
                metadata={
                    **(metadata or {}),
                    "transaction_type": transaction_type,
                    "customer_id":      str(customer_id),
                    "product_price":    float(product_price),
                    "business_fee":     float(business_fee),
                    "business_net":     float(business_net),
                },
            )

            # Flush so UUIDs are populated before revenue record FK references them
            await db.flush()

            # ── Step 4: Record platform revenue ──────────────────────────────
            # gross_amount = customer_total (what customer paid)
            # platform_fee = platform_total (what platform earns)
            # net_amount   = business_net   (what business received)
            revenue = await platform_revenue_crud.create_revenue_record(
                db,
                customer_transaction_id=customer_txn.id,
                business_transaction_id=business_txn.id,
                customer_id=customer_id,
                business_id=business_id,
                gross_amount=customer_total,
                platform_fee=platform_total,
                net_amount=business_net,
                transaction_type=transaction_type,
                transaction_reference=reference,
                related_entity_id=related_entity_id,
                description=description,
                metadata=metadata,
            )

            # ── Step 5: Commit all atomically ─────────────────────────────────
            await db.commit()
            await db.refresh(customer_txn)
            await db.refresh(business_txn)
            await db.refresh(revenue)

            logger.info(
                "Payment processed: %s | customer_paid=₦%s | business_net=₦%s | "
                "platform=₦%s",
                reference, customer_total, business_net, platform_total,
            )
            return customer_txn, business_txn, revenue

        except IntegrityError as exc:
            await db.rollback()
            logger.error("Payment IntegrityError: %s — %s", reference, exc)
            raise ValidationException("Payment failed — duplicate reference")
        except InsufficientBalanceException:
            await db.rollback()
            raise
        except Exception as exc:
            await db.rollback()
            logger.error("Payment error: %s — %s", reference, exc)
            raise ValidationException(f"Payment failed: {exc}")

    # ═══════════════════════════════════════════════════════════════════════
    # REFUND PROCESSING
    # ═══════════════════════════════════════════════════════════════════════

    async def process_refund(
        self,
        db: AsyncSession,
        *,
        original_reference: str,
        refund_reference: str,
        refund_amount: Optional[Decimal] = None,
        description: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> Tuple[WalletTransaction, WalletTransaction, PlatformRevenue]:
        """
        Refund a previous payment.

        Blueprint §5.1: "Refunds return to customer wallet within 24 hours
        of cancellation approval."
        Blueprint §5.4: Platform fee is NOT refundable unless the cancellation
        is due to a verified platform error.

        Full refund: customer receives back `business_net` (product_price - business_fee).
        Business pays back: `business_net` (exactly what they received).
        Platform retains: the platform fee.

        This is handled by Celery task `process_refund` (max 24h delay).
        """
        # Idempotency check
        existing = await platform_revenue_crud.get_by_reference(
            db, reference=refund_reference
        )
        if existing:
            logger.info("Duplicate refund (idempotent): %s", refund_reference)
            customer_txn = await wallet_transaction_crud.get_by_idempotency_key(
                db, idempotency_key=refund_reference
            )
            business_txn = await wallet_transaction_crud.get_by_idempotency_key(
                db, idempotency_key=f"{refund_reference}_BUSINESS"
            )
            return customer_txn, business_txn, existing

        # Find original revenue record
        original = await platform_revenue_crud.get_by_reference(
            db, reference=original_reference
        )
        if not original:
            raise NotFoundException(f"Original payment not found: {original_reference}")

        # Refund amounts — platform fee retained
        if refund_amount is None:
            customer_refund_amount = original.net_amount   # full refund = business_net
            business_debit_amount  = original.net_amount
        else:
            if refund_amount > original.net_amount:
                raise ValidationException(
                    "Refund amount exceeds refundable amount (gross minus platform fee)"
                )
            customer_refund_amount = refund_amount
            business_debit_amount  = refund_amount

        # Find original wallet transactions
        original_customer_txn = await wallet_transaction_crud.get_by_idempotency_key(
            db, idempotency_key=original_reference
        )
        original_business_txn = await wallet_transaction_crud.get_by_idempotency_key(
            db, idempotency_key=f"{original_reference}_BUSINESS"
        )
        if not original_customer_txn or not original_business_txn:
            raise ValidationException(
                f"Original wallet transactions not found for: {original_reference}"
            )

        try:
            # Credit customer
            customer_refund = await wallet_crud.credit_wallet(
                db,
                wallet_id=original_customer_txn.wallet_id,
                amount=customer_refund_amount,
                transaction_type=TransactionType.REFUND,
                description=description or f"Refund: {original.description}",
                idempotency_key=refund_reference,
                metadata={**(metadata or {}), "original_reference": original_reference},
            )

            # Debit business
            business_debit = await wallet_crud.debit_wallet(
                db,
                wallet_id=original_business_txn.wallet_id,
                amount=business_debit_amount,
                transaction_type=TransactionType.DEBIT,
                description=description or f"Refund issued: {original.description}",
                idempotency_key=f"{refund_reference}_BUSINESS",
                metadata={**(metadata or {}), "original_reference": original_reference},
            )

            await db.flush()

            reversed_revenue = await platform_revenue_crud.create_revenue_record(
                db,
                customer_transaction_id=customer_refund.id,
                business_transaction_id=business_debit.id,
                customer_id=original.customer_id,
                business_id=original.business_id,
                gross_amount=customer_refund_amount,
                platform_fee=Decimal("0"),          # platform retains its fee
                net_amount=business_debit_amount,
                transaction_type=f"{original.transaction_type}_refund",
                transaction_reference=refund_reference,
                related_entity_id=original.related_entity_id,
                description=description or f"Refund: {original.description}",
                metadata={**(metadata or {}), "original_reference": original_reference},
            )

            await db.commit()
            await db.refresh(customer_refund)
            await db.refresh(business_debit)
            await db.refresh(reversed_revenue)

            logger.info(
                "Refund processed: %s | customer=₦%s | business_debit=₦%s",
                refund_reference, customer_refund_amount, business_debit_amount,
            )
            return customer_refund, business_debit, reversed_revenue

        except Exception as exc:
            await db.rollback()
            logger.error("Refund error: %s — %s", refund_reference, exc)
            raise ValidationException(f"Refund failed: {exc}")


# Singleton
transaction_service = TransactionService()