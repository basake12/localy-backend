from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime, date, time
from decimal import Decimal
from uuid import UUID

from app.schemas.common_schema import LocationSchema


# ============================================
# DOCTOR SCHEMAS
# ============================================

class DoctorCreateRequest(BaseModel):
    first_name: str = Field(..., min_length=2)
    last_name: str = Field(..., min_length=2)
    phone: Optional[str] = None
    email: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[date] = None
    specialization: str
    sub_specializations: List[str] = Field(default_factory=list)
    years_of_experience: int = Field(default=0, ge=0)
    medical_degree: Optional[str] = None
    registration_number: Optional[str] = None
    registration_council: Optional[str] = None
    qualifications: List[Dict[str, Any]] = Field(default_factory=list)
    hospital_name: Optional[str] = None
    hospital_address: Optional[str] = None
    hospital_location: Optional[LocationSchema] = None
    consultation_fee_video: Optional[Decimal] = Field(None, ge=0)
    consultation_fee_chat: Optional[Decimal] = Field(None, ge=0)
    consultation_fee_in_person: Optional[Decimal] = Field(None, ge=0)
    consultation_fee_phone: Optional[Decimal] = Field(None, ge=0)
    avg_consultation_duration_mins: int = Field(default=30, ge=10)
    profile_image: Optional[str] = None


class DoctorResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    specialization: str
    sub_specializations: List[str]
    years_of_experience: int
    hospital_name: Optional[str]
    consultation_fee_video: Optional[Decimal]
    consultation_fee_chat: Optional[Decimal]
    consultation_fee_in_person: Optional[Decimal]
    consultation_fee_phone: Optional[Decimal]
    is_online: bool
    is_available_for_instant: bool
    is_verified: bool
    average_rating: Decimal
    total_consultations: int
    total_reviews: int
    profile_image: Optional[str]
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class DoctorAvailabilityCreateRequest(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6)
    start_time: time
    end_time: time
    slot_duration_mins: int = Field(default=30, ge=10)
    available_types: List[str] = Field(default_factory=list)


class DoctorAvailabilityResponse(BaseModel):
    id: UUID
    doctor_id: UUID
    day_of_week: int
    start_time: time
    end_time: time
    slot_duration_mins: int
    available_types: List[str]
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class DoctorSearchFilters(BaseModel):
    query: Optional[str] = None
    specialization: Optional[str] = None
    location: Optional[LocationSchema] = None
    radius_km: Optional[float] = Field(None, gt=0)
    available_only: bool = False
    is_online: Optional[bool] = None
    max_fee: Optional[Decimal] = Field(None, ge=0)
    consultation_type: Optional[str] = None
    min_experience: Optional[int] = Field(None, ge=0)
    min_rating: Optional[Decimal] = Field(None, ge=0)
    is_verified: Optional[bool] = None


# ============================================
# CONSULTATION SCHEMAS
# ============================================

class ConsultationCreateRequest(BaseModel):
    doctor_id: UUID
    consultation_type: str
    consultation_date: date
    consultation_time: time
    patient_name: str = Field(..., min_length=2)
    patient_phone: str = Field(..., min_length=10)
    patient_dob: Optional[date] = None
    patient_gender: Optional[str] = None
    chief_complaint: str = Field(..., min_length=10)
    symptoms: List[str] = Field(default_factory=list)
    medical_history: Optional[str] = None
    allergies: Optional[str] = None
    current_medications: List[str] = Field(default_factory=list)
    payment_method: str = "wallet"

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "doctor_id": "uuid",
            "consultation_type": "video",
            "consultation_date": "2026-03-20",
            "consultation_time": "10:00",
            "patient_name": "John Doe",
            "patient_phone": "+2348012345678",
            "chief_complaint": "Persistent headache for 3 days",
            "symptoms": ["headache", "dizziness", "nausea"],
            "payment_method": "wallet"
        }
    })


class ConsultationResponse(BaseModel):
    id: UUID
    doctor_id: UUID
    patient_id: UUID
    consultation_type: str
    consultation_date: date
    consultation_time: time
    patient_name: str
    chief_complaint: str
    consultation_fee: Decimal
    status: str
    payment_status: str
    meeting_url: Optional[str]
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ConsultationNoteRequest(BaseModel):
    doctor_notes: Optional[str] = None
    diagnosis: Optional[str] = None
    treatment_plan: Optional[str] = None
    follow_up_date: Optional[date] = None
    follow_up_notes: Optional[str] = None


