from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func, cast, String
from uuid import UUID
from datetime import date, time, timezone
from datetime import datetime
from decimal import Decimal
import random
import string

from app.crud.base_crud import CRUDBase
from app.models.health_model import (
    Doctor, DoctorAvailability, Consultation, Prescription,
    Pharmacy, PharmacyProduct, PharmacyOrder, PharmacyOrderItem,
    LabCenter, LabTest, LabBooking, LabResult,
    ConsultationStatusEnum, PrescriptionStatusEnum,
    PharmacyOrderStatusEnum, LabBookingStatusEnum
)
from app.core.exceptions import (
    NotFoundException, ValidationException
)

_UTC = timezone.utc


def _utcnow() -> datetime:
    """Timezone-aware UTC now (replaces deprecated datetime.utcnow)."""
    return datetime.now(_UTC)


# ============================================
# DOCTOR CRUD
# ============================================

class CRUDDoctor(CRUDBase[Doctor, dict, dict]):

    def get_by_business_id(self, db: Session, *, business_id: UUID) -> Optional[Doctor]:
        return db.query(Doctor).filter(Doctor.business_id == business_id).first()

    def search_doctors(
        self, db: Session, *,
        query_text: Optional[str] = None,
        specialization: Optional[str] = None,
        location: Optional[tuple] = None,
        radius_km: float = 5.0,
        is_online: Optional[bool] = None,
        max_fee: Optional[Decimal] = None,
        consultation_type: Optional[str] = None,
        min_experience: Optional[int] = None,
        min_rating: Optional[Decimal] = None,
        is_verified: Optional[bool] = None,
        skip: int = 0, limit: int = 20
    ) -> List[Doctor]:
        query = db.query(Doctor).filter(Doctor.is_active == True)

        if query_text:
            # FIX: cast Doctor.specialization (Enum column) to String before
            # applying ilike — PostgreSQL Enum types do not support ILIKE
            # directly; casting to ::text allows pattern matching.
            query = query.filter(or_(
                Doctor.first_name.ilike(f"%{query_text}%"),
                Doctor.last_name.ilike(f"%{query_text}%"),
                Doctor.hospital_name.ilike(f"%{query_text}%"),
                cast(Doctor.specialization, String).ilike(f"%{query_text}%")
            ))

        if specialization:
            query = query.filter(Doctor.specialization == specialization)

        if location:
            lat, lng = location
            point = func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)
            # FIX: Include doctors with no hospital_location — ST_DWithin(NULL) returns NULL → empty results
            query = query.filter(or_(
                Doctor.hospital_location.is_(None),
                func.ST_DWithin(Doctor.hospital_location, point, radius_km * 1000)
            ))

        if is_online is not None:
            query = query.filter(Doctor.is_online == is_online)

        if min_experience is not None:
            query = query.filter(Doctor.years_of_experience >= min_experience)

        if min_rating is not None:
            query = query.filter(Doctor.average_rating >= min_rating)

        if is_verified is not None:
            query = query.filter(Doctor.is_verified == is_verified)

        fee_column_map = {
            "video": Doctor.consultation_fee_video,
            "chat": Doctor.consultation_fee_chat,
            "in_person": Doctor.consultation_fee_in_person,
            "phone": Doctor.consultation_fee_phone
        }
        if max_fee and consultation_type and consultation_type in fee_column_map:
            query = query.filter(fee_column_map[consultation_type] <= max_fee)

        return query.order_by(
            Doctor.is_verified.desc(),
            Doctor.average_rating.desc()
        ).offset(skip).limit(limit).all()

    def set_online_status(
        self, db: Session, *,
        doctor_id: UUID,
        is_online: bool,
        is_available_for_instant: bool = False
    ) -> None:
        doctor = self.get(db, id=doctor_id)
        if doctor:
            doctor.is_online = is_online
            doctor.is_available_for_instant = is_available_for_instant
            db.commit()


