from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import date, time, datetime, timezone
from decimal import Decimal

from app.crud.health_crud import (
    doctor_crud, consultation_crud, prescription_crud,
    pharmacy_crud, pharmacy_order_crud,
    lab_center_crud, lab_booking_crud
)
from app.core.exceptions import (
    NotFoundException, ValidationException, InsufficientBalanceException
)
from app.core.constants import (
    TransactionType,
    PLATFORM_FEE_BOOKING,   # ₦100 — consultations, lab bookings (blueprint §4.4)
    PLATFORM_FEE_STANDARD,  # ₦50  — pharmacy orders (product/item fee)
)
from app.models.user_model import User
from app.models.business_model import Business
from app.models.health_model import (
    Consultation, Prescription, PharmacyOrder, LabBooking,
    ConsultationStatusEnum, PrescriptionStatusEnum
)
# FIX: Import wallet models directly — wallet_crud is fully async (AsyncSession)
# and cannot be called from a sync service without await. All payment operations
# here use direct SQLAlchemy sync queries instead, following the same pattern
# used in product_service.py.
from app.models.wallet_model import (
    Wallet,
    WalletTransaction,
    TransactionTypeEnum,
    TransactionStatusEnum,
    generate_wallet_number,
)


# ──────────────────────────────────────────────────────────────────────────────
# Private sync payment helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_or_create_wallet_sync(db: Session, *, user_id: UUID) -> Wallet:
    """
    Get or create a wallet for any user using a sync Session.

    wallet_crud.get_or_create_wallet() is async — it cannot be called from a
    sync service without await. This inline replacement is functionally
    equivalent and safe to use with a sync Session.
    """
    wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()
    if not wallet:
        wallet = Wallet(
            user_id=user_id,
            wallet_number=generate_wallet_number(),
            balance=Decimal("0.00"),
            currency="NGN",
            is_active=True,
        )
        db.add(wallet)
        db.flush()
    return wallet


def _debit_wallet_sync(
    db: Session,
    *,
    wallet: Wallet,
    amount: Decimal,
    description: str,
    reference_id: str,
) -> WalletTransaction:
    """Sync wallet debit. Caller must commit."""
    if wallet.balance < amount:
        raise InsufficientBalanceException()
    balance_before = wallet.balance
    wallet.balance -= amount
    txn = WalletTransaction(
        wallet_id=wallet.id,
        transaction_type=TransactionTypeEnum.PAYMENT,
        amount=amount,
        balance_before=balance_before,
        balance_after=wallet.balance,
        status=TransactionStatusEnum.COMPLETED,
        description=description,
        reference_id=reference_id,
        completed_at=datetime.now(timezone.utc),
    )
    db.add(txn)
    return txn


def _credit_wallet_sync(
    db: Session,
    *,
    wallet: Wallet,
    amount: Decimal,
    transaction_type: TransactionTypeEnum,
    description: str,
    reference_id: str,
) -> WalletTransaction:
    """Sync wallet credit. Caller must commit."""
    balance_before = wallet.balance
    wallet.balance += amount
    txn = WalletTransaction(
        wallet_id=wallet.id,
        transaction_type=transaction_type,
        amount=amount,
        balance_before=balance_before,
        balance_after=wallet.balance,
        status=TransactionStatusEnum.COMPLETED,
        description=description,
        reference_id=reference_id,
        completed_at=datetime.now(timezone.utc),
    )
    db.add(txn)
    return txn


def _credit_business_wallet_sync(
    db: Session,
    *,
    business_user_id: UUID,
    amount: Decimal,
    description: str,
    reference_id: str,
) -> None:
    """
    Credit a vendor's wallet after a successful health payment.

    Blueprint §4.2 — "All customer payments minus platform fee, deposited
    instantly on transaction completion."

    Silently skips if the business wallet cannot be resolved — the customer
    debit is already committed; this must not roll back the booking.
    """
    if amount <= Decimal("0"):
        return
    try:
        biz_wallet = _get_or_create_wallet_sync(db, user_id=business_user_id)
        _credit_wallet_sync(
            db,
            wallet=biz_wallet,
            amount=amount,
            transaction_type=TransactionTypeEnum.CREDIT,
            description=description,
            reference_id=f"biz_{reference_id}",
        )
    except Exception:
        import logging
        logging.getLogger(__name__).error(
            "Business wallet credit failed for user %s ref %s",
            business_user_id, reference_id
        )


