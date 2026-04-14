"""
app/crud/wallet_crud.py

CHANGES:
  1. create_wallet() has TWO variants:
     - async create_wallet() — for wallet endpoints (AsyncSession)
     - sync create_wallet_sync() — called by auth_service.register_user()
       which runs in a sync FastAPI handler with a sync Session.
       Using async create_wallet inside a sync handler would deadlock.

  2. wallet_number is generated on creation using generate_wallet_number()
     from the model. Collision retry loop ensures uniqueness.

  3. get_by_wallet_number() added — lets transfer endpoint resolve
     recipient from their wallet number (LCY1234567).

  4. Paystack amount is in KOBO — all amounts entering the DB are
     already converted to Naira by the service layer before reaching CRUD.

  5. CRUDPlatformRevenue added — tracks all platform fees collected
     with advanced filtering and aggregation for admin analytics.
"""
from typing import Optional, List, Tuple, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_, or_
from uuid import UUID
from datetime import datetime, date, timezone
from decimal import Decimal
import secrets

from app.crud.base_crud import AsyncCRUDBase as CRUDBase
from app.models.wallet_model import (
    Wallet,
    WalletTransaction,
    CryptoTopUp,
    PlatformRevenue,
    TransactionType,
    TransactionStatus,
    generate_wallet_number,
)
from app.core.exceptions import (
    NotFoundException,
    InsufficientBalanceException,
    ValidationException,
)
from app.config import settings


def _sanitize_for_jsonb(obj):
    """
    Recursively convert types that are not JSON-serializable before writing
    to a PostgreSQL JSONB column.

    Root cause: payment_service.verify_transaction() parses Paystack's JSON
    with parse_float=Decimal (or similar), so pay_data can contain Decimal
    values.  Python\'s json encoder (used by asyncpg for JSONB) does not
    handle Decimal and raises:
        TypeError: Object of type Decimal is not JSON serializable

    Converting Decimal → float here means every CRUD caller is safe without
    having to sanitize metadata at each call site.
    """
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_for_jsonb(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_jsonb(v) for v in obj]
    return obj