class CRUDDoctorAvailability(CRUDBase[DoctorAvailability, dict, dict]):

    def get_by_doctor(
        self, db: Session, *,
        doctor_id: UUID,
        active_only: bool = True
    ) -> List[DoctorAvailability]:
        query = db.query(DoctorAvailability).filter(
            DoctorAvailability.doctor_id == doctor_id
        )
        if active_only:
            query = query.filter(DoctorAvailability.is_active == True)
        return query.order_by(
            DoctorAvailability.day_of_week,
            DoctorAvailability.start_time
        ).all()

    def get_available_slots(
        self, db: Session, *,
        doctor_id: UUID,
        target_date: date
    ) -> List[Dict[str, Any]]:
        """Return free time slots for a doctor on a specific date."""
        from datetime import timedelta, datetime as _dt

        day_of_week = target_date.weekday()

        availabilities = db.query(DoctorAvailability).filter(
            and_(
                DoctorAvailability.doctor_id == doctor_id,
                DoctorAvailability.day_of_week == day_of_week,
                DoctorAvailability.is_active == True
            )
        ).all()

        booked_times = db.query(Consultation.consultation_time).filter(
            and_(
                Consultation.doctor_id == doctor_id,
                Consultation.consultation_date == target_date,
                Consultation.status.in_(["pending", "confirmed", "in_progress"])
            )
        ).all()
        booked_set = {t[0] for t in booked_times}

        slots: List[Dict[str, Any]] = []
        for avail in availabilities:
            current = _dt.combine(target_date, avail.start_time)
            end = _dt.combine(target_date, avail.end_time)
            delta = timedelta(minutes=avail.slot_duration_mins)

            while current < end:
                slot_time = current.time()
                if slot_time not in booked_set:
                    slots.append({
                        "time": slot_time.strftime("%H:%M"),
                        "available_types": avail.available_types,
                        "duration_mins": avail.slot_duration_mins
                    })
                current += delta

        return slots


# ============================================
# CONSULTATION CRUD
# ============================================

class CRUDConsultation(CRUDBase[Consultation, dict, dict]):

    def create_consultation(
        self, db: Session, *,
        doctor_id: UUID, patient_id: UUID,
        consultation_type: str, consultation_date: date,
        consultation_time: time, patient_name: str,
        patient_phone: str, chief_complaint: str,
        symptoms: List[str],
        medical_history: Optional[str] = None,
        allergies: Optional[str] = None,
        current_medications: Optional[List[str]] = None,
        patient_dob: Optional[date] = None,
        patient_gender: Optional[str] = None
    ) -> Consultation:
        doctor = doctor_crud.get(db, id=doctor_id)
        if not doctor or not doctor.is_active:
            raise NotFoundException("Doctor")

        conflict = db.query(Consultation).filter(
            and_(
                Consultation.doctor_id == doctor_id,
                Consultation.consultation_date == consultation_date,
                Consultation.consultation_time == consultation_time,
                Consultation.status.in_(["pending", "confirmed", "in_progress"])
            )
        ).first()
        if conflict:
            raise ValidationException("This time slot is already booked")

        fee_map = {
            "video": doctor.consultation_fee_video,
            "chat": doctor.consultation_fee_chat,
            "in_person": doctor.consultation_fee_in_person,
            "phone": doctor.consultation_fee_phone
        }
        fee = fee_map.get(consultation_type)
        if fee is None:
            raise ValidationException(
                f"Doctor does not offer {consultation_type} consultations"
            )

        consultation = Consultation(
            doctor_id=doctor_id,
            patient_id=patient_id,
            consultation_type=consultation_type,
            consultation_date=consultation_date,
            consultation_time=consultation_time,
            patient_name=patient_name,
            patient_phone=patient_phone,
            patient_dob=patient_dob,
            patient_gender=patient_gender,
            chief_complaint=chief_complaint,
            symptoms=symptoms,
            medical_history=medical_history,
            allergies=allergies,
            current_medications=current_medications or [],
            consultation_fee=fee
        )
        db.add(consultation)
        db.commit()
        db.refresh(consultation)
        return consultation

    def get_patient_consultations(
        self, db: Session, *,
        patient_id: UUID,
        skip: int = 0, limit: int = 20
    ) -> List[Consultation]:
        return (
            db.query(Consultation)
            .options(joinedload(Consultation.doctor))
            .filter(Consultation.patient_id == patient_id)
            .order_by(Consultation.created_at.desc())
            .offset(skip).limit(limit).all()
        )

    def get_doctor_consultations(
        self, db: Session, *,
        doctor_id: UUID,
        target_date: Optional[date] = None,
        status: Optional[str] = None,
        skip: int = 0, limit: int = 50
    ) -> List[Consultation]:
        query = db.query(Consultation).filter(
            Consultation.doctor_id == doctor_id
        )
        if target_date:
            query = query.filter(Consultation.consultation_date == target_date)
        if status:
            query = query.filter(Consultation.status == status)
        return query.order_by(
            Consultation.consultation_date,
            Consultation.consultation_time
        ).offset(skip).limit(limit).all()


