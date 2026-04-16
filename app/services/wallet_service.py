"""
app/services/wallet_service.py

FIXES vs previous version:
  1.  [HARD RULE §16.4] All datetime.utcnow() replaced with
      datetime.now(timezone.utc).

  2.  CryptoTopUp / NOWPayments methods DELETED.
      Blueprint §5: Monnify + Paystack ONLY. No crypto funding.

  3.  user.phone → user.phone_number throughout. Blueprint §14.

  4.  wallet_crud calls updated: user_id → owner_id (Blueprint §14 field).

  5.  WebSocket event + push notification added after wallet credit.
      Blueprint §5.1: "emit WebSocket event 'wallet_credited', send push
      notification 'Your wallet has been funded with ₦X,XXX'"

  6.  MIN_WALLET_TOPUP / MAX_WALLET_TOPUP_DAILY values now read from
      settings (corrected to ₦1,000 / ₦2,000,000 in config.py).

  7.  All wallet_crud debit/credit calls now supply idempotency_key.
      Blueprint §5.6 HARD RULE.

  8.  handle_monnify_funding() added — called by the Monnify webhook
      endpoint (POST /api/v1/webhooks/monnify/funding). This is the
      PRIMARY wallet funding mechanism per Blueprint §5.1.

  9.  _build_account_name() uses user.phone_number (not user.phone).
"""
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.wallet_crud import (
    generate_idempotency_key,
    platform_revenue_crud,
    wallet_crud,
    wallet_transaction_crud,
)
from app.models.wallet_model import (
    TransactionStatus,
    TransactionType,
    Wallet,
    WalletTransaction,
)
from app.models.user_model import User
from app.core.exceptions import (
    InsufficientBalanceException,
    NotFoundException,
    ValidationException,
    ServiceUnavailableException,
)
from app.config import settings

logger = logging.getLogger(__name__)