class CRUDWallet(CRUDBase[Wallet, dict, dict]):

    # ── Lookups ──────────────────────────────────────────────────────────────

    async def get_by_user(
        self, db: AsyncSession, *, user_id: UUID
    ) -> Optional[Wallet]:
        result = await db.execute(
            select(Wallet).where(Wallet.user_id == user_id)
        )
        return result.scalars().first()

    async def get_by_id(
        self, db: AsyncSession, *, wallet_id: UUID
    ) -> Optional[Wallet]:
        """Fetch wallet directly by its PK — used by crypto webhook handler."""
        result = await db.execute(
            select(Wallet).where(Wallet.id == wallet_id)
        )
        return result.scalars().first()

    async def get_by_user_id(
        self, db: AsyncSession, *, user_id: UUID
    ) -> Optional[Wallet]:
        return await self.get_by_user(db, user_id=user_id)

    async def get_by_wallet_number(
        self, db: AsyncSession, *, wallet_number: str
    ) -> Optional[Wallet]:
        result = await db.execute(
            select(Wallet).where(Wallet.wallet_number == wallet_number)
        )
        return result.scalars().first()

    # ── Create (async — for wallet endpoints) ────────────────────────────────

    async def create_wallet(
        self, db: AsyncSession, *, user_id: UUID
    ) -> Wallet:
        """Create wallet for a user. Generates a unique wallet_number."""
        wallet_number = await self._unique_wallet_number_async(db)
        wallet = Wallet(
            user_id=user_id,
            wallet_number=wallet_number,
            balance=Decimal("0.00"),
            currency="NGN",
            is_active=True,
        )
        db.add(wallet)
        await db.commit()
        await db.refresh(wallet)
        return wallet

    # ── Create (sync — called by auth_service in sync handler) ───────────────

    def create_wallet_sync(
        self, db: Session, *, user_id: UUID
    ) -> Wallet:
        """
        Sync variant for use inside sync FastAPI endpoints.
        auth_service.register_user() is called from a sync handler;
        using the async version there would block the event loop.
        """
        wallet_number = self._unique_wallet_number_sync(db)
        wallet = Wallet(
            user_id=user_id,
            wallet_number=wallet_number,
            balance=Decimal("0.00"),
            currency="NGN",
            is_active=True,
        )
        db.add(wallet)
        db.commit()
        db.refresh(wallet)
        return wallet

    async def get_or_create_wallet(
        self, db: AsyncSession, *, user_id: UUID
    ) -> Wallet:
        wallet = await self.get_by_user(db, user_id=user_id)
        if not wallet:
            wallet = await self.create_wallet(db, user_id=user_id)
        return wallet

    # ── Credit ───────────────────────────────────────────────────────────────

    async def credit_wallet(
        self,
        db: AsyncSession,
        *,
        wallet_id: UUID,
        amount: Decimal,
        transaction_type: TransactionType,
        description: str,
        reference_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> WalletTransaction:
        result = await db.execute(
            select(Wallet).where(Wallet.id == wallet_id)
        )
        wallet = result.scalars().first()
        if not wallet:
            raise NotFoundException("Wallet")
        if amount <= 0:
            raise ValidationException("Amount must be positive")

        max_bal = Decimal(
            str(getattr(settings, "WALLET_MAX_BALANCE", 10_000_000))
        )
        if wallet.balance + amount > max_bal:
            raise ValidationException("Maximum wallet balance exceeded")

        balance_before = wallet.balance
        wallet.balance += amount

        txn = WalletTransaction(
            wallet_id=wallet_id,
            transaction_type=transaction_type,
            amount=amount,
            balance_before=balance_before,
            balance_after=wallet.balance,
            status=TransactionStatus.COMPLETED,
            description=description,
            reference_id=reference_id or self._generate_reference(),
            meta_data=_sanitize_for_jsonb(metadata) if metadata is not None else None,
            completed_at=datetime.now(timezone.utc),   # set when status = COMPLETED
        )
        db.add(txn)
        return txn  # caller commits for atomicity

    # ── Debit ────────────────────────────────────────────────────────────────

    async def debit_wallet(
        self,
        db: AsyncSession,
        *,
        wallet_id: UUID,
        amount: Decimal,
        transaction_type: TransactionType,
        description: str,
        reference_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> WalletTransaction:
        result = await db.execute(
            select(Wallet).where(Wallet.id == wallet_id)
        )
        wallet = result.scalars().first()
        if not wallet:
            raise NotFoundException("Wallet")
        if amount <= 0:
            raise ValidationException("Amount must be positive")
        if wallet.balance < amount:
            raise InsufficientBalanceException()

        balance_before = wallet.balance
        wallet.balance -= amount

        txn = WalletTransaction(
            wallet_id=wallet_id,
            transaction_type=transaction_type,
            amount=amount,
            balance_before=balance_before,
            balance_after=wallet.balance,
            status=TransactionStatus.COMPLETED,
            description=description,
            reference_id=reference_id or self._generate_reference(),
            meta_data=_sanitize_for_jsonb(metadata) if metadata is not None else None,
            completed_at=datetime.now(timezone.utc),   # set when status = COMPLETED
        )
        db.add(txn)
        return txn  # caller commits

    # ── Wallet number helpers ─────────────────────────────────────────────────

    async def _unique_wallet_number_async(
        self, db: AsyncSession, max_tries: int = 10
    ) -> str:
        for _ in range(max_tries):
            number = generate_wallet_number()
            result = await db.execute(
                select(Wallet).where(Wallet.wallet_number == number)
            )
            if result.scalars().first() is None:
                return number
        raise RuntimeError("Could not generate unique wallet number")

    def _unique_wallet_number_sync(
        self, db: Session, max_tries: int = 10
    ) -> str:
        for _ in range(max_tries):
            number = generate_wallet_number()
            existing = (
                db.query(Wallet)
                .filter(Wallet.wallet_number == number)
                .first()
            )
            if existing is None:
                return number
        raise RuntimeError("Could not generate unique wallet number")

    @staticmethod
    def _generate_reference() -> str:
        return f"TXN_{secrets.token_hex(8).upper()}"


class CRUDWalletTransaction(CRUDBase[WalletTransaction, dict, dict]):

    async def get_by_reference(
        self, db: AsyncSession, *, reference_id: str
    ) -> Optional[WalletTransaction]:
        result = await db.execute(
            select(WalletTransaction).where(
                WalletTransaction.reference_id == reference_id
            )
        )
        return result.scalars().first()

    async def get_wallet_transactions(
        self,
        db: AsyncSession,
        *,
        wallet_id: UUID,
        transaction_type: Optional[TransactionType] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[WalletTransaction], int]:
        query = select(WalletTransaction).where(
            WalletTransaction.wallet_id == wallet_id
        )
        count_q = select(func.count()).select_from(WalletTransaction).where(
            WalletTransaction.wallet_id == wallet_id
        )

        if transaction_type:
            query   = query.where(WalletTransaction.transaction_type == transaction_type)
            count_q = count_q.where(WalletTransaction.transaction_type == transaction_type)

        query = (
            query.order_by(WalletTransaction.created_at.desc())
            .offset(skip)
            .limit(limit)
        )

        result       = await db.execute(query)
        count_result = await db.execute(count_q)
        return list(result.scalars().all()), count_result.scalar_one()

    # Alias kept for backward compatibility
    async def create_transaction(
        self,
        db: AsyncSession,
        *,
        wallet_id: UUID,
        transaction_type: TransactionType,
        amount: Decimal,
        description: str,
        reference: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> WalletTransaction:
        txn = WalletTransaction(
            wallet_id=wallet_id,
            transaction_type=transaction_type,
            amount=amount,
            balance_before=Decimal("0"),
            balance_after=Decimal("0"),
            status=TransactionStatus.COMPLETED,
            description=description,
            reference_id=reference or f"TXN_{secrets.token_hex(8).upper()}",
            meta_data=_sanitize_for_jsonb(metadata) if metadata is not None else None,
        )
        db.add(txn)
        await db.commit()
        await db.refresh(txn)
        return txn


class CRUDCryptoTopUp(CRUDBase[CryptoTopUp, dict, dict]):

    async def get_by_provider_order(
        self, db: AsyncSession, *, provider_order_id: str
    ) -> Optional[CryptoTopUp]:
        result = await db.execute(
            select(CryptoTopUp).where(
                CryptoTopUp.provider_order_id == provider_order_id
            )
        )
        return result.scalars().first()

    async def get_by_provider_order_id(
        self, db: AsyncSession, *, provider_order_id: str
    ) -> Optional[CryptoTopUp]:
        """Alias — matches the method name used in wallet_service."""
        return await self.get_by_provider_order(db, provider_order_id=provider_order_id)

    async def update_status(
        self,
        db: AsyncSession,
        *,
        crypto_top_up_id: UUID,
        status,
        received_crypto: Optional[Decimal] = None,
        confirmations: int = 0,
    ) -> Optional[CryptoTopUp]:
        """Update CryptoTopUp status after webhook confirmation."""
        result = await db.execute(
            select(CryptoTopUp).where(CryptoTopUp.id == crypto_top_up_id)
        )
        record = result.scalars().first()
        if not record:
            return None
        record.status = status
        if received_crypto is not None:
            record.received_crypto = received_crypto
        record.confirmations = confirmations
        from app.models.wallet_model import CryptoTopUpStatusEnum
        if status in (CryptoTopUpStatusEnum.COMPLETED, "completed", CryptoTopUpStatusEnum.COMPLETED.value):
            record.completed_at = datetime.now(timezone.utc)
        db.add(record)
        return record

    async def get_pending_by_wallet(
        self, db: AsyncSession, *, wallet_id: UUID
    ) -> List[CryptoTopUp]:
        from app.models.wallet_model import CryptoTopUpStatusEnum
        result = await db.execute(
            select(CryptoTopUp).where(
                CryptoTopUp.wallet_id == wallet_id,
                CryptoTopUp.status.in_([
                    CryptoTopUpStatusEnum.AWAITING_PAYMENT,
                    CryptoTopUpStatusEnum.CONFIRMING,
                ]),
            )
        )
        return list(result.scalars().all())


class CRUDPlatformRevenue(CRUDBase[PlatformRevenue, dict, dict]):
    """
    CRUD operations for platform revenue tracking.

    Provides:
    - Revenue record creation (called by TransactionService)
    - Advanced filtering for admin analytics
    - Aggregation queries for financial reporting
    - Revenue statistics by date range and transaction type
    """

    # ── Create ───────────────────────────────────────────────────────────────

    async def create_revenue_record(
        self,
        db: AsyncSession,
        *,
        customer_transaction_id: UUID,
        business_transaction_id: UUID,
        customer_id: UUID,
        business_id: UUID,
        gross_amount: Decimal,
        platform_fee: Decimal,
        net_amount: Decimal,
        transaction_type: str,
        transaction_reference: str,
        related_entity_id: Optional[UUID] = None,
        description: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> PlatformRevenue:
        """
        Create a platform revenue record.

        Called by TransactionService.process_payment() after successful
        customer debit and business credit.
        """
        revenue = PlatformRevenue(
            customer_transaction_id=customer_transaction_id,
            business_transaction_id=business_transaction_id,
            customer_id=customer_id,
            business_id=business_id,
            gross_amount=gross_amount,
            platform_fee=platform_fee,
            net_amount=net_amount,
            transaction_type=transaction_type,
            transaction_reference=transaction_reference,
            related_entity_id=related_entity_id,
            description=description,
            meta_data=_sanitize_for_jsonb(metadata) if metadata is not None else None,
        )
        db.add(revenue)
        # Note: caller commits for atomicity with wallet transactions
        return revenue

    # ── Lookups ──────────────────────────────────────────────────────────────

    async def get_by_reference(
        self, db: AsyncSession, *, reference: str
    ) -> Optional[PlatformRevenue]:
        """Get revenue record by transaction reference (idempotency check)."""
        result = await db.execute(
            select(PlatformRevenue).where(
                PlatformRevenue.transaction_reference == reference
            )
        )
        return result.scalars().first()

    async def get_by_entity(
        self, db: AsyncSession, *, entity_id: UUID
    ) -> Optional[PlatformRevenue]:
        """Get revenue record by related entity (booking_id, order_id, etc.)."""
        result = await db.execute(
            select(PlatformRevenue).where(
                PlatformRevenue.related_entity_id == entity_id
            )
        )
        return result.scalars().first()

    # ── List & Filter ────────────────────────────────────────────────────────

    async def get_revenue_list(
        self,
        db: AsyncSession,
        *,
        transaction_type: Optional[str] = None,
        customer_id: Optional[UUID] = None,
        business_id: Optional[UUID] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[PlatformRevenue], int]:
        """
        Get filtered list of platform revenue records.
        
        Used by admin panel for revenue tracking and auditing.
        """
        query = select(PlatformRevenue)
        count_query = select(func.count()).select_from(PlatformRevenue)
        
        filters = []
        
        if transaction_type:
            filters.append(PlatformRevenue.transaction_type == transaction_type)
        
        if customer_id:
            filters.append(PlatformRevenue.customer_id == customer_id)
        
        if business_id:
            filters.append(PlatformRevenue.business_id == business_id)
        
        if start_date:
            start_datetime = datetime.combine(start_date, datetime.min.time())
            filters.append(PlatformRevenue.created_at >= start_datetime)
        
        if end_date:
            end_datetime = datetime.combine(end_date, datetime.max.time())
            filters.append(PlatformRevenue.created_at <= end_datetime)
        
        if filters:
            query = query.where(and_(*filters))
            count_query = count_query.where(and_(*filters))
        
        query = (
            query.order_by(PlatformRevenue.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        
        result = await db.execute(query)
        count_result = await db.execute(count_query)
        
        return list(result.scalars().all()), count_result.scalar_one()

    # ── Aggregations & Analytics ─────────────────────────────────────────────

    async def get_revenue_stats(
        self,
        db: AsyncSession,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """
        Get aggregated revenue statistics for admin dashboard.
        
        Returns total revenue, transaction count, and breakdown by type.
        """
        query = select(
            func.sum(PlatformRevenue.platform_fee).label("total_revenue"),
            func.count(PlatformRevenue.id).label("total_count"),
            func.avg(PlatformRevenue.platform_fee).label("average_fee"),
        )
        
        filters = []
        if start_date:
            start_datetime = datetime.combine(start_date, datetime.min.time())
            filters.append(PlatformRevenue.created_at >= start_datetime)
        
        if end_date:
            end_datetime = datetime.combine(end_date, datetime.max.time())
            filters.append(PlatformRevenue.created_at <= end_datetime)
        
        if filters:
            query = query.where(and_(*filters))
        
        result = await db.execute(query)
        row = result.first()
        
        # Get breakdown by transaction type
        breakdown = await self._get_revenue_breakdown(
            db, start_date=start_date, end_date=end_date
        )
        
        return {
            "total_revenue": row.total_revenue or Decimal("0"),
            "total_transactions": row.total_count or 0,
            "average_fee": row.average_fee or Decimal("0"),
            "breakdown": breakdown,
        }

    async def _get_revenue_breakdown(
        self,
        db: AsyncSession,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Get revenue breakdown by transaction type."""
        query = select(
            PlatformRevenue.transaction_type,
            func.sum(PlatformRevenue.platform_fee).label("revenue"),
            func.count(PlatformRevenue.id).label("count"),
        ).group_by(PlatformRevenue.transaction_type)
        
        filters = []
        if start_date:
            start_datetime = datetime.combine(start_date, datetime.min.time())
            filters.append(PlatformRevenue.created_at >= start_datetime)
        
        if end_date:
            end_datetime = datetime.combine(end_date, datetime.max.time())
            filters.append(PlatformRevenue.created_at <= end_datetime)
        
        if filters:
            query = query.where(and_(*filters))
        
        result = await db.execute(query)
        rows = result.all()
        
        breakdown = {}
        for row in rows:
            breakdown[row.transaction_type] = {
                "revenue": row.revenue or Decimal("0"),
                "count": row.count or 0,
            }
        
        return breakdown

    async def get_total_revenue(
        self,
        db: AsyncSession,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Decimal:
        """Get total platform revenue for a date range."""
        query = select(func.sum(PlatformRevenue.platform_fee))
        
        filters = []
        if start_date:
            start_datetime = datetime.combine(start_date, datetime.min.time())
            filters.append(PlatformRevenue.created_at >= start_datetime)
        
        if end_date:
            end_datetime = datetime.combine(end_date, datetime.max.time())
            filters.append(PlatformRevenue.created_at <= end_datetime)
        
        if filters:
            query = query.where(and_(*filters))
        
        result = await db.execute(query)
        total = result.scalar_one_or_none()
        
        return total or Decimal("0")


# Singletons
wallet_crud             = CRUDWallet(Wallet)
wallet_transaction_crud = CRUDWalletTransaction(WalletTransaction)
transaction_crud        = wallet_transaction_crud   # alias
crypto_top_up_crud      = CRUDCryptoTopUp(CryptoTopUp)
platform_revenue_crud   = CRUDPlatformRevenue(PlatformRevenue)