# ============================================
# PRESCRIPTION CRUD
# ============================================

class CRUDPrescription(CRUDBase[Prescription, dict, dict]):

    def _generate_code(self, db: Session) -> str:
        for _ in range(20):  # Guard against infinite loop
            code = "RX" + "".join(
                random.choices(string.ascii_uppercase + string.digits, k=10)
            )
            if not db.query(Prescription).filter(
                Prescription.prescription_code == code
            ).first():
                return code
        raise ValidationException("Could not generate unique prescription code")

    def create_prescription(
        self, db: Session, *,
        consultation_id: UUID, doctor_id: UUID, patient_id: UUID,
        medicines: List[Dict],
        doctor_notes: Optional[str] = None,
        special_instructions: Optional[str] = None
    ) -> Prescription:
        consultation = consultation_crud.get(db, id=consultation_id)
        if not consultation:
            raise NotFoundException("Consultation")

        existing = db.query(Prescription).filter(
            Prescription.consultation_id == consultation_id
        ).first()
        if existing:
            raise ValidationException(
                "Prescription already exists for this consultation"
            )

        now = _utcnow()
        from datetime import timedelta
        prescription = Prescription(
            consultation_id=consultation_id,
            doctor_id=doctor_id,
            patient_id=patient_id,
            prescription_code=self._generate_code(db),
            medicines=medicines,
            doctor_notes=doctor_notes,
            special_instructions=special_instructions,
            issued_at=now,
            expires_at=now + timedelta(days=30),
            status=PrescriptionStatusEnum.ISSUED
        )
        db.add(prescription)
        db.flush()

        doctor = doctor_crud.get(db, id=doctor_id)
        if doctor:
            doctor.total_prescriptions += 1

        db.commit()
        db.refresh(prescription)
        return prescription

    def get_patient_prescriptions(
        self, db: Session, *,
        patient_id: UUID,
        skip: int = 0, limit: int = 20
    ) -> List[Prescription]:
        return (
            db.query(Prescription)
            .filter(Prescription.patient_id == patient_id)
            .order_by(Prescription.issued_at.desc())
            .offset(skip).limit(limit).all()
        )


# ============================================
# PHARMACY CRUD
# ============================================

class CRUDPharmacy(CRUDBase[Pharmacy, dict, dict]):

    def get_by_business_id(
        self, db: Session, *, business_id: UUID
    ) -> Optional[Pharmacy]:
        return db.query(Pharmacy).filter(
            Pharmacy.business_id == business_id
        ).first()

    def search_pharmacies(
        self, db: Session, *,
        query_text: Optional[str] = None,
        location: Optional[tuple] = None,
        radius_km: float = 5.0,
        city: Optional[str] = None,
        offers_delivery: Optional[bool] = None,
        offers_prescription_fulfillment: Optional[bool] = None,
        skip: int = 0, limit: int = 20
    ) -> List[Pharmacy]:
        query = db.query(Pharmacy).filter(Pharmacy.is_active == True)

        if query_text:
            query = query.filter(or_(
                Pharmacy.name.ilike(f"%{query_text}%"),
                Pharmacy.address.ilike(f"%{query_text}%"),
                Pharmacy.city.ilike(f"%{query_text}%")
            ))
        if city:
            query = query.filter(Pharmacy.city.ilike(f"%{city}%"))
        if location:
            lat, lng = location
            point = func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)
            # FIX: Include pharmacies with no location — ST_DWithin(NULL) returns NULL → empty results
            query = query.filter(or_(
                Pharmacy.location.is_(None),
                func.ST_DWithin(Pharmacy.location, point, radius_km * 1000)
            ))
        if offers_delivery is not None:
            query = query.filter(Pharmacy.offers_delivery == offers_delivery)
        if offers_prescription_fulfillment is not None:
            query = query.filter(
                Pharmacy.offers_prescription_fulfillment ==
                offers_prescription_fulfillment
            )

        return query.order_by(
            Pharmacy.is_verified.desc(),
            Pharmacy.average_rating.desc()
        ).offset(skip).limit(limit).all()


