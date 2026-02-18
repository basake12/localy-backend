from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import date, time, datetime
from decimal import Decimal

from app.crud.health import (
    doctor_crud, consultation_crud, prescription_crud,
    pharmacy_crud, pharmacy_order_crud,
    lab_center_crud, lab_booking_crud
)
from app.crud.wallet import wallet_crud
from app.crud.business import business_crud
from app.core.exceptions import (
    NotFoundException, ValidationException, InsufficientBalanceException
)
from app.core.constants import TransactionType
from app.models.user import User
from app.models.health import (
    Consultation, Prescription, PharmacyOrder, LabBooking,
    ConsultationStatusEnum, PrescriptionStatusEnum
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
        # Create
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
            patient_gender=patient_gender
        )

        # Pay
        if payment_method == "wallet":
            wallet = wallet_crud.get_or_create_wallet(db, user_id=current_user.id)
            if wallet.balance < consultation.consultation_fee:
                db.delete(consultation)
                db.commit()
                raise InsufficientBalanceException()

            wallet_crud.debit_wallet(
                db, wallet_id=wallet.id,
                amount=consultation.consultation_fee,
                transaction_type=TransactionType.PAYMENT,
                description=f"Consultation with Dr. {consultation.doctor.last_name}",
                reference_id=str(consultation.id)
            )
            consultation.payment_status = "paid"
            consultation.status = ConsultationStatusEnum.CONFIRMED
            consultation.confirmed_at = datetime.utcnow()

            # Update doctor stats
            doctor = doctor_crud.get(db, id=doctor_id)
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
        # Validate prescription items vs ordered items
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

        # Pay
        if payment_method == "wallet":
            wallet = wallet_crud.get_or_create_wallet(db, user_id=current_user.id)
            if wallet.balance < order.total_amount:
                # Rollback stock & order
                for item in order.items:
                    db.query(
                        __import__('app.models.health', fromlist=['PharmacyProduct']).PharmacyProduct
                    ).filter_by(id=item.product_id).update({
                        "stock_quantity": __import__('app.models.health', fromlist=['PharmacyProduct']).PharmacyProduct.stock_quantity + item.quantity
                    })
                db.delete(order)
                db.commit()
                raise InsufficientBalanceException()

            wallet_crud.debit_wallet(
                db, wallet_id=wallet.id,
                amount=order.total_amount,
                transaction_type=TransactionType.PAYMENT,
                description=f"Pharmacy order at {order.pharmacy.name}",
                reference_id=str(order.id)
            )
            order.payment_status = "paid"
            order.status = "confirmed"
            order.confirmed_at = datetime.utcnow()

            # Update prescription
            if prescription_id:
                prescription = prescription_crud.get(db, id=prescription_id)
                prescription.fulfilled_order_id = order.id
                prescription.status = PrescriptionStatusEnum.FULFILLED

            # Update pharmacy stats
            pharmacy = pharmacy_crud.get(db, id=pharmacy_id)
            pharmacy.total_orders += 1

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
            wallet = wallet_crud.get_or_create_wallet(db, user_id=current_user.id)
            if wallet.balance < booking.total_amount:
                db.delete(booking)
                db.commit()
                raise InsufficientBalanceException()

            wallet_crud.debit_wallet(
                db, wallet_id=wallet.id,
                amount=booking.total_amount,
                transaction_type=TransactionType.PAYMENT,
                description=f"Lab test at {booking.lab_center.name}",
                reference_id=str(booking.id)
            )
            booking.payment_status = "paid"
            booking.status = "confirmed"
            booking.confirmed_at = datetime.utcnow()

            db.commit()
            db.refresh(booking)

        return booking


health_service = HealthService()