# ============================================
# PRESCRIPTION SCHEMAS
# ============================================

class PrescriptionCreateRequest(BaseModel):
    consultation_id: UUID
    medicines: List[Dict[str, Any]] = Field(..., min_length=1)
    doctor_notes: Optional[str] = None
    special_instructions: Optional[str] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "consultation_id": "uuid",
            "medicines": [
                {
                    "name": "Amoxicillin",
                    "dosage": "500mg",
                    "frequency": "3 times daily",
                    "duration": "7 days",
                    "quantity": 21,
                    "instructions": "Take after meals",
                    "refills_allowed": 0
                }
            ],
            "doctor_notes": "Patient has mild upper respiratory infection"
        }
    })


class PrescriptionResponse(BaseModel):
    id: UUID
    prescription_code: str
    consultation_id: UUID
    doctor_id: UUID
    patient_id: UUID
    medicines: List[Dict[str, Any]]
    doctor_notes: Optional[str]
    special_instructions: Optional[str]
    issued_at: Optional[datetime]
    expires_at: Optional[datetime]
    status: str
    model_config = ConfigDict(from_attributes=True)


# ============================================
# PHARMACY SCHEMAS
# ============================================

class PharmacyCreateRequest(BaseModel):
    name: str = Field(..., min_length=3)
    description: Optional[str] = None
    license_number: Optional[str] = None
    address: str = Field(..., min_length=10)
    city: str
    state: str
    location: LocationSchema
    opening_time: Optional[time] = None
    closing_time: Optional[time] = None
    is_24_hours: bool = False
    offers_delivery: bool = True
    offers_prescription_fulfillment: bool = True
    delivery_fee: Decimal = Field(default=Decimal("0"), ge=0)
    free_delivery_minimum: Optional[Decimal] = None
    delivery_radius_km: Optional[Decimal] = None
    avg_delivery_time_mins: Optional[int] = None
    images: List[str] = Field(default_factory=list)


class PharmacyResponse(BaseModel):
    id: UUID
    name: str
    address: str
    city: str
    state: str
    opening_time: Optional[time]
    closing_time: Optional[time]
    is_24_hours: bool
    offers_delivery: bool
    delivery_fee: Decimal
    avg_delivery_time_mins: Optional[int]
    is_verified: bool
    average_rating: Decimal
    total_orders: int
    images: List[str]
    model_config = ConfigDict(from_attributes=True)


class PharmacyProductCreateRequest(BaseModel):
    name: str = Field(..., min_length=2)
    generic_name: Optional[str] = None
    brand_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    dosage: Optional[str] = None
    form: Optional[str] = None
    unit: Optional[str] = None
    pack_size: Optional[str] = None
    price: Decimal = Field(..., gt=0)
    stock_quantity: int = Field(default=0, ge=0)
    requires_prescription: bool = False
    is_otc: bool = True
    manufacturer: Optional[str] = None
    contraindications: List[str] = Field(default_factory=list)
    side_effects: List[str] = Field(default_factory=list)
    image_url: Optional[str] = None


class PharmacyProductResponse(BaseModel):
    id: UUID
    pharmacy_id: UUID
    name: str
    generic_name: Optional[str]
    brand_name: Optional[str]
    category: Optional[str]
    dosage: Optional[str]
    form: Optional[str]
    pack_size: Optional[str]
    price: Decimal
    stock_quantity: int
    requires_prescription: bool
    is_otc: bool
    manufacturer: Optional[str]
    is_available: bool
    image_url: Optional[str]
    model_config = ConfigDict(from_attributes=True)


class PharmacyOrderCreateRequest(BaseModel):
    pharmacy_id: UUID
    order_type: str  # delivery, pickup
    items: List[Dict[str, Any]] = Field(..., min_length=1)
    # [{"product_id": "uuid", "quantity": 2}]
    prescription_id: Optional[UUID] = None
    delivery_address: Optional[str] = None
    delivery_location: Optional[LocationSchema] = None
    delivery_instructions: Optional[str] = None
    customer_name: str
    customer_phone: str
    payment_method: str = "wallet"


