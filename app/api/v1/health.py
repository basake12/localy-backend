from fastapi import APIRouter, Depends, Query, status, Body
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
from datetime import date, datetime, timezone
from decimal import Decimal
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.dependencies import (
    get_current_active_user, require_customer,
    require_business, get_pagination_params
)
from app.schemas.common_schema import SuccessResponse
from app.schemas.health_schema import (
    DoctorCreateRequest, DoctorResponse,
    DoctorAvailabilityCreateRequest, DoctorAvailabilityResponse,
    DoctorSearchFilters,
    ConsultationCreateRequest, ConsultationResponse, ConsultationNoteRequest,
    PrescriptionCreateRequest, PrescriptionResponse,
    PharmacyCreateRequest, PharmacyResponse,
    PharmacyProductCreateRequest, PharmacyProductResponse,
    PharmacyOrderCreateRequest, PharmacyOrderResponse,
    LabCenterCreateRequest, LabCenterResponse,
    LabTestCreateRequest, LabTestResponse,
    LabBookingCreateRequest, LabBookingResponse,
    LabResultResponse, LabCenterSearchFilters
)
from app.services.health_service import health_service
from app.crud.health_crud import (
    doctor_crud, doctor_availability_crud, consultation_crud,
    prescription_crud, pharmacy_crud, pharmacy_product_crud,
    pharmacy_order_crud, lab_center_crud, lab_test_crud,
    lab_booking_crud, lab_result_crud
)
from app.models.business_model import Business
from app.models.user_model import User
from app.models.health_model import (
    LabResult, Consultation as ConsultModel,
    DoctorSpecializationEnum
)
from app.core.exceptions import (
    NotFoundException, PermissionDeniedException, ValidationException
)
from geoalchemy2.elements import WKTElement
from sqlalchemy import func
import uuid as _uuid

router = APIRouter()

# ────────────────────────────────────────────
# SHARED UTC HELPER
# ────────────────────────────────────────────

_UTC = timezone.utc


def _utcnow() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(_UTC)


# ────────────────────────────────────────────
# FIX: SYNC BUSINESS LOOKUP
# ────────────────────────────────────────────
# business_crud is an AsyncCRUDBase — calling its methods without `await`
# in a sync router returns a coroutine object, not the Business instance.
# Accessing any attribute on that coroutine (e.g. .category, .id) raises:
#   AttributeError: 'coroutine' object has no attribute 'category'
# Fix: query Business directly with the sync Session everywhere in this router.

def _get_business_sync(db: Session, user_id: UUID) -> Optional[Business]:
    """Sync Business lookup by user_id — safe to call from a sync router."""
    return db.query(Business).filter(Business.user_id == user_id).first()


# ────────────────────────────────────────────
# INLINE REQUEST SCHEMAS FOR SIMPLE PAYLOADS
# ────────────────────────────────────────────

class PharmacySearchRequest(BaseModel):
    query: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    radius_km: float = 20.0
    city: Optional[str] = None
    offers_delivery: Optional[bool] = None
    offers_prescription_fulfillment: Optional[bool] = None


class RatingRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    review: Optional[str] = None


class CancelRequest(BaseModel):
    reason: Optional[str] = None


class PharmacyProductUpdateRequest(BaseModel):
    price: Optional[Decimal] = Field(None, gt=0)
    stock_quantity: Optional[int] = Field(None, ge=0)
    is_available: Optional[bool] = None


# ============================================
# DOCTOR DISCOVERY (PUBLIC)
# NOTE: Static sub-paths (/specializations, /my, /availability/my,
#       /consultations/my) MUST be registered BEFORE /doctors/{doctor_id}
#       to prevent FastAPI from routing them as path-param matches.
# ============================================

@router.get("/doctors/specializations", response_model=SuccessResponse[List[str]])
def get_specializations() -> dict:
    """List all supported doctor specializations."""
    return {"success": True, "data": [s.value for s in DoctorSpecializationEnum]}


