"""
app/services/transaction_service.py

Unified payment transaction processor for Localy platform.

Per Blueprint Section 4.4 - Platform Fee Middleware:
- Centralizes ALL payment flows across all modules
- Automatically calculates and deducts platform fees
- Atomic transactions: customer debit + business credit + revenue tracking
- Idempotency protection via reference_id
- Supports refunds with automatic fee reversal

This service is the MANDATORY gateway for:
- Hotel bookings
- Food orders
- Service bookings
- Product purchases
- Health appointments
- Ticket sales

Fee structure:
- ₦50 flat fee: products, food orders, tickets
- ₦100 flat fee: hotel bookings, service bookings, health appointments

All transactions are atomic: if any step fails, entire transaction rolls back.
"""
import logging
from typing import Optional, Tuple
from decimal import Decimal
from uuid import UUID
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.crud.wallet_crud import (
    wallet_crud,
    wallet_transaction_crud,
    platform_revenue_crud,
)
from app.models.wallet_model import (
    WalletTransaction,
    PlatformRevenue,
    TransactionType,
)
from app.core.exceptions import (
    NotFoundException,
    InsufficientBalanceException,
    ValidationException,
)
from app.core.constants import (
    PLATFORM_FEE_STANDARD,
    PLATFORM_FEE_BOOKING,
    PLATFORM_FEE_TICKET,
)

logger = logging.getLogger(__name__)