class CRUDPharmacyProduct(CRUDBase[PharmacyProduct, dict, dict]):

    def get_by_pharmacy(
        self, db: Session, *,
        pharmacy_id: UUID,
        category: Optional[str] = None,
        query_text: Optional[str] = None,
        requires_prescription: Optional[bool] = None,
        in_stock_only: bool = True,
        skip: int = 0, limit: int = 50
    ) -> List[PharmacyProduct]:
        query = db.query(PharmacyProduct).filter(
            and_(
                PharmacyProduct.pharmacy_id == pharmacy_id,
                PharmacyProduct.is_active == True
            )
        )
        if category:
            query = query.filter(
                PharmacyProduct.category.ilike(f"%{category}%")
            )
        if query_text:
            query = query.filter(or_(
                PharmacyProduct.name.ilike(f"%{query_text}%"),
                PharmacyProduct.generic_name.ilike(f"%{query_text}%"),
                PharmacyProduct.brand_name.ilike(f"%{query_text}%"),
                PharmacyProduct.category.ilike(f"%{query_text}%")
            ))
        if requires_prescription is not None:
            query = query.filter(
                PharmacyProduct.requires_prescription == requires_prescription
            )
        if in_stock_only:
            query = query.filter(PharmacyProduct.stock_quantity > 0)
        return query.order_by(
            PharmacyProduct.popularity_score.desc()
        ).offset(skip).limit(limit).all()