@router.post(
    "/doctors/search",
    response_model=SuccessResponse[List[DoctorResponse]]
)
def search_doctors(
    *,
    db: Session = Depends(get_db),
    filters: DoctorSearchFilters,
    pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Search doctors by specialization, location, availability, fee."""
    location = None
    if filters.location:
        location = (filters.location.latitude, filters.location.longitude)

    doctors = doctor_crud.search_doctors(
        db,
        query_text=filters.query,
        specialization=filters.specialization,
        location=location,
        radius_km=filters.radius_km or 20.0,
        is_online=filters.is_online,
        max_fee=filters.max_fee,
        consultation_type=filters.consultation_type,
        min_experience=filters.min_experience,
        min_rating=filters.min_rating,
        is_verified=filters.is_verified,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )
    return {"success": True, "data": doctors}


@router.get("/doctors/{doctor_id}", response_model=SuccessResponse[DoctorResponse])
def get_doctor_details(
    *, db: Session = Depends(get_db), doctor_id: UUID
) -> dict:
    doctor = doctor_crud.get(db, id=doctor_id)
    if not doctor:
        raise NotFoundException("Doctor")
    return {"success": True, "data": doctor}


@router.get(
    "/doctors/{doctor_id}/slots",
    response_model=SuccessResponse[List[dict]]
)
def get_doctor_available_slots(
    *,
    db: Session = Depends(get_db),
    doctor_id: UUID,
    target_date: date = Query(...)
) -> dict:
    """Return available booking slots for a doctor on a given date."""
    slots = doctor_availability_crud.get_available_slots(
        db, doctor_id=doctor_id, target_date=target_date
    )
    return {"success": True, "data": slots}


# ============================================
# DOCTOR MANAGEMENT (BUSINESS)
# Static paths first, then parameterised.
# ============================================

@router.get(
    "/doctors/my/profile",
    response_model=SuccessResponse[DoctorResponse]
)
def get_my_doctor_profile(
    *, db: Session = Depends(get_db),
    current_user: User = Depends(require_business)
) -> dict:
    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    doctor = doctor_crud.get_by_business_id(db, business_id=business.id)
    if not doctor:
        raise NotFoundException("Doctor profile")
    return {"success": True, "data": doctor}


@router.post(
    "/doctors",
    response_model=SuccessResponse[DoctorResponse],
    status_code=status.HTTP_201_CREATED
)
def create_doctor(
    *,
    db: Session = Depends(get_db),
    doctor_in: DoctorCreateRequest,
    current_user: User = Depends(require_business)
) -> dict:
    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    if business.category != "health":
        raise ValidationException(
            "Only health category businesses can create doctor profiles"
        )

    data = doctor_in.model_dump()
    data["business_id"] = business.id

    if doctor_in.hospital_location:
        data["hospital_location"] = WKTElement(
            f"POINT({doctor_in.hospital_location.longitude} "
            f"{doctor_in.hospital_location.latitude})",
            srid=4326
        )

    doctor = doctor_crud.create_from_dict(db, obj_in=data)
    return {"success": True, "data": doctor}


@router.post(
    "/doctors/availability",
    response_model=SuccessResponse[DoctorAvailabilityResponse],
    status_code=status.HTTP_201_CREATED
)
def add_availability(
    *,
    db: Session = Depends(get_db),
    avail_in: DoctorAvailabilityCreateRequest,
    current_user: User = Depends(require_business)
) -> dict:
    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    doctor = doctor_crud.get_by_business_id(db, business_id=business.id)
    if not doctor:
        raise NotFoundException("Doctor profile")

    data = avail_in.model_dump()
    data["doctor_id"] = doctor.id
    avail = doctor_availability_crud.create_from_dict(db, obj_in=data)
    return {"success": True, "data": avail}


@router.get(
    "/doctors/availability/my",
    response_model=SuccessResponse[List[DoctorAvailabilityResponse]]
)
def get_my_availabilities(
    *, db: Session = Depends(get_db),
    current_user: User = Depends(require_business)
) -> dict:
    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    doctor = doctor_crud.get_by_business_id(db, business_id=business.id)
    if not doctor:
        raise NotFoundException("Doctor profile")
    avails = doctor_availability_crud.get_by_doctor(db, doctor_id=doctor.id)
    return {"success": True, "data": avails}


@router.put("/doctors/online-status")
def update_online_status(
    *,
    db: Session = Depends(get_db),
    is_online: bool = Query(...),
    is_available_for_instant: bool = Query(default=False),
    current_user: User = Depends(require_business)
) -> dict:
    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    doctor = doctor_crud.get_by_business_id(db, business_id=business.id)
    if not doctor:
        raise NotFoundException("Doctor profile")
    doctor_crud.set_online_status(
        db,
        doctor_id=doctor.id,
        is_online=is_online,
        is_available_for_instant=is_available_for_instant
    )
    return {
        "success": True,
        "data": {
            "is_online": is_online,
            "is_available_for_instant": is_available_for_instant
        }
    }


# ============================================
# DOCTOR CONSULTATION DASHBOARD (BUSINESS)
# ============================================

@router.get(
    "/doctors/consultations/dashboard",
    response_model=SuccessResponse[List[ConsultationResponse]]
)
def get_doctor_consultations(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
    target_date: Optional[date] = Query(None),
    consultation_status: Optional[str] = Query(None),
    pagination: dict = Depends(get_pagination_params)
) -> dict:
    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    doctor = doctor_crud.get_by_business_id(db, business_id=business.id)
    if not doctor:
        raise NotFoundException("Doctor profile")

    consultations = consultation_crud.get_doctor_consultations(
        db,
        doctor_id=doctor.id,
        target_date=target_date,
        status=consultation_status,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )
    return {"success": True, "data": consultations}


@router.post(
    "/consultations/{consultation_id}/start",
    response_model=SuccessResponse[ConsultationResponse]
)
def start_consultation(
    *,
    db: Session = Depends(get_db),
    consultation_id: UUID,
    current_user: User = Depends(require_business)
) -> dict:
    consultation = consultation_crud.get(db, id=consultation_id)
    if not consultation:
        raise NotFoundException("Consultation")

    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    doctor = doctor_crud.get_by_business_id(db, business_id=business.id)
    if not doctor or consultation.doctor_id != doctor.id:
        raise PermissionDeniedException()

    consultation.status = "in_progress"
    consultation.started_at = _utcnow()

    if consultation.consultation_type in ["video", "chat"]:
        consultation.room_id = str(_uuid.uuid4())
        consultation.meeting_url = f"/health/rooms/{consultation.room_id}"

    db.commit()
    db.refresh(consultation)
    return {"success": True, "data": consultation}


@router.post(
    "/consultations/{consultation_id}/end",
    response_model=SuccessResponse[ConsultationResponse]
)
def end_consultation(
    *,
    db: Session = Depends(get_db),
    consultation_id: UUID,
    current_user: User = Depends(require_business)
) -> dict:
    consultation = consultation_crud.get(db, id=consultation_id)
    if not consultation:
        raise NotFoundException("Consultation")

    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    doctor = doctor_crud.get_by_business_id(db, business_id=business.id)
    if not doctor or consultation.doctor_id != doctor.id:
        raise PermissionDeniedException()

    if consultation.status != "in_progress":
        raise ValidationException("Consultation is not in progress")

    consultation.status = "completed"
    consultation.ended_at = _utcnow()
    db.commit()
    db.refresh(consultation)
    return {"success": True, "data": consultation}


@router.post(
    "/consultations/{consultation_id}/notes",
    response_model=SuccessResponse[ConsultationResponse]
)
def add_consultation_notes(
    *,
    db: Session = Depends(get_db),
    consultation_id: UUID,
    notes: ConsultationNoteRequest,
    current_user: User = Depends(require_business)
) -> dict:
    consultation = consultation_crud.get(db, id=consultation_id)
    if not consultation:
        raise NotFoundException("Consultation")

    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    doctor = doctor_crud.get_by_business_id(db, business_id=business.id)
    if not doctor or consultation.doctor_id != doctor.id:
        raise PermissionDeniedException()

    if notes.doctor_notes is not None:
        consultation.doctor_notes = notes.doctor_notes
    if notes.diagnosis is not None:
        consultation.diagnosis = notes.diagnosis
    if notes.treatment_plan is not None:
        consultation.treatment_plan = notes.treatment_plan
    if notes.follow_up_date is not None:
        consultation.follow_up_date = notes.follow_up_date
    if notes.follow_up_notes is not None:
        consultation.follow_up_notes = notes.follow_up_notes

    db.commit()
    db.refresh(consultation)
    return {"success": True, "data": consultation}


# ============================================
# PRESCRIPTIONS (DOCTOR / BUSINESS)
# ============================================

@router.post(
    "/prescriptions",
    response_model=SuccessResponse[PrescriptionResponse],
    status_code=status.HTTP_201_CREATED
)
def issue_prescription(
    *,
    db: Session = Depends(get_db),
    prescription_in: PrescriptionCreateRequest,
    current_user: User = Depends(require_business)
) -> dict:
    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    doctor = doctor_crud.get_by_business_id(db, business_id=business.id)
    if not doctor:
        raise NotFoundException("Doctor profile")

    consultation = consultation_crud.get(
        db, id=prescription_in.consultation_id
    )
    if not consultation or consultation.doctor_id != doctor.id:
        raise PermissionDeniedException()

    prescription = prescription_crud.create_prescription(
        db,
        consultation_id=prescription_in.consultation_id,
        doctor_id=doctor.id,
        patient_id=consultation.patient_id,
        medicines=prescription_in.medicines,
        doctor_notes=prescription_in.doctor_notes,
        special_instructions=prescription_in.special_instructions
    )
    return {"success": True, "data": prescription}


# ============================================
# CONSULTATIONS (CUSTOMER)
# ============================================

@router.post(
    "/consultations",
    response_model=SuccessResponse[ConsultationResponse],
    status_code=status.HTTP_201_CREATED
)
def book_consultation(
    *,
    db: Session = Depends(get_db),
    consult_in: ConsultationCreateRequest,
    current_user: User = Depends(require_customer)
) -> dict:
    """Book consultation — validates slot, charges wallet."""
    consultation = health_service.book_consultation_and_pay(
        db,
        current_user=current_user,
        doctor_id=consult_in.doctor_id,
        consultation_type=consult_in.consultation_type,
        consultation_date=consult_in.consultation_date,
        consultation_time=consult_in.consultation_time,
        patient_name=consult_in.patient_name,
        patient_phone=consult_in.patient_phone,
        chief_complaint=consult_in.chief_complaint,
        symptoms=consult_in.symptoms,
        medical_history=consult_in.medical_history,
        allergies=consult_in.allergies,
        current_medications=consult_in.current_medications,
        patient_dob=consult_in.patient_dob,
        patient_gender=consult_in.patient_gender,
        payment_method=consult_in.payment_method
    )
    return {"success": True, "data": consultation}


@router.get(
    "/consultations/customer",
    response_model=SuccessResponse[List[ConsultationResponse]]
)
def get_my_consultations_customer(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
    pagination: dict = Depends(get_pagination_params)
) -> dict:
    consultations = consultation_crud.get_patient_consultations(
        db,
        patient_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )
    return {"success": True, "data": consultations}


@router.post(
    "/consultations/{consultation_id}/cancel",
    response_model=SuccessResponse[ConsultationResponse]
)
def cancel_consultation(
    *,
    db: Session = Depends(get_db),
    consultation_id: UUID,
    body: CancelRequest,
    current_user: User = Depends(require_customer)
) -> dict:
    consultation = consultation_crud.get(db, id=consultation_id)
    if not consultation:
        raise NotFoundException("Consultation")
    if consultation.patient_id != current_user.id:
        raise PermissionDeniedException()
    if consultation.status in ["completed", "cancelled", "in_progress"]:
        raise ValidationException("Cannot cancel this consultation")

    consultation.status = "cancelled"
    consultation.cancelled_at = _utcnow()
    consultation.cancellation_reason = body.reason
    db.commit()
    db.refresh(consultation)
    return {"success": True, "data": consultation}


@router.post("/consultations/{consultation_id}/rate")
def rate_consultation(
    *,
    db: Session = Depends(get_db),
    consultation_id: UUID,
    body: RatingRequest,
    current_user: User = Depends(require_customer)
) -> dict:
    consultation = consultation_crud.get(db, id=consultation_id)
    if not consultation:
        raise NotFoundException("Consultation")
    if consultation.patient_id != current_user.id:
        raise PermissionDeniedException()
    if consultation.status != "completed":
        raise ValidationException("Can only rate completed consultations")

    consultation.rating = body.rating
    consultation.review = body.review

    doctor = doctor_crud.get(db, id=consultation.doctor_id)
    avg = db.query(func.avg(ConsultModel.rating)).filter(
        ConsultModel.doctor_id == consultation.doctor_id,
        ConsultModel.rating.isnot(None)
    ).scalar()
    count = db.query(func.count(ConsultModel.id)).filter(
        ConsultModel.doctor_id == consultation.doctor_id,
        ConsultModel.rating.isnot(None)
    ).scalar()
    if doctor:
        doctor.average_rating = round(avg, 2) if avg else 0
        doctor.total_reviews = count

    db.commit()
    return {"success": True, "data": {"rating": body.rating, "review": body.review}}


@router.get(
    "/prescriptions/my",
    response_model=SuccessResponse[List[PrescriptionResponse]]
)
def get_my_prescriptions(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
    pagination: dict = Depends(get_pagination_params)
) -> dict:
    prescriptions = prescription_crud.get_patient_prescriptions(
        db,
        patient_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )
    return {"success": True, "data": prescriptions}


# ============================================
# PHARMACY — PUBLIC
# ============================================

@router.post(
    "/pharmacies/search",
    response_model=SuccessResponse[List[PharmacyResponse]]
)
def search_pharmacies(
    *,
    db: Session = Depends(get_db),
    filters: PharmacySearchRequest,
    pagination: dict = Depends(get_pagination_params)
) -> dict:
    loc = None
    if filters.lat is not None and filters.lng is not None:
        loc = (filters.lat, filters.lng)

    pharmacies = pharmacy_crud.search_pharmacies(
        db,
        query_text=filters.query,
        location=loc,
        radius_km=filters.radius_km,
        city=filters.city,
        offers_delivery=filters.offers_delivery,
        offers_prescription_fulfillment=filters.offers_prescription_fulfillment,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )
    return {"success": True, "data": pharmacies}


@router.get(
    "/pharmacies/{pharmacy_id}",
    response_model=SuccessResponse[PharmacyResponse]
)
def get_pharmacy_details(
    *, db: Session = Depends(get_db), pharmacy_id: UUID
) -> dict:
    pharmacy = pharmacy_crud.get(db, id=pharmacy_id)
    if not pharmacy:
        raise NotFoundException("Pharmacy")
    return {"success": True, "data": pharmacy}


@router.get(
    "/pharmacies/{pharmacy_id}/products",
    response_model=SuccessResponse[List[PharmacyProductResponse]]
)
def get_pharmacy_products(
    *,
    db: Session = Depends(get_db),
    pharmacy_id: UUID,
    category: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    in_stock_only: bool = Query(default=True),
    pagination: dict = Depends(get_pagination_params)
) -> dict:
    products = pharmacy_product_crud.get_by_pharmacy(
        db,
        pharmacy_id=pharmacy_id,
        category=category,
        query_text=query,
        in_stock_only=in_stock_only,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )
    return {"success": True, "data": products}


# ============================================
# PHARMACY — BUSINESS
# ============================================

@router.post(
    "/pharmacies",
    response_model=SuccessResponse[PharmacyResponse],
    status_code=status.HTTP_201_CREATED
)
def create_pharmacy(
    *,
    db: Session = Depends(get_db),
    pharmacy_in: PharmacyCreateRequest,
    current_user: User = Depends(require_business)
) -> dict:
    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business or business.category != "health":
        raise ValidationException(
            "Only health category businesses can create pharmacies"
        )

    data = pharmacy_in.model_dump()
    data["business_id"] = business.id
    data["location"] = WKTElement(
        f"POINT({pharmacy_in.location.longitude} "
        f"{pharmacy_in.location.latitude})",
        srid=4326
    )
    pharmacy = pharmacy_crud.create_from_dict(db, obj_in=data)
    return {"success": True, "data": pharmacy}


@router.post(
    "/pharmacies/products",
    response_model=SuccessResponse[PharmacyProductResponse],
    status_code=status.HTTP_201_CREATED
)
def add_pharmacy_product(
    *,
    db: Session = Depends(get_db),
    product_in: PharmacyProductCreateRequest,
    current_user: User = Depends(require_business)
) -> dict:
    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    pharmacy = pharmacy_crud.get_by_business_id(db, business_id=business.id)
    if not pharmacy:
        raise NotFoundException("Pharmacy")

    data = product_in.model_dump()
    data["pharmacy_id"] = pharmacy.id
    product = pharmacy_product_crud.create_from_dict(db, obj_in=data)
    return {"success": True, "data": product}


@router.put(
    "/pharmacies/products/{product_id}",
    response_model=SuccessResponse[PharmacyProductResponse]
)
def update_pharmacy_product(
    *,
    db: Session = Depends(get_db),
    product_id: UUID,
    update_in: PharmacyProductUpdateRequest,
    current_user: User = Depends(require_business)
) -> dict:
    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    pharmacy = pharmacy_crud.get_by_business_id(db, business_id=business.id)
    if not pharmacy:
        raise NotFoundException("Pharmacy")

    product = pharmacy_product_crud.get(db, id=product_id)
    if not product or product.pharmacy_id != pharmacy.id:
        raise NotFoundException("Product")

    update: dict = update_in.model_dump(exclude_none=True)
    product = pharmacy_product_crud.update(db, db_obj=product, obj_in=update)
    return {"success": True, "data": product}


@router.get(
    "/pharmacies/orders/business",
    response_model=SuccessResponse[List[PharmacyOrderResponse]]
)
def get_pharmacy_orders_business(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
    order_status: Optional[str] = Query(None),
    pagination: dict = Depends(get_pagination_params)
) -> dict:
    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    pharmacy = pharmacy_crud.get_by_business_id(db, business_id=business.id)
    if not pharmacy:
        raise NotFoundException("Pharmacy")

    orders = pharmacy_order_crud.get_pharmacy_orders(
        db,
        pharmacy_id=pharmacy.id,
        status=order_status,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )
    return {"success": True, "data": orders}


@router.post("/pharmacies/orders/{order_id}/preparing")
def mark_order_preparing(
    *,
    db: Session = Depends(get_db),
    order_id: UUID,
    current_user: User = Depends(require_business)
) -> dict:
    order = pharmacy_order_crud.get(db, id=order_id)
    if not order:
        raise NotFoundException("Order")
    order.status = "preparing"
    db.commit()
    return {"success": True, "data": {"status": "preparing"}}


@router.post("/pharmacies/orders/{order_id}/ready")
def mark_order_ready(
    *,
    db: Session = Depends(get_db),
    order_id: UUID,
    current_user: User = Depends(require_business)
) -> dict:
    order = pharmacy_order_crud.get(db, id=order_id)
    if not order:
        raise NotFoundException("Order")
    order.status = "ready"
    order.prepared_at = _utcnow()
    db.commit()
    return {"success": True, "data": {"status": "ready"}}


# ============================================
# PHARMACY ORDERS — CUSTOMER
# ============================================

@router.post(
    "/pharmacies/orders",
    response_model=SuccessResponse[PharmacyOrderResponse],
    status_code=status.HTTP_201_CREATED
)
def place_pharmacy_order(
    *,
    db: Session = Depends(get_db),
    order_in: PharmacyOrderCreateRequest,
    current_user: User = Depends(require_customer)
) -> dict:
    loc = None
    if order_in.delivery_location:
        loc = {
            "latitude": order_in.delivery_location.latitude,
            "longitude": order_in.delivery_location.longitude
        }

    order = health_service.place_pharmacy_order_and_pay(
        db,
        current_user=current_user,
        pharmacy_id=order_in.pharmacy_id,
        items=order_in.items,
        order_type=order_in.order_type,
        customer_name=order_in.customer_name,
        customer_phone=order_in.customer_phone,
        prescription_id=order_in.prescription_id,
        delivery_address=order_in.delivery_address,
        delivery_location=loc,
        delivery_instructions=order_in.delivery_instructions,
        payment_method=order_in.payment_method
    )
    return {"success": True, "data": order}


@router.get(
    "/pharmacies/orders/customer",
    response_model=SuccessResponse[List[PharmacyOrderResponse]]
)
def get_my_pharmacy_orders(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
    pagination: dict = Depends(get_pagination_params)
) -> dict:
    orders = pharmacy_order_crud.get_customer_orders(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )
    return {"success": True, "data": orders}


# ============================================
# LAB — PUBLIC
# ============================================

@router.post(
    "/labs/search",
    response_model=SuccessResponse[List[LabCenterResponse]]
)
def search_labs(
    *,
    db: Session = Depends(get_db),
    filters: LabCenterSearchFilters,
    pagination: dict = Depends(get_pagination_params)
) -> dict:
    location = None
    if filters.location:
        location = (filters.location.latitude, filters.location.longitude)

    labs = lab_center_crud.search_lab_centers(
        db,
        query_text=filters.query,
        location=location,
        radius_km=filters.radius_km or 20.0,
        city=filters.city,
        offers_home_collection=filters.offers_home_collection,
        is_verified=filters.is_verified,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )
    return {"success": True, "data": labs}


@router.get(
    "/labs/{lab_center_id}/tests",
    response_model=SuccessResponse[List[LabTestResponse]]
)
def get_lab_tests(
    *,
    db: Session = Depends(get_db),
    lab_center_id: UUID,
    category: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    pagination: dict = Depends(get_pagination_params)
) -> dict:
    tests = lab_test_crud.get_by_lab_center(
        db,
        lab_center_id=lab_center_id,
        category=category,
        query_text=query,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )
    return {"success": True, "data": tests}


# ============================================
# LAB — BUSINESS
# ============================================

@router.post(
    "/labs",
    response_model=SuccessResponse[LabCenterResponse],
    status_code=status.HTTP_201_CREATED
)
def create_lab_center(
    *,
    db: Session = Depends(get_db),
    lab_in: LabCenterCreateRequest,
    current_user: User = Depends(require_business)
) -> dict:
    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business or business.category != "health":
        raise ValidationException(
            "Only health category businesses can create lab centers"
        )

    data = lab_in.model_dump()
    data["business_id"] = business.id
    data["location"] = WKTElement(
        f"POINT({lab_in.location.longitude} {lab_in.location.latitude})",
        srid=4326
    )
    lab = lab_center_crud.create_from_dict(db, obj_in=data)
    return {"success": True, "data": lab}


@router.post(
    "/labs/tests",
    response_model=SuccessResponse[LabTestResponse],
    status_code=status.HTTP_201_CREATED
)
def add_lab_test(
    *,
    db: Session = Depends(get_db),
    test_in: LabTestCreateRequest,
    current_user: User = Depends(require_business)
) -> dict:
    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    lab = lab_center_crud.get_by_business_id(db, business_id=business.id)
    if not lab:
        raise NotFoundException("Lab center")

    data = test_in.model_dump()
    data["lab_center_id"] = lab.id
    test = lab_test_crud.create_from_dict(db, obj_in=data)
    return {"success": True, "data": test}


@router.get(
    "/labs/bookings/business",
    response_model=SuccessResponse[List[LabBookingResponse]]
)
def get_lab_bookings_business(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
    target_date: Optional[date] = Query(None),
    booking_status: Optional[str] = Query(None),
    pagination: dict = Depends(get_pagination_params)
) -> dict:
    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    lab = lab_center_crud.get_by_business_id(db, business_id=business.id)
    if not lab:
        raise NotFoundException("Lab center")

    bookings = lab_booking_crud.get_lab_bookings(
        db,
        lab_center_id=lab.id,
        status=booking_status,
        target_date=target_date,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )
    return {"success": True, "data": bookings}


@router.post("/labs/bookings/{booking_id}/sample-collected")
def mark_sample_collected(
    *,
    db: Session = Depends(get_db),
    booking_id: UUID,
    current_user: User = Depends(require_business)
) -> dict:
    booking = lab_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")
    booking.status = "sample_collected"
    booking.sample_collected_at = _utcnow()
    db.commit()
    return {"success": True, "data": {"status": "sample_collected"}}


@router.post(
    "/labs/results",
    response_model=SuccessResponse[LabResultResponse],
    status_code=status.HTTP_201_CREATED
)
def upload_lab_result(
    *,
    db: Session = Depends(get_db),
    booking_id: UUID = Body(...),
    results: List[dict] = Body(...),
    summary: Optional[str] = Body(None),
    overall_status: Optional[str] = Body(None),
    technician_name: Optional[str] = Body(None),
    report_url: Optional[str] = Body(None),
    current_user: User = Depends(require_business)
) -> dict:
    result = lab_result_crud.create_result(
        db,
        booking_id=booking_id,
        results=results,
        summary=summary,
        overall_status=overall_status,
        technician_name=technician_name,
        report_url=report_url
    )
    return {"success": True, "data": result}


@router.post(
    "/labs/results/{result_id}/release",
    response_model=SuccessResponse[LabResultResponse]
)
def release_lab_result(
    *,
    db: Session = Depends(get_db),
    result_id: UUID,
    current_user: User = Depends(require_business)
) -> dict:
    result = lab_result_crud.release_result(db, result_id=result_id)
    return {"success": True, "data": result}


# ============================================
# LAB BOOKINGS — CUSTOMER
# ============================================

@router.post(
    "/labs/bookings",
    response_model=SuccessResponse[LabBookingResponse],
    status_code=status.HTTP_201_CREATED
)
def book_lab_test(
    *,
    db: Session = Depends(get_db),
    booking_in: LabBookingCreateRequest,
    current_user: User = Depends(require_customer)
) -> dict:
    loc = None
    if booking_in.home_location:
        loc = {
            "latitude": booking_in.home_location.latitude,
            "longitude": booking_in.home_location.longitude
        }

    booking = health_service.book_lab_and_pay(
        db,
        current_user=current_user,
        lab_center_id=booking_in.lab_center_id,
        test_ids=booking_in.test_ids,
        appointment_date=booking_in.appointment_date,
        appointment_time=booking_in.appointment_time,
        sample_collection_type=booking_in.sample_collection_type,
        patient_name=booking_in.patient_name,
        patient_phone=booking_in.patient_phone,
        patient_email=booking_in.patient_email,
        patient_dob=booking_in.patient_dob,
        patient_gender=booking_in.patient_gender,
        referring_doctor_id=booking_in.referring_doctor_id,
        doctor_notes=booking_in.doctor_notes,
        home_address=booking_in.home_address,
        home_location=loc,
        payment_method=booking_in.payment_method
    )
    return {"success": True, "data": booking}


@router.get(
    "/labs/bookings/customer",
    response_model=SuccessResponse[List[LabBookingResponse]]
)
def get_my_lab_bookings(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
    pagination: dict = Depends(get_pagination_params)
) -> dict:
    bookings = lab_booking_crud.get_customer_bookings(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )
    return {"success": True, "data": bookings}


@router.get(
    "/labs/bookings/{booking_id}/result",
    response_model=SuccessResponse[LabResultResponse]
)
def get_my_lab_result(
    *,
    db: Session = Depends(get_db),
    booking_id: UUID,
    current_user: User = Depends(require_customer)
) -> dict:
    booking = lab_booking_crud.get(db, id=booking_id)
    if not booking or booking.customer_id != current_user.id:
        raise NotFoundException("Booking")

    result = db.query(LabResult).filter(
        LabResult.booking_id == booking_id
    ).first()
    if not result or not result.is_released:
        raise ValidationException("Results are not yet available")

    return {"success": True, "data": result}