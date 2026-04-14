"""
app/services/wallet_service.py

Wallet business logic service for Localy platform.

Per Blueprint v2.0 Section 4:
- Customer wallets are SPEND-ONLY (no withdrawals)
- Business/Rider wallets can withdraw
- Virtual account auto-funding via Monnify
- Card funding via Paystack
- Crypto funding via NOWPayments (USDT/BTC/ETH) with webhook confirmation
- Referral: ₦1,000 to referrer, ₦1,000 off for new user
- PIN required for transactions
- Platform fees deducted before crediting business wallet

INTEGRATION WITH TRANSACTION_SERVICE:
- For NEW payments from customers to businesses, use transaction_service.process_payment()
- wallet_service.credit_business_wallet() is now LEGACY and should only be used for:
  * Manual admin adjustments
  * Promotional credits
  * Non-standard business credits
- All standard payment flows (hotels, food, services, products, health, tickets)
  MUST use transaction_service for automatic fee deduction and revenue tracking

FIX (virtual account):
  get_user_wallet() now calls _provision_virtual_account() after wallet creation.
  Previously the wallet was created with account_number=None/bank_name=None
  and never populated, so the Flutter WalletCard had no bank transfer details.

FIX (withdrawal):
  process_payout() previously:
    1. Debited wallet
    2. Called Paystack (could fail → dirty debit+credit pair in history)
  Now:
    1. Validates bank account via Paystack resolve_account (fail fast, no wallet touch)
    2. Creates transfer recipient
    3. Debits wallet
    4. Initiates transfer (only reverses if this step fails)
  Result: wallet is only touched once we know the bank details are valid.
"""
import logging
from typing import Optional, Tuple, List
from decimal import Decimal
from uuid import UUID
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.crud.wallet_crud import wallet_crud, wallet_transaction_crud, crypto_top_up_crud
from app.models.wallet_model import (
    Wallet,
    WalletTransaction,
    CryptoTopUp,
    TransactionType,
    TransactionStatus,
    CryptoTopUpStatusEnum,
)
from app.models.user_model import User
from app.schemas.wallet_schema import WalletTransactionOut, CryptoTopUpOut
from app.core.exceptions import (
    NotFoundException,
    InsufficientBalanceException,
    ValidationException,
    ServiceUnavailableException,
)
from app.core.constants import (
    PLATFORM_FEE_STANDARD,
    PLATFORM_FEE_BOOKING,
    REFERRAL_BONUS_AMOUNT,
    REFERRAL_DISCOUNT_AMOUNT,
    MIN_WALLET_TOPUP,
    MAX_WALLET_TOPUP_DAILY,
    # FIX: import withdrawal limits so they come from the single source of truth
    # in constants.py — previously _check_daily_withdrawal_limit hardcoded
    # Decimal("1000000.00") instead of using these constants.
    MIN_WITHDRAWAL_AMOUNT,
    MAX_WITHDRAWAL_AMOUNT,
)

logger = logging.getLogger(__name__)


