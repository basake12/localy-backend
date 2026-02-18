from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from uuid import UUID

from app.core.database import get_db
from app.dependencies import get_current_active_user
from app.models.user import User
from app.models.wallet import TransactionType
from app.schemas.wallet import (
    WalletOut,
    WalletTopUpRequest,
    WalletWithdrawRequest,
    WalletTransferRequest,
    WalletTransactionOut,
    WalletTransactionListOut
)
from app.schemas.common import SuccessResponse
from app.services.wallet_service import wallet_service
from app.services.payment_service import payment_service

router = APIRouter()


@router.get("/wallet", response_model=SuccessResponse[WalletOut])
def get_my_wallet(
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """Get current user's wallet."""
    wallet = wallet_service.get_user_wallet(db, user_id=user.id)
    return {"success": True, "data": wallet}


@router.post("/wallet/topup", response_model=SuccessResponse[dict])
def topup_wallet(
        payload: WalletTopUpRequest,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """
    Initialize wallet top-up via Paystack.
    Returns payment authorization URL.
    """
    # Initialize Paystack transaction
    payment = payment_service.initialize_transaction(
        email=user.email,
        amount=payload.amount,
        metadata={
            "user_id": str(user.id),
            "type": "wallet_topup"
        }
    )

    return {
        "success": True,
        "data": {
            "authorization_url": payment.get("data", {}).get("authorization_url"),
            "reference": payment.get("data", {}).get("reference"),
            "amount": float(payload.amount)
        }
    }


@router.post("/wallet/topup/verify", response_model=SuccessResponse[WalletTransactionOut])
def verify_topup(
        reference: str = Query(...),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """Verify Paystack payment and credit wallet."""
    # Verify payment
    result = payment_service.verify_transaction(reference)

    if result.get("status") and result.get("data", {}).get("status") == "success":
        # Credit wallet
        transaction = wallet_service.credit_wallet(
            db,
            user_id=user.id,
            amount=result["data"]["amount"],
            description="Wallet top-up via Paystack",
            reference=reference,
            metadata={"payment_data": result["data"]}
        )

        return {"success": True, "data": transaction}

    return {"success": False, "error": {"message": "Payment verification failed"}}


@router.post("/wallet/withdraw", response_model=SuccessResponse[WalletTransactionOut])
def withdraw_from_wallet(
        payload: WalletWithdrawRequest,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """Withdraw funds from wallet to bank account."""
    # Process payout
    transaction = wallet_service.process_payout(
        db,
        user_id=user.id,
        amount=payload.amount,
        bank_account=payload.bank_account_number,
        bank_code=payload.bank_code
    )

    return {"success": True, "data": transaction}


@router.post("/wallet/transfer", response_model=SuccessResponse[dict])
def transfer_funds(
        payload: WalletTransferRequest,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """Transfer funds to another user's wallet."""
    debit_txn, credit_txn = wallet_service.transfer(
        db,
        from_user_id=user.id,
        to_user_id=payload.recipient_id,
        amount=payload.amount,
        description=payload.description or "Wallet transfer"
    )

    return {
        "success": True,
        "data": {
            "debit_transaction": debit_txn,
            "credit_transaction": credit_txn,
            "amount": float(payload.amount)
        }
    }


@router.get("/wallet/transactions", response_model=SuccessResponse[WalletTransactionListOut])
def get_wallet_transactions(
        transaction_type: TransactionType = Query(None),
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """Get wallet transaction history."""
    transactions, total = wallet_service.get_transaction_history(
        db,
        user_id=user.id,
        transaction_type=transaction_type,
        skip=skip,
        limit=limit
    )

    return {
        "success": True,
        "data": {
            "transactions": transactions,
            "total": total,
            "page": skip // limit + 1,
            "page_size": limit
        }
    }