class CRUDPharmacyOrder(CRUDBase[PharmacyOrder, dict, dict]):

    def create_order(
        self, db: Session, *,
        pharmacy_id: UUID, customer_id: UUID,
        items: List[Dict], order_type: str,
        customer_name: str, customer_phone: str,
        prescription_id: Optional[UUID] = None,
        delivery_address: Optional[str] = None,
        delivery_location: Optional[Dict] = None,
        delivery_instructions: Optional[str] = None
    ) -> PharmacyOrder:
        pharmacy = pharmacy_crud.get(db, id=pharmacy_id)
        if not pharmacy:
            raise NotFoundException("Pharmacy")

        subtotal = Decimal("0")
        order_items_data = []

        for item in items:
            product = db.query(PharmacyProduct).filter(
                and_(
                    PharmacyProduct.id == item["product_id"],
                    PharmacyProduct.pharmacy_id == pharmacy_id
                )
            ).first()
            if not product:
                raise ValidationException(
                    f"Product {item['product_id']} not found in this pharmacy"
                )
            if (
                not product.is_available
                or product.stock_quantity < item["quantity"]
            ):
                raise ValidationException(
                    f"{product.name} is not available in the requested quantity"
                )

            item_total = product.price * item["quantity"]
            subtotal += item_total
            order_items_data.append({
                "product_id": product.id,
                "quantity": item["quantity"],
                "unit_price": product.price,
                "total_price": item_total,
                "product_name": product.name,
                "product_snapshot": {
                    "dosage": product.dosage,
                    "form": product.form,
                    "pack_size": product.pack_size,
                    "manufacturer": product.manufacturer
                },
                "from_prescription": prescription_id is not None
            })

        delivery_fee = (
            pharmacy.delivery_fee if order_type == "delivery" else Decimal("0")
        )
        if (
            pharmacy.free_delivery_minimum
            and subtotal >= pharmacy.free_delivery_minimum
        ):
            delivery_fee = Decimal("0")

        # FIX: Blueprint §4.4 — ₦50 flat fee on pharmacy (product) orders.
        # The old 5% service_charge is not specified in the blueprint. Removed.
        service_charge = Decimal("0.00")
        from app.core.constants import PLATFORM_FEE_STANDARD
        platform_fee = PLATFORM_FEE_STANDARD   # ₦50
        total_amount = subtotal + delivery_fee + platform_fee

        from geoalchemy2.elements import WKTElement
        loc = None
        if delivery_location:
            loc = WKTElement(
                f"POINT({delivery_location['longitude']} "
                f"{delivery_location['latitude']})",
                srid=4326
            )

        order = PharmacyOrder(
            pharmacy_id=pharmacy_id,
            customer_id=customer_id,
            prescription_id=prescription_id,
            order_type=order_type,
            delivery_address=delivery_address,
            delivery_location=loc,
            delivery_instructions=delivery_instructions,
            customer_name=customer_name,
            customer_phone=customer_phone,
            subtotal=subtotal,
            delivery_fee=delivery_fee,
            service_charge=service_charge,
            total_amount=total_amount
        )
        db.add(order)
        db.flush()

        for item_data in order_items_data:
            db.add(PharmacyOrderItem(order_id=order.id, **item_data))
            db.query(PharmacyProduct).filter(
                PharmacyProduct.id == item_data["product_id"]
            ).update({
                "stock_quantity": (
                    PharmacyProduct.stock_quantity - item_data["quantity"]
                ),
                "total_sold": (
                    PharmacyProduct.total_sold + item_data["quantity"]
                ),
                "popularity_score": PharmacyProduct.popularity_score + 1
            })

        if prescription_id:
            prescription = db.get(Prescription, prescription_id)
            if prescription:
                prescription.status = PrescriptionStatusEnum.PENDING_FULFILLMENT
                prescription.fulfilled_pharmacy_id = pharmacy_id

        db.commit()
        db.refresh(order)
        return order

    def get_customer_orders(
        self, db: Session, *,
        customer_id: UUID,
        skip: int = 0, limit: int = 20
    ) -> List[PharmacyOrder]:
        return (
            db.query(PharmacyOrder)
            .options(joinedload(PharmacyOrder.items))
            .filter(PharmacyOrder.customer_id == customer_id)
            .order_by(PharmacyOrder.created_at.desc())
            .offset(skip).limit(limit).all()
        )

    def get_pharmacy_orders(
        self, db: Session, *,
        pharmacy_id: UUID,
        status: Optional[str] = None,
        skip: int = 0, limit: int = 50
    ) -> List[PharmacyOrder]:
        query = (
            db.query(PharmacyOrder)
            .options(joinedload(PharmacyOrder.items))
            .filter(PharmacyOrder.pharmacy_id == pharmacy_id)
        )
        if status:
            query = query.filter(PharmacyOrder.status == status)
        return query.order_by(
            PharmacyOrder.created_at.desc()
        ).offset(skip).limit(limit).all()


# ============================================
# LAB CRUD
# ============================================

class CRUDLabCenter(CRUDBase[LabCenter, dict, dict]):

    def get_by_business_id(
        self, db: Session, *, business_id: UUID
    ) -> Optional[LabCenter]:
        return db.query(LabCenter).filter(
            LabCenter.business_id == business_id
        ).first()

    def search_lab_centers(
        self, db: Session, *,
        query_text: Optional[str] = None,
        location: Optional[tuple] = None,
        radius_km: float = 5.0,
        city: Optional[str] = None,
        offers_home_collection: Optional[bool] = None,
        is_verified: Optional[bool] = None,
        skip: int = 0, limit: int = 20
    ) -> List[LabCenter]:
        query = db.query(LabCenter).filter(LabCenter.is_active == True)

        if query_text:
            query = query.filter(or_(
                LabCenter.name.ilike(f"%{query_text}%"),
                LabCenter.city.ilike(f"%{query_text}%")
            ))
        if city:
            query = query.filter(LabCenter.city.ilike(f"%{city}%"))
        if location:
            lat, lng = location
            point = func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)
            # FIX: Include lab centers with no location — ST_DWithin(NULL) returns NULL → empty results
            query = query.filter(or_(
                LabCenter.location.is_(None),
                func.ST_DWithin(LabCenter.location, point, radius_km * 1000)
            ))
        if offers_home_collection is not None:
            query = query.filter(
                LabCenter.offers_home_sample_collection == offers_home_collection
            )
        if is_verified is not None:
            query = query.filter(LabCenter.is_verified == is_verified)

        return query.order_by(
            LabCenter.is_verified.desc(),
            LabCenter.average_rating.desc()
        ).offset(skip).limit(limit).all()