class TransactionService:
    """
    Centralized payment transaction processor.
    
    All payment flows MUST go through this service to ensure:
    1. Platform fees are deducted automatically
    2. Revenue is tracked for analytics
    3. Transactions are atomic (all-or-nothing)
    4. Idempotency is enforced
    """

    # ═══════════════════════════════════════════════════════════════════════
    # PAYMENT PROCESSING
    # ═══════════════════════════════════════════════════════════════════════

    async def process_payment(
        self,
        db: AsyncSession,
        *,
        customer_id: UUID,
        business_id: UUID,
        gross_amount: Decimal,
        transaction_type: str,
        description: str,
        reference: str,
        related_entity_id: Optional[UUID] = None,
        metadata: Optional[dict] = None,
    ) -> Tuple[WalletTransaction, WalletTransaction, PlatformRevenue]:
        """
        Process a complete payment transaction with automatic fee deduction.
        
        This is an ATOMIC operation that:
        1. Debits customer wallet (gross amount)
        2. Calculates platform fee based on transaction type
        3. Credits business wallet (gross - fee)
        4. Records platform revenue
        
        All steps occur in a single database transaction. If any step fails,
        the entire operation rolls back.
        
        Args:
            customer_id: UUID of the paying customer
            business_id: UUID of the business receiving payment
            gross_amount: Total amount customer pays (₦)
            transaction_type: One of: hotel_booking, food_order, service_booking,
                            product_purchase, ticket_sale, health_appointment
            description: Human-readable description
            reference: Unique idempotency key (e.g., booking_id, order_id)
            related_entity_id: Optional ID of the booking/order/purchase
            metadata: Optional additional context
        
        Returns:
            Tuple of (customer_transaction, business_transaction, platform_revenue)
        
        Raises:
            ValidationException: If gross_amount <= 0 or net_amount <= 0
            InsufficientBalanceException: If customer wallet balance < gross_amount
            NotFoundException: If customer or business wallet not found
        """
        # Validate amount
        if gross_amount <= 0:
            raise ValidationException("Gross amount must be positive")

        # Check idempotency - if this reference already processed, return existing
        existing_revenue = await platform_revenue_crud.get_by_reference(
            db, reference=reference
        )
        if existing_revenue:
            logger.info(f"Duplicate payment attempt: {reference}")
            # Fetch and return the original transactions
            customer_txn = await wallet_transaction_crud.get(
                db, id=existing_revenue.customer_transaction_id
            )
            business_txn = await wallet_transaction_crud.get(
                db, id=existing_revenue.business_transaction_id
            )
            return customer_txn, business_txn, existing_revenue

        # Calculate platform fee
        platform_fee = self.calculate_platform_fee(transaction_type)
        net_amount = gross_amount - platform_fee

        if net_amount <= 0:
            raise ValidationException(
                f"Net amount after ₦{platform_fee} fee must be positive"
            )

        # Get wallets
        customer_wallet = await wallet_crud.get_by_user(db, user_id=customer_id)
        if not customer_wallet:
            raise NotFoundException("Customer wallet")

        business_wallet = await wallet_crud.get_by_user(db, user_id=business_id)
        if not business_wallet:
            raise NotFoundException("Business wallet")

        try:
            # Step 1: Debit customer wallet (gross amount)
            customer_transaction = await wallet_crud.debit_wallet(
                db,
                wallet_id=customer_wallet.id,
                amount=gross_amount,
                transaction_type=TransactionType.PAYMENT,
                description=f"Payment: {description}",
                reference_id=reference,
                metadata={
                    **(metadata or {}),
                    "transaction_type": transaction_type,
                    "business_id": str(business_id),
                    "gross_amount": float(gross_amount),
                    "platform_fee": float(platform_fee),
                },
            )

            # Step 2: Credit business wallet (net amount)
            business_transaction = await wallet_crud.credit_wallet(
                db,
                wallet_id=business_wallet.id,
                amount=net_amount,
                transaction_type=TransactionType.CREDIT,
                description=f"Payment received: {description}",
                reference_id=f"{reference}_BUSINESS",
                metadata={
                    **(metadata or {}),
                    "transaction_type": transaction_type,
                    "customer_id": str(customer_id),
                    "gross_amount": float(gross_amount),
                    "platform_fee": float(platform_fee),
                    "net_amount": float(net_amount),
                },
            )

            # Step 3: Record platform revenue.
            # Flush first so customer_transaction.id and business_transaction.id
            # are populated (server-generated UUIDs are NULL until flushed).
            await db.flush()

            revenue_record = await platform_revenue_crud.create_revenue_record(
                db,
                customer_transaction_id=customer_transaction.id,
                business_transaction_id=business_transaction.id,
                customer_id=customer_id,
                business_id=business_id,
                gross_amount=gross_amount,
                platform_fee=platform_fee,
                net_amount=net_amount,
                transaction_type=transaction_type,
                transaction_reference=reference,
                related_entity_id=related_entity_id,
                description=description,
                metadata=metadata,
            )

            # Commit all changes atomically
            await db.commit()
            await db.refresh(customer_transaction)
            await db.refresh(business_transaction)
            await db.refresh(revenue_record)

            logger.info(
                f"Payment processed: {reference} | "
                f"Customer: ₦{gross_amount} | "
                f"Business: ₦{net_amount} | "
                f"Fee: ₦{platform_fee}"
            )

            return customer_transaction, business_transaction, revenue_record

        except IntegrityError as e:
            await db.rollback()
            logger.error(f"Payment integrity error: {reference} - {str(e)}")
            raise ValidationException("Payment processing failed - duplicate reference")

        except InsufficientBalanceException:
            await db.rollback()
            raise

        except Exception as e:
            await db.rollback()
            logger.error(f"Payment processing error: {reference} - {str(e)}")
            raise ValidationException(f"Payment processing failed: {str(e)}")

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
    ) -> Tuple[WalletTransaction, WalletTransaction, Optional[PlatformRevenue]]:
        """
        Process a refund for a previous payment.

        This operation:
        1. Finds the original payment transaction
        2. Credits customer wallet (refund full or partial gross amount)
        3. Debits business wallet (corresponding net amount)
        4. Reverses platform revenue if full refund

        Args:
            original_reference: Reference of the original payment
            refund_reference: Unique reference for this refund
            refund_amount: Optional partial refund amount (defaults to full)
            description: Optional refund reason
            metadata: Optional additional context

        Returns:
            Tuple of (customer_refund, business_debit, reversed_revenue_or_none)
        """
        # Check refund idempotency
        existing_refund = await platform_revenue_crud.get_by_reference(
            db, reference=refund_reference
        )
        if existing_refund:
            logger.info(f"Duplicate refund attempt: {refund_reference}")
            customer_txn = await wallet_transaction_crud.get(
                db, id=existing_refund.customer_transaction_id
            )
            business_txn = await wallet_transaction_crud.get(
                db, id=existing_refund.business_transaction_id
            )
            return customer_txn, business_txn, existing_refund

        # Find original revenue record
        original_revenue = await platform_revenue_crud.get_by_reference(
            db, reference=original_reference
        )
        if not original_revenue:
            raise NotFoundException(
                f"Original payment not found: {original_reference}"
            )

        # Determine refund amounts.
        #
        # Blueprint §4.4: "Platform fee is non-refundable even if a booking
        # is cancelled."
        #
        # Full refund flow:
        #   Customer receives back:  net_amount  (gross - platform_fee)
        #   Business pays back:      net_amount  (exactly what they received)
        #   Platform retains:        platform_fee (already in PlatformRevenue)
        #
        # Both parties effectively bear the platform fee — the customer does
        # not recover it, and the business returns only what they were credited.
        if refund_amount is None:
            # Full refund — platform fee is kept, not returned to customer
            customer_refund_amount = original_revenue.net_amount   # gross - fee
            business_debit_amount  = original_revenue.net_amount   # what they received
            fee_refund             = Decimal("0.00")               # platform retains
        else:
            # Partial refund — scale against net (already excludes the fee)
            if refund_amount > original_revenue.net_amount:
                raise ValidationException("Refund amount exceeds refundable amount (gross minus platform fee)")

            refund_ratio           = refund_amount / original_revenue.net_amount
            customer_refund_amount = refund_amount
            business_debit_amount  = original_revenue.net_amount * refund_ratio
            fee_refund             = Decimal("0.00")               # platform retains

        # Aliases used below for clarity
        gross_refund = customer_refund_amount
        net_refund   = business_debit_amount

        # Resolve original wallet transactions by their reference_id strings —
        # this avoids both the lazy-load problem (ORM relationship returns None
        # in async context) and any UUID type-cast issues with FK column lookups.
        # The reference strings are deterministic: they were set when
        # process_payment() called debit_wallet / credit_wallet.
        original_customer_txn = await wallet_transaction_crud.get_by_reference(
            db, reference_id=original_reference
        )
        original_business_txn = await wallet_transaction_crud.get_by_reference(
            db, reference_id=f"{original_reference}_BUSINESS"
        )
        if not original_customer_txn or not original_business_txn:
            raise ValidationException(
                f"Original wallet transactions not found for refund '{original_reference}'. "
                "Ensure the original payment was processed successfully."
            )

        try:
            # Step 1: Credit customer wallet (gross refund)
            customer_refund = await wallet_crud.credit_wallet(
                db,
                wallet_id=original_customer_txn.wallet_id,
                amount=gross_refund,
                transaction_type=TransactionType.REFUND,
                description=description or f"Refund: {original_revenue.description}",
                reference_id=refund_reference,
                metadata={
                    **(metadata or {}),
                    "original_reference": original_reference,
                    "original_gross": float(original_revenue.gross_amount),
                    "refund_gross": float(gross_refund),
                },
            )

            # Step 2: Debit business wallet (net refund)
            business_debit = await wallet_crud.debit_wallet(
                db,
                wallet_id=original_business_txn.wallet_id,
                amount=net_refund,
                transaction_type=TransactionType.DEBIT,
                description=description or f"Refund issued: {original_revenue.description}",
                reference_id=f"{refund_reference}_BUSINESS",
                metadata={
                    **(metadata or {}),
                    "original_reference": original_reference,
                    "refund_net": float(net_refund),
                },
            )

            # Step 3: Record reversed revenue.
            #
            # FIX: The platform_revenue table has a non_negative_net_amount
            # CHECK constraint — it rejects negative values.  Store absolute
            # (positive) amounts; the transaction_type already identifies this
            # as a refund/reversal.
            #
            # FIX: flush before reading .id — WalletTransaction PKs are
            # server-generated UUIDs so they are NULL until the row is flushed.
            # Without flush, business_transaction_id is None → IntegrityError.
            await db.flush()

            reversed_revenue = await platform_revenue_crud.create_revenue_record(
                db,
                customer_transaction_id=customer_refund.id,
                business_transaction_id=business_debit.id,
                customer_id=original_revenue.customer_id,
                business_id=original_revenue.business_id,
                gross_amount=gross_refund,   # positive — constraint forbids negative
                platform_fee=fee_refund,     # positive — type field marks as reversal
                net_amount=net_refund,       # positive
                transaction_type=f"{original_revenue.transaction_type}_refund",
                transaction_reference=refund_reference,
                related_entity_id=original_revenue.related_entity_id,
                description=description or f"Refund: {original_revenue.description}",
                metadata={
                    **(metadata or {}),
                    "original_reference": original_reference,
                    "is_reversal": True,
                },
            )

            await db.commit()
            await db.refresh(customer_refund)
            await db.refresh(business_debit)
            await db.refresh(reversed_revenue)

            logger.info(
                f"Refund processed: {refund_reference} | "
                f"Customer refund: ₦{gross_refund} | "
                f"Business debit: ₦{net_refund} | "
                f"Fee reversed: ₦{fee_refund}"
            )

            return customer_refund, business_debit, reversed_revenue

        except Exception as e:
            await db.rollback()
            logger.error(f"Refund processing error: {refund_reference} - {str(e)}")
            raise ValidationException(f"Refund processing failed: {str(e)}")

    # ═══════════════════════════════════════════════════════════════════════
    # FEE CALCULATION
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def calculate_platform_fee(transaction_type: str) -> Decimal:
        """
        Calculate platform fee based on transaction type.
        
        Per Blueprint Section 4.4:
        - ₦50: product_purchase, food_order, ticket_sale
        - ₦100: hotel_booking, service_booking, health_appointment
        
        Args:
            transaction_type: Type of transaction
        
        Returns:
            Platform fee amount (₦50 or ₦100)
        """
        # Normalize transaction type to lowercase for comparison
        txn_type_lower = transaction_type.lower()

        # ₦100 fee (bookings)
        booking_types = [
            "hotel_booking",
            "hotel",
            "service_booking",
            "service",
            "health_appointment",
            "health",
            "booking",
        ]

        if any(t in txn_type_lower for t in booking_types):
            return PLATFORM_FEE_BOOKING  # ₦100

        # ₦50 fee (standard transactions)
        # Includes: product_purchase, food_order, ticket_sale
        return PLATFORM_FEE_STANDARD  # ₦50

    @staticmethod
    def get_fee_breakdown(
        gross_amount: Decimal,
        transaction_type: str,
    ) -> dict:
        """
        Calculate fee breakdown for display before payment confirmation.
        
        Useful for checkout screens to show:
        - Amount to be charged
        - Platform fee
        - Amount business will receive
        
        Args:
            gross_amount: Total amount customer will pay
            transaction_type: Type of transaction
        
        Returns:
            Dict with gross_amount, platform_fee, net_amount
        """
        platform_fee = TransactionService.calculate_platform_fee(transaction_type)
        net_amount = gross_amount - platform_fee

        return {
            "gross_amount": gross_amount,
            "platform_fee": platform_fee,
            "net_amount": net_amount,
            "currency": "NGN",
        }


# Singleton
transaction_service = TransactionService()