class WalletService:
    """Customer wallet operations - spend-only per Blueprint."""

    # ═══════════════════════════════════════════════════════════════════════
    # WALLET RETRIEVAL
    # ═══════════════════════════════════════════════════════════════════════

    async def get_user_wallet(
        self, db: AsyncSession, *, user_id: UUID
    ) -> Wallet:
        """
        Get or create wallet for user.

        FIX: After wallet creation, immediately try to provision a Monnify
        virtual account so the wallet's account_number and bank_name are
        populated. Monnify failure is non-fatal — the wallet is still returned
        with account_number=None and provisioning can be retried later.
        """
        wallet = await wallet_crud.get_by_user(db, user_id=user_id)
        if not wallet:
            wallet = await wallet_crud.create_wallet(db, user_id=user_id)
            await db.commit()
            await db.refresh(wallet)
            # Provision virtual account after first creation
            await self._provision_virtual_account(db, wallet=wallet, user_id=user_id)

        # Re-attempt provisioning if a previous attempt failed
        elif not wallet.account_number:
            await self._provision_virtual_account(db, wallet=wallet, user_id=user_id)

        return wallet

    async def _provision_virtual_account(
        self,
        db: AsyncSession,
        *,
        wallet: Wallet,
        user_id: UUID,
    ) -> None:
        """
        Reserve a Monnify dedicated virtual account and persist it on the wallet.

        Uses user_id as the account_reference — Monnify deduplicates on this,
        so calling this multiple times for the same user is safe.

        Failure is logged but NOT raised — wallet creation must not fail just
        because Monnify is temporarily unavailable.
        """
        from app.services.monnify_service import monnify_service
        from sqlalchemy.orm import selectinload

        try:
            # FIX: Load user WITH profile relationships eagerly.
            # Using db.get(User, user_id) returns the user but leaves
            # customer_profile/business/rider as unloaded lazy refs.
            # Accessing them in an async context raises:
            #   "greenlet_spawn has not been called; can't call await_only()"
            # selectinload fetches the relationships in the same query call,
            # so _build_account_name() can access them without triggering lazy loads.
            result = await db.execute(
                select(User)
                .options(
                    selectinload(User.customer_profile),
                    selectinload(User.business),
                    selectinload(User.rider),
                )
                .where(User.id == user_id)
            )
            user = result.scalars().first()
            if user is None:
                logger.warning(f"Cannot provision virtual account: user {user_id} not found")
                return

            # Build a human-readable account name
            account_name = _build_account_name(user)
            account_reference = str(user_id)  # stable, unique per user

            response = await monnify_service.reserve_virtual_account(
                account_reference=account_reference,
                account_name=account_name,
                customer_email=user.email or "",
                customer_name=account_name,
            )

            # Monnify with getAllAvailableBanks=True returns accounts as a list.
            # Fall back to top-level fields for single-bank responses.
            account_number = response.get("accountNumber")
            bank_name      = response.get("bankName")

            if not account_number and response.get("accounts"):
                # Pick the first available bank account from the list
                first = response["accounts"][0]
                account_number = first.get("accountNumber")
                bank_name      = first.get("bankName")
                logger.info(
                    f"Monnify returned {len(response['accounts'])} bank(s) — "
                    f"using: {bank_name} ({account_number})"
                )

            if account_number:
                wallet.account_number = account_number
                wallet.bank_name      = bank_name or "Monnify"
                await db.commit()
                await db.refresh(wallet)
                logger.info(
                    f"Virtual account provisioned: user={user_id} "
                    f"account={account_number} bank={bank_name}"
                )
            else:
                logger.warning(
                    f"Monnify returned no account number for user={user_id}: {response}"
                )

        except Exception as exc:
            # Non-fatal — log and continue. Wallet is usable for card top-ups.
            logger.warning(
                f"Monnify virtual account provisioning failed for user={user_id}: {exc}"
            )

    async def get_balance(
        self, db: AsyncSession, *, user_id: UUID
    ) -> Decimal:
        """Get current wallet balance."""
        wallet = await self.get_user_wallet(db, user_id=user_id)
        return wallet.balance

    # ═══════════════════════════════════════════════════════════════════════
    # FUNDING (TOP-UP)
    # ═══════════════════════════════════════════════════════════════════════

    async def credit_wallet(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        amount_ngn: Decimal,
        description: str,
        reference: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> WalletTransaction:
        """
        Credit user's wallet (idempotent by reference).

        Per Blueprint: Used for top-ups, refunds, referral bonuses, cashback.
        Validates against daily funding limit.
        """
        # Validate amount
        if amount_ngn < MIN_WALLET_TOPUP:
            raise ValidationException(
                f"Minimum top-up amount is ₦{MIN_WALLET_TOPUP}"
            )

        # Get wallet
        wallet = await self.get_user_wallet(db, user_id=user_id)

        # Check idempotency (reference must be unique)
        if reference:
            existing = await wallet_transaction_crud.get_by_reference(
                db, reference_id=reference
            )
            if existing:
                logger.info(f"Duplicate credit attempt: {reference}")
                return existing

        # Check daily funding limit
        await self._check_daily_funding_limit(db, wallet_id=wallet.id, amount=amount_ngn)

        # Credit wallet
        transaction = await wallet_crud.credit_wallet(
            db,
            wallet_id=wallet.id,
            amount=amount_ngn,
            transaction_type=TransactionType.TOP_UP,
            description=description,
            reference_id=reference,
            metadata=metadata,
        )

        await db.commit()
        await db.refresh(transaction)
        return transaction

    async def _check_daily_funding_limit(
        self, db: AsyncSession, *, wallet_id: UUID, amount: Decimal
    ) -> None:
        """Validate daily funding limit (₦500,000 per Blueprint)."""
        from sqlalchemy import func

        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        result = await db.execute(
            select(func.sum(WalletTransaction.amount))
            .where(
                WalletTransaction.wallet_id == wallet_id,
                WalletTransaction.transaction_type == TransactionType.TOP_UP,
                WalletTransaction.status == TransactionStatus.COMPLETED,
                WalletTransaction.created_at >= today_start,
            )
        )
        today_total = result.scalar() or Decimal("0")

        if today_total + amount > MAX_WALLET_TOPUP_DAILY:
            raise ValidationException(
                f"Daily funding limit of ₦{MAX_WALLET_TOPUP_DAILY:,.0f} exceeded"
            )

    # ═══════════════════════════════════════════════════════════════════════
    # SPENDING (DEBIT)
    # ═══════════════════════════════════════════════════════════════════════

    async def debit_wallet(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        amount_ngn: Decimal,
        description: str,
        reference: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> WalletTransaction:
        """
        Debit user's wallet for payments.

        Per Blueprint: Used for booking payments, product purchases, service payments.
        Throws InsufficientBalanceException if balance too low.
        """
        wallet = await self.get_user_wallet(db, user_id=user_id)

        # Check idempotency
        if reference:
            existing = await wallet_transaction_crud.get_by_reference(
                db, reference_id=reference
            )
            if existing:
                logger.info(f"Duplicate debit attempt: {reference}")
                return existing

        # Debit wallet (will raise InsufficientBalanceException if balance < amount)
        transaction = await wallet_crud.debit_wallet(
            db,
            wallet_id=wallet.id,
            amount=amount_ngn,
            transaction_type=TransactionType.PAYMENT,
            description=description,
            reference_id=reference,
            metadata=metadata,
        )

        await db.commit()
        await db.refresh(transaction)
        return transaction

    # ═══════════════════════════════════════════════════════════════════════
    # REFUNDS
    # ═══════════════════════════════════════════════════════════════════════

    async def refund_wallet(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        amount_ngn: Decimal,
        description: str,
        reference: Optional[str] = None,
    ) -> WalletTransaction:
        """
        Refund to wallet (instant per Blueprint).

        Used for cancelled bookings/orders.
        """
        wallet = await self.get_user_wallet(db, user_id=user_id)

        transaction = await wallet_crud.credit_wallet(
            db,
            wallet_id=wallet.id,
            amount=amount_ngn,
            transaction_type=TransactionType.REFUND,
            description=description,
            reference_id=reference,
        )

        await db.commit()
        await db.refresh(transaction)
        return transaction

    # ═══════════════════════════════════════════════════════════════════════
    # REFERRAL REWARDS (₦1,000 per Blueprint)
    # ═══════════════════════════════════════════════════════════════════════

    async def credit_referral_bonus(
        self,
        db: AsyncSession,
        *,
        referrer_user_id: UUID,
        referred_user_id: UUID,
        first_order_amount: Decimal,
    ) -> Optional[WalletTransaction]:
        """
        Credit referral bonus to referrer when referred user completes first order.

        Per Blueprint Section 6.1:
        - ₦1,000 to referrer
        - Only triggers once per referred user
        - First order must be >₦2,000
        """
        from app.core.constants import REFERRAL_MINIMUM_ORDER

        if first_order_amount < REFERRAL_MINIMUM_ORDER:
            logger.info(
                f"First order ₦{first_order_amount} < ₦{REFERRAL_MINIMUM_ORDER} - no bonus"
            )
            return None

        reference = f"REFERRAL_{referred_user_id}"

        # Check if already credited (idempotency)
        existing = await wallet_transaction_crud.get_by_reference(
            db, reference_id=reference
        )
        if existing:
            logger.info(f"Referral bonus already credited: {reference}")
            return existing

        wallet = await self.get_user_wallet(db, user_id=referrer_user_id)

        transaction = await wallet_crud.credit_wallet(
            db,
            wallet_id=wallet.id,
            amount=REFERRAL_BONUS_AMOUNT,
            transaction_type=TransactionType.REFERRAL_BONUS,
            description=f"Referral bonus for user {referred_user_id}",
            reference_id=reference,
            metadata={
                "referred_user_id": str(referred_user_id),
                "first_order_amount": float(first_order_amount),
            },
        )

        await db.commit()
        await db.refresh(transaction)
        return transaction

    # ═══════════════════════════════════════════════════════════════════════
    # BUSINESS WALLET OPERATIONS
    # ═══════════════════════════════════════════════════════════════════════

    async def credit_business_wallet(
        self,
        db: AsyncSession,
        *,
        business_user_id: UUID,
        gross_amount: Decimal,
        platform_fee: Decimal,
        description: str,
        reference: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> WalletTransaction:
        """
        LEGACY METHOD - Credit business wallet with platform fee already deducted.

        ⚠️ DEPRECATION NOTICE:
        This method is LEGACY and should ONLY be used for:
        - Manual admin wallet adjustments
        - Promotional business credits
        - Non-standard business credits

        For ALL standard payment flows (hotels, food, services, products, health, tickets),
        use transaction_service.process_payment() instead. It handles:
        - Customer wallet debit
        - Automatic platform fee calculation and deduction
        - Business wallet credit
        - Platform revenue tracking
        - All in one atomic transaction

        Args:
            business_user_id: Business receiving the payment
            gross_amount: Total amount before fee deduction
            platform_fee: Fee to deduct (₦50 or ₦100)
        """
        net_amount = gross_amount - platform_fee

        if net_amount <= 0:
            raise ValidationException("Net amount after fee must be positive")

        wallet = await self.get_user_wallet(db, user_id=business_user_id)

        # Check idempotency
        if reference:
            existing = await wallet_transaction_crud.get_by_reference(
                db, reference_id=reference
            )
            if existing:
                logger.info(f"Duplicate business credit: {reference}")
                return existing

        # Credit business wallet with net amount
        transaction = await wallet_crud.credit_wallet(
            db,
            wallet_id=wallet.id,
            amount=net_amount,
            transaction_type=TransactionType.CREDIT,
            description=description,
            reference_id=reference,
            metadata={
                **(metadata or {}),
                "gross_amount": float(gross_amount),
                "platform_fee": float(platform_fee),
                "net_amount": float(net_amount),
            },
        )

        await db.commit()
        await db.refresh(transaction)
        return transaction

    # ═══════════════════════════════════════════════════════════════════════
    # BUSINESS WITHDRAWAL
    # ═══════════════════════════════════════════════════════════════════════

    async def process_payout(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        amount_ngn: Decimal,
        bank_account: str,
        bank_code: str,
        recipient_name: str,
        description: Optional[str] = None,
    ) -> WalletTransaction:
        """
        Process withdrawal to bank account for Business/Rider.

        Per Blueprint Section 4.2:
        - Minimum withdrawal: ₦1,000
        - Maximum withdrawal: ₦1,000,000/day
        - PIN required (checked in route layer)
        - Same-day/next business day processing

        FIX: Previously the wallet was debited BEFORE calling Paystack.
        If account resolution failed, a debit+credit pair was written to
        the transaction history (messy) and the error message was garbled
        due to ServiceUnavailableException receiving the wrong positional arg.

        New flow (fail-fast, wallet only touched after Paystack validates):
        1. Validate amount and daily limit
        2. Resolve bank account via Paystack (validate before touching wallet)
        3. Create Paystack transfer recipient
        4. Debit wallet (only reaches here if steps 2-3 succeeded)
        5. Initiate Paystack transfer
        6. If step 5 fails → reverse debit (credit back)
        """
        from app.services.payment_service import payment_service

        # ── 1. Amount validation ───────────────────────────────────────────
        # FIX: use constants instead of hardcoded values so a single change in
        # constants.py propagates to both the per-transaction and cumulative checks.
        if amount_ngn < MIN_WITHDRAWAL_AMOUNT:
            raise ValidationException(
                f"Minimum withdrawal amount is ₦{MIN_WITHDRAWAL_AMOUNT:,.0f}"
            )

        if amount_ngn > MAX_WITHDRAWAL_AMOUNT:
            raise ValidationException(
                f"Maximum withdrawal amount is ₦{MAX_WITHDRAWAL_AMOUNT:,.0f} per day"
            )

        await self._check_daily_withdrawal_limit(db, user_id=user_id, amount=amount_ngn)

        # ── 2. Validate bank account via Paystack BEFORE touching wallet ───
        try:
            resolve_response = await payment_service.resolve_account(
                account_number=bank_account,
                bank_code=bank_code,
            )
            resolved_name = (
                resolve_response.get("data", {}).get("account_name")
                or recipient_name
            )
        except ServiceUnavailableException as exc:
            # Surface Paystack's exact message (e.g. "Could not resolve account")
            raise ValidationException(f"Bank account validation failed: {exc.detail}")

        # ── 3. Create Paystack transfer recipient ──────────────────────────
        try:
            recipient_response = await payment_service.create_transfer_recipient(
                account_number=bank_account,
                bank_code=bank_code,
                name=resolved_name,
            )
            recipient_code = recipient_response.get("data", {}).get("recipient_code")
            if not recipient_code:
                raise ValidationException(
                    "Failed to create transfer recipient — no recipient code returned"
                )
        except ServiceUnavailableException as exc:
            raise ValidationException(f"Failed to register bank account: {exc.detail}")

        # ── 4. Debit wallet (only after bank account is confirmed valid) ────
        wallet = await self.get_user_wallet(db, user_id=user_id)

        if wallet.balance < amount_ngn:
            raise InsufficientBalanceException()

        transaction = await wallet_crud.debit_wallet(
            db,
            wallet_id=wallet.id,
            amount=amount_ngn,
            transaction_type=TransactionType.DEBIT,
            description=description or "Withdrawal to bank account",
            metadata={
                "bank_account":    bank_account,
                "bank_code":       bank_code,
                "recipient_name":  resolved_name,
                "recipient_code":  recipient_code,
                "withdrawal_type": "bank_transfer",
            },
        )
        # Commit debit before initiating transfer so we have a record
        # even if the transfer call times out
        await db.commit()
        await db.refresh(transaction)

        # ── 5. Initiate Paystack transfer (reverse on failure) ─────────────
        try:
            transfer_response = await payment_service.initiate_transfer(
                recipient_code=recipient_code,
                amount=amount_ngn,
                reason=description or "Wallet withdrawal",
            )

            # Persist transfer details on the transaction record
            transaction.meta_data = {
                **(transaction.meta_data or {}),
                "transfer_code":   transfer_response.get("data", {}).get("transfer_code"),
                "transfer_status": "pending",
            }
            await db.commit()
            await db.refresh(transaction)

        except Exception as exc:
            # Transfer initiation failed after wallet was debited.
            # Reverse the debit by crediting back immediately.
            logger.error(
                f"Transfer initiation failed for user={user_id} amount=₦{amount_ngn}: {exc}. "
                f"Reversing debit."
            )
            await wallet_crud.credit_wallet(
                db,
                wallet_id=wallet.id,
                amount=amount_ngn,
                transaction_type=TransactionType.REFUND,
                description=f"Withdrawal reversal — transfer failed: {exc}",
                reference_id=f"REVERSAL_{transaction.reference_id}",
            )
            await db.commit()
            raise ValidationException(
                f"Withdrawal could not be processed. Your funds have been returned to your wallet."
            )

        logger.info(
            f"Withdrawal initiated: user={user_id} amount=₦{amount_ngn} "
            f"account={bank_account} recipient={recipient_code}"
        )
        return transaction

    async def _check_daily_withdrawal_limit(
        self, db: AsyncSession, *, user_id: UUID, amount: Decimal
    ) -> None:
        """Validate daily withdrawal limit (₦1,000,000 per Blueprint)."""
        from sqlalchemy import func

        wallet = await self.get_user_wallet(db, user_id=user_id)
        today_start = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        result = await db.execute(
            select(func.sum(WalletTransaction.amount))
            .where(
                WalletTransaction.wallet_id == wallet.id,
                WalletTransaction.transaction_type == TransactionType.DEBIT,
                WalletTransaction.status == TransactionStatus.COMPLETED,
                WalletTransaction.created_at >= today_start,
            )
        )
        today_total = result.scalar() or Decimal("0")

        # FIX: use MAX_WITHDRAWAL_AMOUNT constant — was hardcoded Decimal("1000000.00")
        if today_total + amount > MAX_WITHDRAWAL_AMOUNT:
            raise ValidationException(
                f"Daily withdrawal limit of ₦{MAX_WITHDRAWAL_AMOUNT:,.0f} exceeded"
            )

    # ═══════════════════════════════════════════════════════════════════════
    # RIDER WALLET - DELIVERY EARNINGS
    # ═══════════════════════════════════════════════════════════════════════

    async def credit_rider_earnings(
        self,
        db: AsyncSession,
        *,
        rider_user_id: UUID,
        delivery_fee: Decimal,
        delivery_id: UUID,
        description: str,
    ) -> WalletTransaction:
        """
        Credit rider wallet after completed delivery.

        Per Blueprint Section 4.3: Riders receive full delivery fee.
        Platform fee already collected from customer/business.
        """
        wallet = await self.get_user_wallet(db, user_id=rider_user_id)

        transaction = await wallet_crud.credit_wallet(
            db,
            wallet_id=wallet.id,
            amount=delivery_fee,
            transaction_type=TransactionType.CREDIT,
            description=description,
            metadata={"delivery_id": str(delivery_id), "type": "delivery_earning"},
        )

        await db.commit()
        await db.refresh(transaction)
        return transaction

    # ═══════════════════════════════════════════════════════════════════════
    # PLATFORM FEE CALCULATION
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def calculate_platform_fee(transaction_type: str) -> Decimal:
        """
        Calculate platform fee based on transaction type.

        Per Blueprint Section 4.4:
        - ₦50: products, food, tickets
        - ₦100: hotel bookings, service bookings, health appointments

        NOTE: For new payment flows, use transaction_service.calculate_platform_fee()
        This method is kept for backward compatibility.
        """
        booking_types = ["hotel", "service", "health", "booking"]

        if any(t in transaction_type.lower() for t in booking_types):
            return PLATFORM_FEE_BOOKING   # ₦100
        else:
            return PLATFORM_FEE_STANDARD  # ₦50


    # ═══════════════════════════════════════════════════════════════════════
    # CRYPTO TOP-UP (NOWPayments)
    # ═══════════════════════════════════════════════════════════════════════

    async def initiate_crypto_top_up(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        ngn_amount: Decimal,
        crypto_currency: str = "USDT",
        crypto_network: str = "TRC20",
    ) -> CryptoTopUp:
        """
        Start a crypto top-up order via NOWPayments.

        Flow (Blueprint §4.1.1 — crypto channel):
        1. Fetch live exchange rate from NOWPayments
        2. Calculate exact crypto amount user must send
        3. Create a payment order on NOWPayments → get deposit address
        4. Persist CryptoTopUp record (status=PENDING)
        5. Return deposit address + expected amounts to client

        Wallet is NOT credited here — crediting happens in
        confirm_crypto_top_up() when the NOWPayments webhook fires.
        """
        from app.services.nowpayments_service import nowpayments_service
        import uuid as _uuid

        if ngn_amount < MIN_WALLET_TOPUP:
            raise ValidationException(
                f"Minimum top-up amount is ₦{MIN_WALLET_TOPUP}"
            )

        wallet = await self.get_user_wallet(db, user_id=user_id)

        # 1. Get NGN → crypto exchange rate
        rate_data = await nowpayments_service.get_exchange_rate(
            from_currency="NGN",
            to_currency=crypto_currency,
        )
        exchange_rate = Decimal(str(rate_data["rate"]))

        if exchange_rate <= 0:
            raise ValidationException("Unable to fetch exchange rate. Try again.")

        # 2. Calculate exact crypto amount
        expected_crypto = (ngn_amount / exchange_rate).quantize(Decimal("0.00000001"))

        # 3. Create NOWPayments payment order
        order_data = await nowpayments_service.create_payment(
            price_amount=float(ngn_amount),
            price_currency="NGN",
            pay_currency=crypto_currency,
            pay_amount=float(expected_crypto),
            order_id=str(_uuid.uuid4()),
            order_description=f"Localy wallet top-up ₦{ngn_amount}",
        )

        provider_order_id = str(order_data["payment_id"])
        deposit_address   = order_data["pay_address"]
        expires_at        = datetime.utcnow() + timedelta(minutes=20)

        # 4. Persist CryptoTopUp record
        crypto_top_up = await crypto_top_up_crud.create(
            db,
            wallet_id=wallet.id,
            crypto_currency=crypto_currency,
            crypto_network=crypto_network,
            deposit_address=deposit_address,
            expected_crypto=expected_crypto,
            expected_ngn=ngn_amount,
            exchange_rate=exchange_rate,
            provider_order_id=provider_order_id,
            expires_at=expires_at,
        )

        await db.commit()
        await db.refresh(crypto_top_up)

        logger.info(
            f"Crypto top-up initiated: user={user_id} "
            f"ngn={ngn_amount} crypto={expected_crypto} {crypto_currency} "
            f"order={provider_order_id}"
        )
        return crypto_top_up

    async def confirm_crypto_top_up(
        self,
        db: AsyncSession,
        *,
        provider_order_id: str,
        received_crypto: Decimal,
        confirmations: int = 0,
    ) -> Optional[WalletTransaction]:
        """
        Confirm a crypto payment and credit the wallet.

        Called exclusively by the NOWPayments webhook handler
        (wallet.py /crypto/webhook) when payment_status is
        'finished' or 'confirmed'.

        Idempotent — safe to call multiple times for same order.
        Only credits wallet once (COMPLETED guard).

        Partial payment handling:
        - If received_crypto >= 95% of expected → credit full NGN amount
        - If received_crypto < 95%              → mark UNDERPAID, do NOT credit
        """
        # Fetch the pending CryptoTopUp record
        crypto_top_up = await crypto_top_up_crud.get_by_provider_order_id(
            db, provider_order_id=provider_order_id
        )

        if not crypto_top_up:
            logger.warning(
                f"Crypto top-up not found for order: {provider_order_id}"
            )
            return None

        # Idempotency guard — already processed
        if crypto_top_up.status in (CryptoTopUpStatusEnum.COMPLETED, CryptoTopUpStatusEnum.COMPLETED.value):
            logger.info(f"Crypto top-up already completed: {provider_order_id}")
            existing_txn = await wallet_transaction_crud.get_by_reference(
                db, reference_id=f"CRYPTO_{provider_order_id}"
            )
            return existing_txn

        # Partial payment check (allow 5% tolerance for network fees)
        tolerance      = Decimal("0.95")
        minimum_crypto = crypto_top_up.expected_crypto * tolerance

        if received_crypto < minimum_crypto:
            logger.warning(
                f"Crypto underpayment: order={provider_order_id} "
                f"expected={crypto_top_up.expected_crypto} "
                f"received={received_crypto}"
            )
            await crypto_top_up_crud.update_status(
                db,
                crypto_top_up_id=crypto_top_up.id,
                status=CryptoTopUpStatusEnum.UNDERPAID,
                received_crypto=received_crypto,
                confirmations=confirmations,
            )
            await db.commit()
            return None

        # Credit the wallet
        wallet = await wallet_crud.get_by_id(db, wallet_id=crypto_top_up.wallet_id)
        if not wallet:
            raise NotFoundException("Wallet not found for crypto top-up")

        reference = f"CRYPTO_{provider_order_id}"

        transaction = await wallet_crud.credit_wallet(
            db,
            wallet_id=wallet.id,
            amount=crypto_top_up.expected_ngn,
            transaction_type=TransactionType.TOP_UP,
            description=(
                f"Crypto top-up: {received_crypto} {crypto_top_up.crypto_currency} "
                f"→ ₦{crypto_top_up.expected_ngn}"
            ),
            reference_id=reference,
            metadata={
                "provider_order_id": provider_order_id,
                "crypto_currency":   crypto_top_up.crypto_currency,
                "crypto_network":    crypto_top_up.crypto_network,
                "received_crypto":   float(received_crypto),
                "expected_crypto":   float(crypto_top_up.expected_crypto),
                "exchange_rate":     float(crypto_top_up.exchange_rate),
                "confirmations":     confirmations,
            },
        )

        # Mark CryptoTopUp as COMPLETED
        await crypto_top_up_crud.update_status(
            db,
            crypto_top_up_id=crypto_top_up.id,
            status=CryptoTopUpStatusEnum.COMPLETED,
            received_crypto=received_crypto,
            confirmations=confirmations,
        )

        await db.commit()
        await db.refresh(transaction)

        logger.info(
            f"Crypto top-up completed: order={provider_order_id} "
            f"credited=₦{crypto_top_up.expected_ngn} wallet={wallet.id}"
        )
        return transaction


    # ═══════════════════════════════════════════════════════════════════════
    # WALLET-TO-WALLET TRANSFER
    # ═══════════════════════════════════════════════════════════════════════

    async def transfer(
        self,
        db: AsyncSession,
        *,
        from_user_id: UUID,
        recipient_wallet_number: str,
        amount_ngn: Decimal,
        description: str = "Wallet transfer",
    ) -> Tuple[WalletTransaction, WalletTransaction]:
        """
        Transfer funds between two Localy wallets by wallet number.

        Blueprint §4.1.2 / §2.3:
        - PIN required for amounts > ₦5,000 (enforced at route layer via
          get_async_fully_verified_user dependency)
        - Both sender and recipient must have active wallets
        - Atomic: debit + credit happen in the same DB transaction
        - Idempotent reference: TRANSFER_{sender_wallet_id}_{timestamp_ms}
        - Sender cannot transfer to themselves

        Returns:
            Tuple[debit_transaction, credit_transaction]
        """
        import time

        if amount_ngn <= Decimal("0"):
            raise ValidationException("Transfer amount must be positive")

        if amount_ngn < Decimal("1"):
            raise ValidationException("Minimum transfer amount is ₦1")

        # Resolve sender wallet
        sender_wallet = await self.get_user_wallet(db, user_id=from_user_id)

        # Resolve recipient wallet by wallet number (e.g. LCY1234567)
        recipient_wallet = await wallet_crud.get_by_wallet_number(
            db, wallet_number=recipient_wallet_number
        )
        if not recipient_wallet:
            raise ValidationException(
                f"Wallet number '{recipient_wallet_number}' not found"
            )

        # Prevent self-transfer
        if sender_wallet.id == recipient_wallet.id:
            raise ValidationException("You cannot transfer to your own wallet")

        # Check sender has sufficient balance
        if sender_wallet.balance < amount_ngn:
            raise InsufficientBalanceException()

        # Generate shared reference for this transfer pair
        ref_base   = f"TRANSFER_{sender_wallet.id}_{int(time.time() * 1000)}"
        debit_ref  = f"{ref_base}_DEBIT"
        credit_ref = f"{ref_base}_CREDIT"

        # Idempotency — guard against duplicate requests
        existing_debit = await wallet_transaction_crud.get_by_reference(
            db, reference_id=debit_ref
        )
        if existing_debit:
            logger.info(f"Duplicate transfer attempt: {debit_ref}")
            existing_credit = await wallet_transaction_crud.get_by_reference(
                db, reference_id=credit_ref
            )
            return existing_debit, existing_credit

        # ── Atomic debit + credit ─────────────────────────────────────────

        # 1. Debit sender
        debit_txn = await wallet_crud.debit_wallet(
            db,
            wallet_id=sender_wallet.id,
            amount=amount_ngn,
            transaction_type=TransactionType.DEBIT,
            description=f"Transfer to {recipient_wallet_number}: {description}",
            reference_id=debit_ref,
            metadata={
                "transfer_type":           "outgoing",
                "recipient_wallet_number": recipient_wallet_number,
                "recipient_wallet_id":     str(recipient_wallet.id),
            },
        )

        # 2. Credit recipient
        credit_txn = await wallet_crud.credit_wallet(
            db,
            wallet_id=recipient_wallet.id,
            amount=amount_ngn,
            transaction_type=TransactionType.CREDIT,
            description=f"Transfer from {sender_wallet.wallet_number}: {description}",
            reference_id=credit_ref,
            metadata={
                "transfer_type":        "incoming",
                "sender_wallet_number": sender_wallet.wallet_number,
                "sender_wallet_id":     str(sender_wallet.id),
            },
        )

        await db.commit()
        await db.refresh(debit_txn)
        await db.refresh(credit_txn)

        logger.info(
            f"Transfer completed: ₦{amount_ngn} "
            f"from {sender_wallet.wallet_number} "
            f"to {recipient_wallet_number} "
            f"ref={ref_base}"
        )

        return debit_txn, credit_txn


    # ═══════════════════════════════════════════════════════════════════════
    # TRANSACTION HISTORY
    # ═══════════════════════════════════════════════════════════════════════

    async def get_transaction_history(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        transaction_type: Optional[TransactionType] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[WalletTransaction], int]:
        """
        Paginated transaction history for a user's wallet.

        Blueprint §4.1.2: Wallet history accessible from wallet screen.
        Supports filtering by transaction_type (top_up, payment, debit, etc.)
        """
        wallet = await self.get_user_wallet(db, user_id=user_id)
        return await wallet_transaction_crud.get_wallet_transactions(
            db,
            wallet_id=wallet.id,
            transaction_type=transaction_type,
            skip=skip,
            limit=limit,
        )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _build_account_name(user) -> str:
    """
    Build the display name for the Monnify virtual account.
    Tries customer_profile name first, then falls back to email/phone.
    """
    if hasattr(user, "customer_profile") and user.customer_profile:
        p = user.customer_profile
        name = f"{p.first_name or ''} {p.last_name or ''}".strip()
        if name:
            return name
    if hasattr(user, "business") and user.business:
        return user.business.business_name or "Localy Business"
    if hasattr(user, "rider") and user.rider:
        r = user.rider
        name = f"{r.first_name or ''} {r.last_name or ''}".strip()
        if name:
            return name
    # Ultimate fallback
    return (user.email or user.phone or str(user.id))[:50]


# Singleton
wallet_service = WalletService()