class CRUDLabTest(CRUDBase[LabTest, dict, dict]):

    def get_by_lab_center(
        self, db: Session, *,
        lab_center_id: UUID,
        category: Optional[str] = None,
        query_text: Optional[str] = None,
        skip: int = 0, limit: int = 50
    ) -> List[LabTest]:
        query = db.query(LabTest).filter(
            and_(
                LabTest.lab_center_id == lab_center_id,
                LabTest.is_active == True
            )
        )
        if category:
            query = query.filter(LabTest.category == category)
        if query_text:
            query = query.filter(or_(
                LabTest.name.ilike(f"%{query_text}%"),
                LabTest.description.ilike(f"%{query_text}%")
            ))
        return query.order_by(
            LabTest.popularity_score.desc()
        ).offset(skip).limit(limit).all()


class CRUDLabBooking(CRUDBase[LabBooking, dict, dict]):

    def _generate_reference(self, db: Session) -> str:
        for _ in range(20):  # Guard against infinite loop
            code = "LAB" + "".join(
                random.choices(string.ascii_uppercase + string.digits, k=8)
            )
            if not db.query(LabBooking).filter(
                LabBooking.booking_reference == code
            ).first():
                return code
        raise ValidationException("Could not generate unique booking reference")

    def create_booking(
        self, db: Session, *,
        lab_center_id: UUID, customer_id: UUID,
        test_ids: List[UUID], appointment_date: date,
        appointment_time: time, sample_collection_type: str,
        patient_name: str, patient_phone: str,
        patient_email: Optional[str] = None,
        patient_dob: Optional[date] = None,
        patient_gender: Optional[str] = None,
        referring_doctor_id: Optional[UUID] = None,
        doctor_notes: Optional[str] = None,
        home_address: Optional[str] = None,
        home_location: Optional[Dict] = None
    ) -> LabBooking:
        lab = lab_center_crud.get(db, id=lab_center_id)
        if not lab:
            raise NotFoundException("Lab center")

        tests_data = []
        subtotal = Decimal("0")
        for test_id in test_ids:
            test = db.query(LabTest).filter(
                and_(
                    LabTest.id == test_id,
                    LabTest.lab_center_id == lab_center_id,
                    LabTest.is_available == True
                )
            ).first()
            if not test:
                raise ValidationException(
                    f"Test {test_id} not available at this lab"
                )
            subtotal += test.price
            tests_data.append({
                "test_id": str(test.id),
                "test_name": test.name,
                "price": float(test.price)
            })

        home_fee = Decimal("0")
        if sample_collection_type == "home":
            if not lab.offers_home_sample_collection:
                raise ValidationException(
                    "This lab does not offer home sample collection"
                )
            home_fee = lab.home_collection_fee

        # FIX: Blueprint §4.4 — ₦100 booking fee on health appointments.
        # The old 5% service_charge is not specified in the blueprint. Removed.
        service_charge = Decimal("0.00")
        from app.core.constants import PLATFORM_FEE_BOOKING
        platform_fee = PLATFORM_FEE_BOOKING   # ₦100
        total_amount = subtotal + home_fee + platform_fee

        from geoalchemy2.elements import WKTElement
        loc = None
        if home_location:
            loc = WKTElement(
                f"POINT({home_location['longitude']} "
                f"{home_location['latitude']})",
                srid=4326
            )

        booking = LabBooking(
            lab_center_id=lab_center_id,
            customer_id=customer_id,
            booking_reference=self._generate_reference(db),
            tests=tests_data,
            appointment_date=appointment_date,
            appointment_time=appointment_time,
            sample_collection_type=sample_collection_type,
            home_address=home_address,
            home_location=loc,
            patient_name=patient_name,
            patient_phone=patient_phone,
            patient_email=patient_email,
            patient_dob=patient_dob,
            patient_gender=patient_gender,
            referring_doctor_id=referring_doctor_id,
            doctor_notes=doctor_notes,
            subtotal=subtotal,
            home_collection_fee=home_fee,
            service_charge=service_charge,
            total_amount=total_amount
        )
        db.add(booking)
        db.flush()

        for test_id in test_ids:
            db.query(LabTest).filter(LabTest.id == test_id).update({
                "total_bookings": LabTest.total_bookings + 1,
                "popularity_score": LabTest.popularity_score + 1
            })

        lab.total_bookings += 1
        db.commit()
        db.refresh(booking)
        return booking

    def get_customer_bookings(
        self, db: Session, *,
        customer_id: UUID,
        skip: int = 0, limit: int = 20
    ) -> List[LabBooking]:
        return (
            db.query(LabBooking)
            .options(
                joinedload(LabBooking.lab_center),
                joinedload(LabBooking.result)
            )
            .filter(LabBooking.customer_id == customer_id)
            .order_by(LabBooking.created_at.desc())
            .offset(skip).limit(limit).all()
        )

    def get_lab_bookings(
        self, db: Session, *,
        lab_center_id: UUID,
        status: Optional[str] = None,
        target_date: Optional[date] = None,
        skip: int = 0, limit: int = 50
    ) -> List[LabBooking]:
        query = db.query(LabBooking).filter(
            LabBooking.lab_center_id == lab_center_id
        )
        if status:
            query = query.filter(LabBooking.status == status)
        if target_date:
            query = query.filter(LabBooking.appointment_date == target_date)
        return query.order_by(
            LabBooking.appointment_date,
            LabBooking.appointment_time
        ).offset(skip).limit(limit).all()