class HealthService:

    # ── Consultations ──

    @staticmethod
    def book_consultation_and_pay(
        db: Session, *, current_user: User,
        doctor_id: UUID, consultation_type: str,
        consultation_date: date, consultation_time: time,
        patient_name: str, patient_phone: str,
        chief_complaint: str, symptoms: List[str],
        medical_history: Optional[str] = None,
        allergies: Optional[str] = None,
        current_medications: Optional[List[str]] = None,
        patient_dob: Optional[date] = None,
        patient_gender: Optional[str] = None,
        payment_method: str = "wallet"
    ) -> Consultation:
        consultation = consultation_crud.create_consultation(
            db,
            doctor_id=doctor_id,
            patient_id=current_user.id,
            consultation_type=consultation_type,
            consultation_date=consultation_date,
            consultation_time=consultation_time,
            patient_name=patient_name,
            patient_phone=patient_phone,
            chief_complaint=chief_complaint,
            symptoms=symptoms,
            medical_history=medical_history,
            allergies=allergies,
            current_medications=current_medications,
            patient_dob=patient_dob,
            patient_gender=patient_gender,
            platform_fee=PLATFORM_FEE_BOOKING,
        )

        if payment_method == "wallet":
            # FIX: Blueprint §4.4 — ₦100 booking fee on health appointments.
            # Previously this was not charged at all.
            total_charge = consultation.consultation_fee + PLATFORM_FEE_BOOKING

            # FIX: Use sync wallet ops — wallet_crud methods are async.
            customer_wallet = _get_or_create_wallet_sync(db, user_id=current_user.id)
            if customer_wallet.balance < total_charge:
                db.delete(consultation)
                db.commit()
                raise InsufficientBalanceException()

            customer_txn = _debit_wallet_sync(
                db,
                wallet=customer_wallet,
                amount=total_charge,
                description=f"Consultation with Dr. {consultation.doctor.last_name}",
                reference_id=str(consultation.id),
            )

            consultation.payment_status = "paid"
            consultation.status = ConsultationStatusEnum.CONFIRMED
            consultation.confirmed_at = datetime.now(timezone.utc)

            # FIX: Credit doctor's business wallet (amount minus platform fee).
            doctor = doctor_crud.get(db, id=doctor_id)
            if doctor:
                # FIX: business_crud.get() is async — use direct sync query instead.
                doc_business = (
                    db.query(Business).filter(Business.id == doctor.business_id).first()
                    if getattr(doctor, 'business_id', None) else None
                )
                doc_user_id = getattr(doc_business, 'user_id', None)

                if doc_user_id:
                    _credit_business_wallet_sync(
                        db,
                        business_user_id=doc_user_id,
                        amount=consultation.consultation_fee,  # net of platform fee
                        description=f"Consultation booking payment",
                        reference_id=str(consultation.id),
                    )

                doctor.total_consultations += 1

            db.commit()
            db.refresh(consultation)

        return consultation

    # ── Pharmacy Orders ──

    @staticmethod
    def place_pharmacy_order_and_pay(
        db: Session, *, current_user: User,
        pharmacy_id: UUID, items: List[Dict],
        order_type: str, customer_name: str,
        customer_phone: str,
        prescription_id: Optional[UUID] = None,
        delivery_address: Optional[str] = None,
        delivery_location: Optional[Dict] = None,
        delivery_instructions: Optional[str] = None,
        payment_method: str = "wallet"
    ) -> PharmacyOrder:
        if prescription_id:
            prescription = prescription_crud.get(db, id=prescription_id)
            if not prescription:
                raise NotFoundException("Prescription")
            if prescription.status == PrescriptionStatusEnum.EXPIRED:
                raise ValidationException("Prescription has expired")

        order = pharmacy_order_crud.create_order(
            db,
            pharmacy_id=pharmacy_id,
            customer_id=current_user.id,
            items=items,
            order_type=order_type,
            customer_name=customer_name,
            customer_phone=customer_phone,
            prescription_id=prescription_id,
            delivery_address=delivery_address,
            delivery_location=delivery_location,
            delivery_instructions=delivery_instructions
        )

        if payment_method == "wallet":
            # FIX: Use sync wallet ops — wallet_crud methods are async.
            customer_wallet = _get_or_create_wallet_sync(db, user_id=current_user.id)
            if customer_wallet.balance < order.total_amount:
                # Rollback stock & order
                for item in order.items:
                    db.query(
                        __import__('app.models.health_model', fromlist=['PharmacyProduct']).PharmacyProduct
                    ).filter_by(id=item.product_id).update({
                        "stock_quantity": __import__('app.models.health_model', fromlist=['PharmacyProduct']).PharmacyProduct.stock_quantity + item.quantity
                    })
                db.delete(order)
                db.commit()
                raise InsufficientBalanceException()

            _debit_wallet_sync(
                db,
                wallet=customer_wallet,
                amount=order.total_amount,
                description=f"Pharmacy order at {order.pharmacy.name}",
                reference_id=str(order.id),
            )
            order.payment_status = "paid"
            order.status = "confirmed"
            order.confirmed_at = datetime.now(timezone.utc)

            if prescription_id:
                prescription = prescription_crud.get(db, id=prescription_id)
                prescription.fulfilled_order_id = order.id
                prescription.status = PrescriptionStatusEnum.FULFILLED

            pharmacy = pharmacy_crud.get(db, id=pharmacy_id)
            pharmacy.total_orders += 1

            # FIX: Credit pharmacy's business wallet (net of ₦50 platform fee).
            # FIX: business_crud.get() is async — use direct sync query instead.
            pharmacy_business = (
                db.query(Business).filter(Business.id == pharmacy.business_id).first()
                if getattr(pharmacy, 'business_id', None) else None
            )
            if pharmacy_business and pharmacy_business.user_id:
                net_amount = order.total_amount - PLATFORM_FEE_STANDARD
                _credit_business_wallet_sync(
                    db,
                    business_user_id=pharmacy_business.user_id,
                    amount=max(net_amount, Decimal("0")),
                    description=f"Pharmacy order payment",
                    reference_id=str(order.id),
                )

            db.commit()
            db.refresh(order)

        return order

    # ── Lab Bookings ──

    @staticmethod
    def book_lab_and_pay(
        db: Session, *, current_user: User,
        lab_center_id: UUID, test_ids: List[UUID],
        appointment_date: date, appointment_time: time,
        sample_collection_type: str,
        patient_name: str, patient_phone: str,
        patient_email: Optional[str] = None,
        patient_dob: Optional[date] = None,
        patient_gender: Optional[str] = None,
        referring_doctor_id: Optional[UUID] = None,
        doctor_notes: Optional[str] = None,
        home_address: Optional[str] = None,
        home_location: Optional[Dict] = None,
        payment_method: str = "wallet"
    ) -> LabBooking:
        booking = lab_booking_crud.create_booking(
            db,
            lab_center_id=lab_center_id,
            customer_id=current_user.id,
            test_ids=test_ids,
            appointment_date=appointment_date,
            appointment_time=appointment_time,
            sample_collection_type=sample_collection_type,
            patient_name=patient_name,
            patient_phone=patient_phone,
            patient_email=patient_email,
            patient_dob=patient_dob,
            patient_gender=patient_gender,
            referring_doctor_id=referring_doctor_id,
            doctor_notes=doctor_notes,
            home_address=home_address,
            home_location=home_location
        )

        if payment_method == "wallet":
            # booking.total_amount already includes PLATFORM_FEE_BOOKING (added
            # in lab_booking_crud.create_booking). Do not add it again here.
            total_charge = booking.total_amount

            # FIX: Use sync wallet ops — wallet_crud methods are async.
            customer_wallet = _get_or_create_wallet_sync(db, user_id=current_user.id)
            if customer_wallet.balance < total_charge:
                db.delete(booking)
                db.commit()
                raise InsufficientBalanceException()

            _debit_wallet_sync(
                db,
                wallet=customer_wallet,
                amount=total_charge,
                description=f"Lab test at {booking.lab_center.name}",
                reference_id=str(booking.id),
            )
            booking.payment_status = "paid"
            booking.status = "confirmed"
            booking.confirmed_at = datetime.now(timezone.utc)

            # FIX: Credit lab center's business wallet (net of ₦100 platform fee).
            lab_center = lab_center_crud.get(db, id=lab_center_id)
            if lab_center:
                # FIX: business_crud.get() is async — use direct sync query instead.
                lab_business = (
                    db.query(Business).filter(Business.id == lab_center.business_id).first()
                    if getattr(lab_center, 'business_id', None) else None
                )
                if lab_business and lab_business.user_id:
                    # Deduct platform fee before crediting lab — platform keeps ₦100
                    net_amount = booking.total_amount - PLATFORM_FEE_BOOKING
                    _credit_business_wallet_sync(
                        db,
                        business_user_id=lab_business.user_id,
                        amount=max(net_amount, Decimal("0")),
                        description=f"Lab booking payment",
                        reference_id=str(booking.id),
                    )

            db.commit()
            db.refresh(booking)

        return booking


health_service = HealthService()