class WalletService:

    # ═══════════════════════════════════════════════════════════════════════
    # WALLET RETRIEVAL
    # ═══════════════════════════════════════════════════════════════════════

    async def get_user_wallet(
        self, db: AsyncSession, *, user_id: UUID
    ) -> Wallet:
        """
        Get wallet by owner_id. Provisions Monnify virtual account if missing.
        Blueprint §5.1: virtual account permanent, never changes.
        """
        wallet = await wallet_crud.get_by_owner(db, owner_id=user_id)
        if not wallet:
            # This path should rarely be hit — wallet is created by Celery
            # task create_wallet at registration. Defensive fallback only.
            wallet = await wallet_crud.create_wallet(
                db, owner_id=user_id, owner_type=await self._resolve_owner_type(db, user_id)
            )
            await db.commit()
            await db.refresh(wallet)
            await self._provision_virtual_account(db, wallet=wallet, user_id=user_id)
        elif not wallet.virtual_acct_number:
            await self._provision_virtual_account(db, wallet=wallet, user_id=user_id)
        return wallet

    async def _resolve_owner_type(self, db: AsyncSession, user_id: UUID) -> str:
        """Resolve 'customer'|'business'|'rider' from user's role field."""
        result = await db.execute(select(User).where(User.id == user_id))
        user   = result.scalars().first()
        if not user:
            return "customer"
        role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
        return role_val  # matches owner_type values exactly

    async def _provision_virtual_account(
        self,
        db: AsyncSession,
        *,
        wallet: Wallet,
        user_id: UUID,
    ) -> None:
        """
        Reserve a Monnify dedicated virtual account and store on wallet.
        Blueprint §5.1: "Assign_virtual_account task: calls Monnify API."
        Failure is non-fatal — wallet is usable for card top-ups immediately.
        """
        from app.services.monnify_service import monnify_service
        from sqlalchemy.orm import selectinload

        try:
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
            if not user:
                logger.warning("Cannot provision virtual account: user %s not found", user_id)
                return

            account_name      = _build_account_name(user)
            account_reference = str(user_id)   # stable, unique per user

            response = await monnify_service.reserve_virtual_account(
                account_reference=account_reference,
                account_name=account_name,
                customer_email=user.email or "",
                customer_name=account_name,
            )

            # Monnify may return accounts as a list (getAllAvailableBanks=True)
            account_number = response.get("accountNumber")
            bank_name      = response.get("bankName")
            acct_name      = response.get("accountName", account_name)
            acct_ref       = response.get("accountReference", account_reference)

            if not account_number and response.get("accounts"):
                first          = response["accounts"][0]
                account_number = first.get("accountNumber")
                bank_name      = first.get("bankName")

            if account_number:
                wallet.virtual_acct_number = account_number
                wallet.virtual_acct_name   = acct_name
                wallet.virtual_acct_bank   = bank_name or "Monnify"
                wallet.monnify_acct_ref    = acct_ref
                await db.commit()
                await db.refresh(wallet)
                logger.info(
                    "Virtual account provisioned: user=%s account=%s bank=%s",
                    user_id, account_number, bank_name,
                )
        except Exception as exc:
            logger.warning(
                "Monnify provisioning failed for user=%s: %s", user_id, exc
            )

    async def get_balance(self, db: AsyncSession, *, user_id: UUID) -> Decimal:
        wallet = await self.get_user_wallet(db, user_id=user_id)
        return wallet.balance

    # ═══════════════════════════════════════════════════════════════════════
    # MONNIFY WEBHOOK — PRIMARY FUNDING MECHANISM (Blueprint §5.1 / §5.5)
    # ═══════════════════════════════════════════════════════════════════════

    async def handle_monnify_funding(
        self,
        db: AsyncSession,
        *,
        monnify_reference: str,   # monnifyTransactionReference — idempotency key
        virtual_account_number: str,
        amount_ngn: Decimal,
        sender_bank: str = "",
        sender_name: str = "",
    ) -> Optional[WalletTransaction]:
        """
        Credit wallet from Monnify bank transfer webhook.

        Blueprint §5.1:
        "Transfer from any Nigerian bank triggers Monnify webhook →
        POST /api/v1/webhooks/monnify/funding
        Backend: verify HMAC signature → check idempotency key
        (idempotency key = monnify_transaction_reference, stored in
         wallet_transactions.external_reference — UNIQUE constraint)
        On success: credit wallet, emit WebSocket event 'wallet_credited',
        send push notification 'Your wallet has been funded with ₦X,XXX'"

        Args:
            monnify_reference: Monnify's transaction reference (idempotency key).
            virtual_account_number: The virtual account that received the transfer.
            amount_ngn: Amount in Naira (Monnify sends amountPaid in Naira, not kobo).
        """
        # Blueprint §5.5: idempotency via external_reference UNIQUE constraint
        existing = await wallet_transaction_crud.get_by_external_reference(
            db, external_reference=monnify_reference
        )
        if existing:
            logger.info("Duplicate Monnify webhook (idempotent): %s", monnify_reference)
            return existing

        # Validate amount
        min_topup = Decimal(str(settings.WALLET_MIN_TOPUP))
        if amount_ngn < min_topup:
            logger.warning(
                "Monnify amount ₦%s below minimum ₦%s: ref=%s",
                amount_ngn, min_topup, monnify_reference,
            )
            return None

        # Find wallet by virtual account number
        result = await db.execute(
            select(Wallet).where(
                Wallet.virtual_acct_number == virtual_account_number
            )
        )
        wallet = result.scalars().first()
        if not wallet:
            logger.error(
                "Monnify webhook: no wallet found for virtual account %s",
                virtual_account_number,
            )
            return None

        # Check daily funding limit
        await self._check_daily_funding_limit(db, wallet_id=wallet.id, amount=amount_ngn)

        # Credit wallet — all in one atomic transaction
        txn = await wallet_crud.credit_wallet(
            db,
            wallet_id=wallet.id,
            amount=amount_ngn,
            transaction_type=TransactionType.TOP_UP,
            description=f"Bank transfer from {sender_name or 'bank'} via Monnify",
            idempotency_key=f"MONNIFY_{monnify_reference}",
            external_reference=monnify_reference,   # UNIQUE constraint prevents double-credit
            metadata={
                "sender_bank":       sender_bank,
                "sender_name":       sender_name,
                "virtual_account":   virtual_account_number,
                "monnify_reference": monnify_reference,
            },
        )
        await db.commit()
        await db.refresh(txn)

        # Blueprint §5.1: emit WebSocket event + send push notification
        await self._emit_wallet_credited(
            wallet=wallet, amount=amount_ngn, transaction=txn
        )

        logger.info(
            "Monnify funding credited: wallet=%s amount=₦%s ref=%s",
            wallet.id, amount_ngn, monnify_reference,
        )
        return txn

    # ═══════════════════════════════════════════════════════════════════════
    # PAYSTACK CARD TOP-UP CONFIRMATION
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
        Credit wallet after Paystack payment verification.
        NOTE: Paystack amounts are in kobo — divide by 100 before calling this.
        Blueprint §5.1: "Paystack amounts are in kobo — DIVIDE by 100 before crediting."
        """
        min_topup = Decimal(str(settings.WALLET_MIN_TOPUP))
        if amount_ngn < min_topup:
            raise ValidationException(f"Minimum top-up is ₦{min_topup:,.0f}")

        wallet = await self.get_user_wallet(db, user_id=user_id)

        # External reference idempotency (Paystack reference is unique)
        if reference:
            existing = await wallet_transaction_crud.get_by_external_reference(
                db, external_reference=reference
            )
            if existing:
                logger.info("Duplicate Paystack top-up (idempotent): %s", reference)
                return existing

        await self._check_daily_funding_limit(db, wallet_id=wallet.id, amount=amount_ngn)

        idem_key = generate_idempotency_key()
        txn = await wallet_crud.credit_wallet(
            db,
            wallet_id=wallet.id,
            amount=amount_ngn,
            transaction_type=TransactionType.TOP_UP,
            description=description,
            idempotency_key=idem_key,
            external_reference=reference,
            metadata=metadata,
        )
        await db.commit()
        await db.refresh(txn)

        # Blueprint §5.1: WebSocket + push
        await self._emit_wallet_credited(wallet=wallet, amount=amount_ngn, transaction=txn)
        return txn

    # ═══════════════════════════════════════════════════════════════════════
    # DEBIT (spending)
    # ═══════════════════════════════════════════════════════════════════════

    async def debit_wallet(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        amount_ngn: Decimal,
        description: str,
        idempotency_key: str,
        reference: Optional[str] = None,
        related_order_id: Optional[UUID] = None,
        related_booking_id: Optional[UUID] = None,
        metadata: Optional[dict] = None,
    ) -> WalletTransaction:
        """Debit wallet for payment. idempotency_key REQUIRED (Blueprint §5.6)."""
        wallet = await self.get_user_wallet(db, user_id=user_id)

        existing = await wallet_transaction_crud.get_by_idempotency_key(
            db, idempotency_key=idempotency_key
        )
        if existing:
            logger.info("Duplicate debit (idempotent): %s", idempotency_key)
            return existing

        txn = await wallet_crud.debit_wallet(
            db,
            wallet_id=wallet.id,
            amount=amount_ngn,
            transaction_type=TransactionType.PAYMENT,
            description=description,
            idempotency_key=idempotency_key,
            external_reference=reference,
            related_order_id=related_order_id,
            related_booking_id=related_booking_id,
            metadata=metadata,
        )
        await db.commit()
        await db.refresh(txn)
        return txn

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
        idempotency_key: str,
        reference: Optional[str] = None,
    ) -> WalletTransaction:
        """
        Refund to wallet.
        Blueprint §5.1: "Refunds: return to customer wallet within 24 hours."
        Triggered by Celery task process_refund (max 24h delay).
        """
        wallet = await self.get_user_wallet(db, user_id=user_id)

        existing = await wallet_transaction_crud.get_by_idempotency_key(
            db, idempotency_key=idempotency_key
        )
        if existing:
            return existing

        txn = await wallet_crud.credit_wallet(
            db,
            wallet_id=wallet.id,
            amount=amount_ngn,
            transaction_type=TransactionType.REFUND,
            description=description,
            idempotency_key=idempotency_key,
            external_reference=reference,
        )
        await db.commit()
        await db.refresh(txn)
        return txn

    # ═══════════════════════════════════════════════════════════════════════
    # REFERRAL BONUS
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
        Credit ₦1,000 referral bonus to referrer.
        Blueprint §9.1: "Referrer reward: ₦1,000 on referred friend's
        first completed purchase."
        """
        min_order = Decimal(str(settings.NEW_USER_DISCOUNT_MIN_ORDER))  # ₦2,000
        if first_order_amount < min_order:
            logger.info(
                "First order ₦%s < minimum ₦%s — no referral bonus",
                first_order_amount, min_order,
            )
            return None

        idem_key = f"REFERRAL_{referred_user_id}"

        existing = await wallet_transaction_crud.get_by_idempotency_key(
            db, idempotency_key=idem_key
        )
        if existing:
            logger.info("Referral bonus already credited: %s", idem_key)
            return existing

        wallet = await self.get_user_wallet(db, user_id=referrer_user_id)
        bonus  = Decimal(str(settings.REFERRAL_BONUS_AMOUNT))  # ₦1,000

        txn = await wallet_crud.credit_wallet(
            db,
            wallet_id=wallet.id,
            amount=bonus,
            transaction_type=TransactionType.REFERRAL_BONUS,
            description=f"Referral bonus — your friend completed their first order",
            idempotency_key=idem_key,
            metadata={
                "referred_user_id":   str(referred_user_id),
                "first_order_amount": float(first_order_amount),
            },
        )
        await db.commit()
        await db.refresh(txn)
        return txn

    # ═══════════════════════════════════════════════════════════════════════
    # BUSINESS WALLET CREDIT (legacy — use transaction_service.process_payment)
    # ═══════════════════════════════════════════════════════════════════════

    async def credit_business_wallet(
        self,
        db: AsyncSession,
        *,
        business_user_id: UUID,
        gross_amount: Decimal,
        platform_fee: Decimal,
        description: str,
        idempotency_key: str,
        reference: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> WalletTransaction:
        """
        LEGACY: Credit business wallet with fee already deducted.
        For standard payment flows, use transaction_service.process_payment().
        """
        net_amount = gross_amount - platform_fee
        if net_amount <= 0:
            raise ValidationException("Net amount after fee must be positive")

        wallet = await self.get_user_wallet(db, user_id=business_user_id)

        existing = await wallet_transaction_crud.get_by_idempotency_key(
            db, idempotency_key=idempotency_key
        )
        if existing:
            return existing

        txn = await wallet_crud.credit_wallet(
            db,
            wallet_id=wallet.id,
            amount=net_amount,
            transaction_type=TransactionType.CREDIT,
            description=description,
            idempotency_key=idempotency_key,
            external_reference=reference,
            metadata={
                **(metadata or {}),
                "gross_amount": float(gross_amount),
                "platform_fee": float(platform_fee),
                "net_amount":   float(net_amount),
            },
        )
        await db.commit()
        await db.refresh(txn)
        return txn

    # ═══════════════════════════════════════════════════════════════════════
    # WITHDRAWAL — Business / Rider
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
        Withdraw to bank account. Business/Rider only.
        Blueprint §5.2: min ₦1,000. Daily limit ₦1,000,000.
        Blueprint §3.3: PIN verified at router layer (require_pin_set dependency).
        """
        from app.services.payment_service import payment_service

        min_w = Decimal("1000")
        max_w = Decimal("1000000")

        if amount_ngn < min_w:
            raise ValidationException(f"Minimum withdrawal is ₦{min_w:,.0f}")
        if amount_ngn > max_w:
            raise ValidationException(f"Maximum withdrawal is ₦{max_w:,.0f} per day")

        await self._check_daily_withdrawal_limit(db, user_id=user_id, amount=amount_ngn)

        # Validate bank account BEFORE touching wallet (fail-fast)
        try:
            resolve  = await payment_service.resolve_account(
                account_number=bank_account, bank_code=bank_code
            )
            resolved_name = resolve.get("data", {}).get("account_name") or recipient_name
        except Exception as exc:
            raise ValidationException(f"Bank account validation failed: {exc}")

        try:
            recipient_resp = await payment_service.create_transfer_recipient(
                account_number=bank_account,
                bank_code=bank_code,
                name=resolved_name,
            )
            recipient_code = recipient_resp.get("data", {}).get("recipient_code")
            if not recipient_code:
                raise ValidationException("Failed to register bank account with Paystack")
        except Exception as exc:
            raise ValidationException(f"Bank account registration failed: {exc}")

        # Only now debit wallet
        wallet = await self.get_user_wallet(db, user_id=user_id)
        if wallet.balance < amount_ngn:
            raise InsufficientBalanceException()

        idem_key = generate_idempotency_key()
        txn = await wallet_crud.debit_wallet(
            db,
            wallet_id=wallet.id,
            amount=amount_ngn,
            transaction_type=TransactionType.DEBIT,
            description=description or "Withdrawal to bank account",
            idempotency_key=idem_key,
            metadata={
                "bank_account":   bank_account,
                "bank_code":      bank_code,
                "recipient_name": resolved_name,
                "recipient_code": recipient_code,
            },
        )
        await db.commit()
        await db.refresh(txn)

        # Initiate transfer (reverse debit on failure)
        try:
            transfer_resp = await payment_service.initiate_transfer(
                recipient_code=recipient_code,
                amount=amount_ngn,
                reason=description or "Wallet withdrawal",
            )
            txn.meta_data = {
                **(txn.meta_data or {}),
                "transfer_code":   transfer_resp.get("data", {}).get("transfer_code"),
                "transfer_status": "pending",
            }
            await db.commit()
            await db.refresh(txn)
        except Exception as exc:
            logger.error(
                "Transfer failed for user=%s amount=₦%s — reversing debit: %s",
                user_id, amount_ngn, exc,
            )
            reversal_key = generate_idempotency_key()
            await wallet_crud.credit_wallet(
                db,
                wallet_id=wallet.id,
                amount=amount_ngn,
                transaction_type=TransactionType.REFUND,
                description=f"Withdrawal reversal — transfer failed",
                idempotency_key=reversal_key,
            )
            await db.commit()
            raise ValidationException(
                "Withdrawal could not be processed. Your funds have been returned."
            )

        logger.info(
            "Withdrawal initiated: user=%s amount=₦%s account=%s",
            user_id, amount_ngn, bank_account,
        )
        return txn

    # ═══════════════════════════════════════════════════════════════════════
    # RIDER EARNINGS
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
        wallet   = await self.get_user_wallet(db, user_id=rider_user_id)
        idem_key = f"DELIVERY_{delivery_id}"

        existing = await wallet_transaction_crud.get_by_idempotency_key(
            db, idempotency_key=idem_key
        )
        if existing:
            return existing

        txn = await wallet_crud.credit_wallet(
            db,
            wallet_id=wallet.id,
            amount=delivery_fee,
            transaction_type=TransactionType.CREDIT,
            description=description,
            idempotency_key=idem_key,
            metadata={"delivery_id": str(delivery_id)},
        )
        await db.commit()
        await db.refresh(txn)
        return txn

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
        """Transfer between Localy wallets. Atomic debit + credit."""
        import time

        if amount_ngn <= 0:
            raise ValidationException("Transfer amount must be positive")

        sender_wallet    = await self.get_user_wallet(db, user_id=from_user_id)
        recipient_wallet = await wallet_crud.get_by_wallet_number(
            db, wallet_number=recipient_wallet_number
        )
        if not recipient_wallet:
            raise ValidationException(f"Wallet '{recipient_wallet_number}' not found")
        if sender_wallet.id == recipient_wallet.id:
            raise ValidationException("Cannot transfer to your own wallet")
        if sender_wallet.balance < amount_ngn:
            raise InsufficientBalanceException()

        ref_base   = f"TRANSFER_{sender_wallet.id}_{int(time.time() * 1000)}"
        debit_key  = f"{ref_base}_DEBIT"
        credit_key = f"{ref_base}_CREDIT"

        existing_debit = await wallet_transaction_crud.get_by_idempotency_key(
            db, idempotency_key=debit_key
        )
        if existing_debit:
            existing_credit = await wallet_transaction_crud.get_by_idempotency_key(
                db, idempotency_key=credit_key
            )
            return existing_debit, existing_credit

        debit_txn = await wallet_crud.debit_wallet(
            db,
            wallet_id=sender_wallet.id,
            amount=amount_ngn,
            transaction_type=TransactionType.DEBIT,
            description=f"Transfer to {recipient_wallet_number}: {description}",
            idempotency_key=debit_key,
            metadata={
                "transfer_type":           "outgoing",
                "recipient_wallet_number": recipient_wallet_number,
            },
        )
        credit_txn = await wallet_crud.credit_wallet(
            db,
            wallet_id=recipient_wallet.id,
            amount=amount_ngn,
            transaction_type=TransactionType.CREDIT,
            description=f"Transfer from {sender_wallet.wallet_number}: {description}",
            idempotency_key=credit_key,
            metadata={
                "transfer_type":        "incoming",
                "sender_wallet_number": sender_wallet.wallet_number,
            },
        )
        await db.commit()
        await db.refresh(debit_txn)
        await db.refresh(credit_txn)
        return debit_txn, credit_txn

    # ═══════════════════════════════════════════════════════════════════════
    # TRANSACTION HISTORY
    # ═══════════════════════════════════════════════════════════════════════

    async def get_transaction_history(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        transaction_type=None,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[WalletTransaction], int]:
        wallet = await self.get_user_wallet(db, user_id=user_id)
        return await wallet_transaction_crud.get_wallet_transactions(
            db,
            wallet_id=wallet.id,
            transaction_type=transaction_type,
            skip=skip,
            limit=limit,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # INTERNAL HELPERS
    # ═══════════════════════════════════════════════════════════════════════

    async def _check_daily_funding_limit(
        self, db: AsyncSession, *, wallet_id: UUID, amount: Decimal
    ) -> None:
        """Blueprint §5.1: Daily funding limit ₦2,000,000."""
        from sqlalchemy import func
        # Blueprint §16.4 HARD RULE: timezone-aware timestamp
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        result = await db.execute(
            select(func.sum(WalletTransaction.amount)).where(
                WalletTransaction.wallet_id == wallet_id,
                WalletTransaction.transaction_type == TransactionType.TOP_UP,
                WalletTransaction.status == TransactionStatus.COMPLETED,
                WalletTransaction.created_at >= today_start,
            )
        )
        today_total = result.scalar() or Decimal("0")
        daily_limit = Decimal(str(settings.WALLET_DAILY_FUNDING_LIMIT))  # ₦2,000,000
        if today_total + amount > daily_limit:
            raise ValidationException(
                f"Daily funding limit of ₦{daily_limit:,.0f} exceeded"
            )

    async def _check_daily_withdrawal_limit(
        self, db: AsyncSession, *, user_id: UUID, amount: Decimal
    ) -> None:
        """Blueprint §5.2: Daily withdrawal limit ₦1,000,000."""
        from sqlalchemy import func
        wallet = await self.get_user_wallet(db, user_id=user_id)
        # Blueprint §16.4 HARD RULE: timezone-aware timestamp
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        result = await db.execute(
            select(func.sum(WalletTransaction.amount)).where(
                WalletTransaction.wallet_id == wallet.id,
                WalletTransaction.transaction_type == TransactionType.DEBIT,
                WalletTransaction.status == TransactionStatus.COMPLETED,
                WalletTransaction.created_at >= today_start,
            )
        )
        today_total = result.scalar() or Decimal("0")
        if today_total + amount > Decimal("1000000"):
            raise ValidationException(
                "Daily withdrawal limit of ₦1,000,000 exceeded"
            )

    async def _emit_wallet_credited(
        self,
        wallet: Wallet,
        amount: Decimal,
        transaction: WalletTransaction,
    ) -> None:
        """
        Blueprint §5.1: "emit WebSocket event 'wallet_credited', send push notification
        'Your wallet has been funded with ₦X,XXX'"
        Non-fatal — wallet is already credited before this is called.
        """
        try:
            from app.services.websocket_manager import ws_manager
            await ws_manager.send_to_user(
                user_id=str(wallet.owner_id),
                event="wallet_credited",
                data={
                    "amount":    float(amount),
                    "balance":   float(wallet.balance),
                    "txn_id":    str(transaction.id),
                    "currency":  "NGN",
                },
            )
        except Exception as exc:
            logger.warning("WebSocket wallet_credited emission failed: %s", exc)

        try:
            from app.tasks.notification_tasks import send_wallet_funded_push
            send_wallet_funded_push.delay(
                str(wallet.owner_id), float(amount)
            )
        except Exception as exc:
            logger.warning("Push notification for wallet_credited failed: %s", exc)

    @staticmethod
    def calculate_platform_fee(transaction_type: str) -> Decimal:
        """Backward compat — returns customer-side fee only."""
        from app.services.transaction_service import TransactionService
        customer_fee, _ = TransactionService.get_fees(transaction_type)
        return customer_fee


# ─── Account name helper ──────────────────────────────────────────────────────

def _build_account_name(user) -> str:
    """Display name for Monnify virtual account."""
    if hasattr(user, "customer_profile") and user.customer_profile:
        p    = user.customer_profile
        name = f"{p.first_name or ''} {p.last_name or ''}".strip()
        if name:
            return name
    if hasattr(user, "business") and user.business:
        return user.business.business_name or "Localy Business"
    if hasattr(user, "rider") and user.rider:
        r    = user.rider
        name = f"{r.first_name or ''} {r.last_name or ''}".strip()
        if name:
            return name
    # Fallback — Blueprint §14: phone_number (not phone)
    return getattr(user, "phone_number", None) or str(user.id)[:20]


# Singleton
wallet_service = WalletService()