class CRUDLabResult(CRUDBase[LabResult, dict, dict]):

    def create_result(
        self, db: Session, *,
        booking_id: UUID,
        results: List[Dict],
        summary: Optional[str] = None,
        overall_status: Optional[str] = None,
        technician_name: Optional[str] = None,
        technician_notes: Optional[str] = None,
        report_url: Optional[str] = None
    ) -> LabResult:
        existing = db.query(LabResult).filter(
            LabResult.booking_id == booking_id
        ).first()
        if existing:
            raise ValidationException(
                "Result already exists for this booking"
            )

        result = LabResult(
            booking_id=booking_id,
            results=results,
            summary=summary,
            overall_status=overall_status,
            technician_name=technician_name,
            technician_notes=technician_notes,
            report_url=report_url
        )
        db.add(result)
        db.flush()

        booking = lab_booking_crud.get(db, id=booking_id)
        if booking:
            booking.status = LabBookingStatusEnum.RESULTS_READY
            booking.results_ready_at = _utcnow()

        db.commit()
        db.refresh(result)
        return result

    def release_result(self, db: Session, *, result_id: UUID) -> LabResult:
        result = self.get(db, id=result_id)
        if not result:
            raise NotFoundException("Lab result")

        result.is_released = True
        result.released_at = _utcnow()

        booking = lab_booking_crud.get(db, id=result.booking_id)
        if booking:
            booking.status = LabBookingStatusEnum.COMPLETED

        db.commit()
        db.refresh(result)
        return result


# ============================================
# SINGLETONS
# ============================================

doctor_crud = CRUDDoctor(Doctor)
doctor_availability_crud = CRUDDoctorAvailability(DoctorAvailability)
consultation_crud = CRUDConsultation(Consultation)
prescription_crud = CRUDPrescription(Prescription)
pharmacy_crud = CRUDPharmacy(Pharmacy)
pharmacy_product_crud = CRUDPharmacyProduct(PharmacyProduct)
pharmacy_order_crud = CRUDPharmacyOrder(PharmacyOrder)
lab_center_crud = CRUDLabCenter(LabCenter)
lab_test_crud = CRUDLabTest(LabTest)
lab_booking_crud = CRUDLabBooking(LabBooking)
lab_result_crud = CRUDLabResult(LabResult)