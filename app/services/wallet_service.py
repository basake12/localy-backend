from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID
from decimal import Decimal

from app.models.user import User
from app.models.wallet import Wallet, WalletTransaction, TransactionType, TransactionStatus
from app.crud.wallet import wallet_crud, transaction_crud
from app.core.exceptions import (
    NotFoundException,
    InsufficientFundsException,
    ValidationException
)
from app.core.utils import generate_reference_code


class WalletService:
    """Business logic for wallet operations."""

    def get_user_wallet(self, db: Session, *, user_id: UUID) -> Wallet:
        """Get user's wallet."""
        wallet = wallet_crud.get_by_user(db, user_id=user_id)
        if not wallet:
            raise NotFoundException("Wallet")
        return wallet

    def credit_wallet(
            self,
            db: Session,
            *,
            user_id: UUID,
            amount: Decimal,
            description: str,
            reference: Optional[str] = None,
            metadata: Optional[dict] = None
    ) -> WalletTransaction:
        """
        Credit wallet (add money).
        """
        if amount <= 0:
            raise ValidationException("Amount must be positive")

        wallet = self.get_user_wallet(db, user_id=user_id)

        if not reference:
            reference = generate_reference_code("CR")

        # Create credit transaction
        transaction = transaction_crud.create_transaction(
            db,
            wallet_id=wallet.id,
            transaction_type=TransactionType.CREDIT,
            amount=amount,
            description=description,
            reference=reference,
            metadata=metadata
        )

        return transaction

    def debit_wallet(
            self,
            db: Session,
            *,
            user_id: UUID,
            amount: Decimal,
            description: str,
            reference: Optional[str] = None,
            metadata: Optional[dict] = None
    ) -> WalletTransaction:
        """
        Debit wallet (remove money).
        Raises InsufficientFundsException if balance is too low.
        """
        if amount <= 0:
            raise ValidationException("Amount must be positive")

        wallet = self.get_user_wallet(db, user_id=user_id)

        # Check sufficient balance
        if wallet.balance < amount:
            raise InsufficientFundsException(
                f"Insufficient funds. Balance: ₦{wallet.balance}, Required: ₦{amount}"
            )

        if not reference:
            reference = generate_reference_code("DR")

        # Create debit transaction
        transaction = transaction_crud.create_transaction(
            db,
            wallet_id=wallet.id,
            transaction_type=TransactionType.DEBIT,
            amount=amount,
            description=description,
            reference=reference,
            metadata=metadata
        )

        return transaction

    def transfer(
            self,
            db: Session,
            *,
            from_user_id: UUID,
            to_user_id: UUID,
            amount: Decimal,
            description: str = "Wallet transfer"
    ) -> tuple[WalletTransaction, WalletTransaction]:
        """
        Transfer money between wallets.
        Returns (debit_transaction, credit_transaction).
        """
        if amount <= 0:
            raise ValidationException("Amount must be positive")

        if from_user_id == to_user_id:
            raise ValidationException("Cannot transfer to same wallet")

        # Get both wallets
        from_wallet = self.get_user_wallet(db, user_id=from_user_id)
        to_wallet = self.get_user_wallet(db, user_id=to_user_id)

        # Check sufficient balance
        if from_wallet.balance < amount:
            raise InsufficientFundsException()

        # Generate reference
        reference = generate_reference_code("TRF")

        # Debit sender
        debit_txn = self.debit_wallet(
            db,
            user_id=from_user_id,
            amount=amount,
            description=f"{description} (to {to_user_id})",
            reference=f"{reference}-OUT",
            metadata={"transfer_to": str(to_user_id)}
        )

        # Credit receiver
        credit_txn = self.credit_wallet(
            db,
            user_id=to_user_id,
            amount=amount,
            description=f"{description} (from {from_user_id})",
            reference=f"{reference}-IN",
            metadata={"transfer_from": str(from_user_id)}
        )

        return debit_txn, credit_txn

    def get_transaction_history(
            self,
            db: Session,
            *,
            user_id: UUID,
            transaction_type: Optional[TransactionType] = None,
            skip: int = 0,
            limit: int = 20
    ):
        """Get wallet transaction history."""
        wallet = self.get_user_wallet(db, user_id=user_id)

        return transaction_crud.get_wallet_transactions(
            db,
            wallet_id=wallet.id,
            transaction_type=transaction_type,
            skip=skip,
            limit=limit
        )

    def process_refund(
            self,
            db: Session,
            *,
            user_id: UUID,
            amount: Decimal,
            order_id: str,
            reason: str
    ) -> WalletTransaction:
        """Process refund to wallet."""
        reference = generate_reference_code("REFUND")

        return self.credit_wallet(
            db,
            user_id=user_id,
            amount=amount,
            description=f"Refund for order {order_id}: {reason}",
            reference=reference,
            metadata={
                "type": "refund",
                "order_id": order_id,
                "reason": reason
            }
        )

    def process_payout(
            self,
            db: Session,
            *,
            user_id: UUID,
            amount: Decimal,
            bank_account: str,
            bank_code: str
    ) -> WalletTransaction:
        """Process wallet withdrawal/payout."""
        reference = generate_reference_code("PAYOUT")

        return self.debit_wallet(
            db,
            user_id=user_id,
            amount=amount,
            description=f"Withdrawal to {bank_account}",
            reference=reference,
            metadata={
                "type": "payout",
                "bank_account": bank_account,
                "bank_code": bank_code
            }
        )


# Singleton instance
wallet_service = WalletService()