from typing import Optional, List
from sqlalchemy.orm import Session
from uuid import UUID
from decimal import Decimal
import secrets

from app.crud.base import CRUDBase
from app.models.wallet import Wallet, WalletTransaction
from app.core.exceptions import (
    NotFoundException,
    InsufficientBalanceException,
    ValidationException
)
from app.models.wallet import TransactionType, TransactionStatus
from app.config import settings


class CRUDWallet(CRUDBase[Wallet, dict, dict]):
    """CRUD operations for Wallet model"""

    def get_by_user_id(self, db: Session, *, user_id: UUID) -> Optional[Wallet]:
        """Get wallet by user ID"""
        return db.query(Wallet).filter(Wallet.user_id == user_id).first()

    def create_wallet(self, db: Session, *, user_id: UUID) -> Wallet:
        """
        Create a new wallet for user

        Args:
            db: Database session
            user_id: User ID

        Returns:
            Created wallet instance
        """
        wallet = Wallet(
            user_id=user_id,
            balance=Decimal('0.00'),
            currency="NGN",
            is_active=True
        )
        db.add(wallet)
        db.commit()
        db.refresh(wallet)
        return wallet

    def get_or_create_wallet(self, db: Session, *, user_id: UUID) -> Wallet:
        """Get existing wallet or create new one"""
        wallet = self.get_by_user_id(db, user_id=user_id)
        if not wallet:
            wallet = self.create_wallet(db, user_id=user_id)
        return wallet

    def get_balance(self, db: Session, *, user_id: UUID) -> Decimal:
        """Get wallet balance"""
        wallet = self.get_by_user_id(db, user_id=user_id)
        if not wallet:
            return Decimal('0.00')
        return wallet.balance

    def credit_wallet(
            self,
            db: Session,
            *,
            wallet_id: UUID,
            amount: Decimal,
            transaction_type: TransactionType,
            description: str,
            reference_id: Optional[str] = None,
            metadata: Optional[dict] = None
    ) -> WalletTransaction:
        """
        Credit wallet (add funds)

        Args:
            db: Database session
            wallet_id: Wallet ID
            amount: Amount to credit
            transaction_type: Type of transaction
            description: Transaction description
            reference_id: Optional reference ID
            metadata: Optional metadata

        Returns:
            Created transaction instance

        Raises:
            NotFoundException: If wallet not found
            ValidationException: If amount invalid
        """
        wallet = self.get(db, id=wallet_id)
        if not wallet:
            raise NotFoundException("Wallet")

        if amount <= 0:
            raise ValidationException("Amount must be positive")

        # Check maximum balance
        if wallet.balance + amount > Decimal(str(settings.WALLET_MAX_BALANCE)):
            raise ValidationException("Maximum wallet balance exceeded")

        # Create transaction
        balance_before = wallet.balance
        wallet.balance += amount
        balance_after = wallet.balance

        transaction = WalletTransaction(
            wallet_id=wallet_id,
            transaction_type=transaction_type,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            status=TransactionStatus.COMPLETED,
            description=description,
            reference_id=reference_id or self._generate_reference(),
            meta_data=str(metadata) if metadata else None
        )

        db.add(transaction)
        db.commit()
        db.refresh(transaction)

        return transaction

    def debit_wallet(
            self,
            db: Session,
            *,
            wallet_id: UUID,
            amount: Decimal,
            transaction_type: TransactionType,
            description: str,
            reference_id: Optional[str] = None,
            metadata: Optional[dict] = None
    ) -> WalletTransaction:
        """
        Debit wallet (deduct funds)

        Args:
            db: Database session
            wallet_id: Wallet ID
            amount: Amount to debit
            transaction_type: Type of transaction
            description: Transaction description
            reference_id: Optional reference ID
            metadata: Optional metadata

        Returns:
            Created transaction instance

        Raises:
            NotFoundException: If wallet not found
            InsufficientBalanceException: If insufficient balance
            ValidationException: If amount invalid
        """
        wallet = self.get(db, id=wallet_id)
        if not wallet:
            raise NotFoundException("Wallet")

        if amount <= 0:
            raise ValidationException("Amount must be positive")

        if wallet.balance < amount:
            raise InsufficientBalanceException()

        # Create transaction
        balance_before = wallet.balance
        wallet.balance -= amount
        balance_after = wallet.balance

        transaction = WalletTransaction(
            wallet_id=wallet_id,
            transaction_type=transaction_type,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            status=TransactionStatus.COMPLETED,
            description=description,
            reference_id=reference_id or self._generate_reference(),
            meta_data=str(metadata) if metadata else None
        )

        db.add(transaction)
        db.commit()
        db.refresh(transaction)

        return transaction

    def get_transactions(
            self,
            db: Session,
            *,
            wallet_id: UUID,
            skip: int = 0,
            limit: int = 50
    ) -> List[WalletTransaction]:
        """Get wallet transactions with pagination"""
        return db.query(WalletTransaction).filter(
            WalletTransaction.wallet_id == wallet_id
        ).order_by(
            WalletTransaction.created_at.desc()
        ).offset(skip).limit(limit).all()

    def _generate_reference(self) -> str:
        """Generate unique transaction reference"""
        return f"TXN_{secrets.token_hex(8).upper()}"


class CRUDWalletTransaction(CRUDBase[WalletTransaction, dict, dict]):
    """CRUD operations for WalletTransaction model"""

    def get_by_reference(
            self,
            db: Session,
            *,
            reference_id: str
    ) -> Optional[WalletTransaction]:
        """Get transaction by reference ID"""
        return db.query(WalletTransaction).filter(
            WalletTransaction.reference_id == reference_id
        ).first()


# Create singleton instances
wallet_crud = CRUDWallet(Wallet)
wallet_transaction_crud = CRUDWalletTransaction(WalletTransaction)

# Alias — import as either name
transaction_crud = wallet_transaction_crud