class PharmacyOrderResponse(BaseModel):
    id: UUID
    pharmacy_id: UUID
    customer_id: UUID
    order_type: str
    subtotal: Decimal
    delivery_fee: Decimal
    service_charge: Decimal
    total_amount: Decimal
    status: str
    payment_status: str
    prescription_id: Optional[UUID]
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# ============================================
# LAB SCHEMAS
# ============================================

class LabCenterCreateRequest(BaseModel):
    name: str = Field(..., min_length=3)
    description: Optional[str] = None
    license_number: Optional[str] = None
    accreditation: List[str] = Field(default_factory=list)
    address: str = Field(..., min_length=10)
    city: str
    state: str
    location: LocationSchema
    opening_time: Optional[time] = None
    closing_time: Optional[time] = None
    is_24_hours: bool = False
    offers_home_sample_collection: bool = False
    home_collection_fee: Decimal = Field(default=Decimal("0"), ge=0)
    home_collection_radius_km: Optional[Decimal] = None
    avg_result_time_hours: Optional[int] = None
    images: List[str] = Field(default_factory=list)


class LabCenterResponse(BaseModel):
    id: UUID
    name: str
    address: str
    city: str
    state: str
    accreditation: List[str]
    offers_home_sample_collection: bool
    home_collection_fee: Decimal
    avg_result_time_hours: Optional[int]
    is_verified: bool
    average_rating: Decimal
    images: List[str]
    model_config = ConfigDict(from_attributes=True)


class LabTestCreateRequest(BaseModel):
    name: str = Field(..., min_length=3)
    code: Optional[str] = None
    description: Optional[str] = None
    category: str
    conditions_detected: List[str] = Field(default_factory=list)
    parameters_tested: List[str] = Field(default_factory=list)
    price: Decimal = Field(..., gt=0)
    sample_type: Optional[str] = None
    fasting_required: bool = False
    fasting_hours: Optional[int] = None
    preparation_instructions: Optional[str] = None
    result_time_hours: Optional[int] = None
    includes_consultation: bool = False


class LabTestResponse(BaseModel):
    id: UUID
    lab_center_id: UUID
    name: str
    code: Optional[str]
    category: str
    description: Optional[str]
    price: Decimal
    sample_type: Optional[str]
    fasting_required: bool
    result_time_hours: Optional[int]
    is_available: bool
    model_config = ConfigDict(from_attributes=True)


class LabBookingCreateRequest(BaseModel):
    lab_center_id: UUID
    test_ids: List[UUID] = Field(..., min_length=1)
    appointment_date: date
    appointment_time: time
    sample_collection_type: str  # center, home
    home_address: Optional[str] = None
    home_location: Optional[LocationSchema] = None
    patient_name: str = Field(..., min_length=2)
    patient_phone: str = Field(..., min_length=10)
    patient_email: Optional[str] = None
    patient_dob: Optional[date] = None
    patient_gender: Optional[str] = None
    referring_doctor_id: Optional[UUID] = None
    doctor_notes: Optional[str] = None
    payment_method: str = "wallet"


class LabBookingResponse(BaseModel):
    id: UUID
    lab_center_id: UUID
    booking_reference: str
    patient_name: str
    tests: List[Dict[str, Any]]
    appointment_date: date
    appointment_time: time
    sample_collection_type: str
    total_amount: Decimal
    status: str
    payment_status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class LabResultResponse(BaseModel):
    id: UUID
    booking_id: UUID
    results: List[Dict[str, Any]]
    summary: Optional[str]
    overall_status: Optional[str]
    technician_name: Optional[str]
    doctor_interpretation: Optional[str]
    report_url: Optional[str]
    is_released: bool
    released_at: Optional[datetime]
    model_config = ConfigDict(from_attributes=True)


class LabCenterSearchFilters(BaseModel):
    query: Optional[str] = None
    location: Optional[LocationSchema] = None
    radius_km: Optional[float] = Field(None, gt=0)
    city: Optional[str] = None
    offers_home_collection: Optional[bool] = None
    is_verified: Optional[bool] = None
    test_category: Optional[str] = None