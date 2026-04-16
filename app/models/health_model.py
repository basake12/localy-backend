from sqlalchemy import (
    Column, String, Boolean, Enum, Text, Integer,
    Numeric, ForeignKey, Date, Time, DateTime, CheckConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from geoalchemy2 import Geography
import enum

from app.models.base_model import BaseModel


# ============================================
# ENUMS
# ============================================

class DoctorSpecializationEnum(str, enum.Enum):
    GENERAL_PRACTITIONER = "general_practitioner"
    CARDIOLOGIST = "cardiologist"
    NEUROLOGIST = "neurologist"
    ORTHOPEDIST = "orthopedist"
    DERMATOLOGIST = "dermatologist"
    PEDIATRICIAN = "pediatrician"
    GYNECOLOGIST = "gynecologist"
    OPHTHALMOLOGIST = "ophthalmologist"
    DENTIST = "dentist"
    PSYCHIATRIST = "psychiatrist"
    SURGEON = "surgeon"
    PHARMACIST = "pharmacist"
    DIETITIAN = "dietitian"
    PHYSIOTHERAPIST = "physiotherapist"
    RADIOLOGIST = "radiologist"
    UROLOGIST = "urologist"
    ENDOCRINOLOGIST = "endocrinologist"
    ALLERGIST = "allergist"
    ONCOLOGIST = "oncologist"
    OBSTETRICIAN = "obstetrician"


class ConsultationTypeEnum(str, enum.Enum):
    VIDEO = "video"
    CHAT = "chat"
    IN_PERSON = "in_person"
    PHONE = "phone"


class ConsultationStatusEnum(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class PrescriptionStatusEnum(str, enum.Enum):
    ISSUED = "issued"
    PENDING_FULFILLMENT = "pending_fulfillment"
    FULFILLED = "fulfilled"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class PharmacyOrderStatusEnum(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PREPARING = "preparing"
    READY = "ready"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class LabBookingStatusEnum(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SAMPLE_COLLECTED = "sample_collected"
    PROCESSING = "processing"
    RESULTS_READY = "results_ready"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class LabTestCategoryEnum(str, enum.Enum):
    BLOOD = "blood"
    URINE = "urine"
    IMAGING = "imaging"
    STOOL = "stool"
    SWAB = "swab"
    BIOPSY = "biopsy"
    ALLERGY = "allergy"
    GENETIC = "genetic"
    HORMONAL = "hormonal"
    COMPREHENSIVE = "comprehensive"


# ============================================
# DOCTOR MODEL
# ============================================

class Doctor(BaseModel):
    """Doctor profiles"""
    __tablename__ = "doctors"

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    # Personal
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=True)
    email = Column(String(255), nullable=True)
    gender = Column(String(10), nullable=True)
    date_of_birth = Column(Date, nullable=True)
    nationality = Column(String(100), nullable=True)
    languages = Column(JSONB, default=list)

    # Professional
    specialization = Column(
        Enum(DoctorSpecializationEnum),
        nullable=False,
        index=True
    )
    sub_specializations = Column(JSONB, default=list)
    years_of_experience = Column(Integer, default=0)
    medical_degree = Column(String(100), nullable=True)      # MBBS, MD, etc
    registration_number = Column(String(100), unique=True, nullable=True)
    registration_council = Column(String(100), nullable=True) # NMC, etc

    # Qualifications & Certifications
    qualifications = Column(JSONB, default=list)
    # [{"degree": "MBBS", "institution": "University of Lagos", "year": 2015}]
    certifications = Column(JSONB, default=list)

    # Practice Info
    hospital_name = Column(String(255), nullable=True)
    hospital_address = Column(Text, nullable=True)
    hospital_location = Column(Geography(geometry_type='POINT', srid=4326, spatial_index=True), nullable=True)
    clinic_address = Column(Text, nullable=True)

    # Consultation Settings
    consultation_fee_video = Column(Numeric(10, 2), nullable=True)
    consultation_fee_chat = Column(Numeric(10, 2), nullable=True)
    consultation_fee_in_person = Column(Numeric(10, 2), nullable=True)
    consultation_fee_phone = Column(Numeric(10, 2), nullable=True)
    avg_consultation_duration_mins = Column(Integer, default=30)
    accepts_walk_ins = Column(Boolean, default=False)

    # Online Status
    is_online = Column(Boolean, default=False)
    is_available_for_instant = Column(Boolean, default=False)

    # Media
    profile_image = Column(Text, nullable=True)
    intro_video_url = Column(Text, nullable=True)

    # Stats
    total_consultations = Column(Integer, default=0)
    average_rating = Column(Numeric(3, 2), default=0.00)
    total_reviews = Column(Integer, default=0)
    total_prescriptions = Column(Integer, default=0)

    # Status
    is_verified = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    # Relationships
    business = relationship("Business", back_populates="doctor")
    availabilities = relationship(
        "DoctorAvailability",
        back_populates="doctor",
        cascade="all, delete-orphan"
    )
    consultations = relationship(
        "Consultation",
        back_populates="doctor"
    )
    prescriptions = relationship(
        "Prescription",
        back_populates="doctor"
    )

    def __repr__(self):
        return f"<Doctor {self.first_name} {self.last_name}>"


# ============================================
# DOCTOR AVAILABILITY
# ============================================

class DoctorAvailability(BaseModel):
    """Doctor's weekly availability schedule"""
    __tablename__ = "doctor_availabilities"

    doctor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("doctors.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Day of week (0=Monday, 6=Sunday)
    day_of_week = Column(Integer, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    # Slot duration in minutes
    slot_duration_mins = Column(Integer, default=30)

    # Consultation types available this slot
    available_types = Column(JSONB, default=list)  # ["video", "chat", "in_person"]

    is_active = Column(Boolean, default=True)

    # Relationships
    doctor = relationship("Doctor", back_populates="availabilities")

    __table_args__ = (
        CheckConstraint('day_of_week >= 0 AND day_of_week <= 6', name='valid_day_of_week'),
        CheckConstraint('slot_duration_mins > 0', name='positive_slot_duration'),
    )


# ============================================
# CONSULTATION MODEL
# ============================================

class Consultation(BaseModel):
    """Doctor-patient consultations"""
    __tablename__ = "consultations"

    doctor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("doctors.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    patient_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Consultation Details
    consultation_type = Column(
        Enum(ConsultationTypeEnum),
        nullable=False,
        index=True
    )
    consultation_date = Column(Date, nullable=False, index=True)
    consultation_time = Column(Time, nullable=False)
    duration_mins = Column(Integer, nullable=True)

    # Patient Info Snapshot
    patient_name = Column(String(200), nullable=False)
    patient_phone = Column(String(20), nullable=False)
    patient_dob = Column(Date, nullable=True)
    patient_gender = Column(String(10), nullable=True)

    # Medical Context
    chief_complaint = Column(Text, nullable=False)
    symptoms = Column(JSONB, default=list)
    medical_history = Column(Text, nullable=True)
    allergies = Column(Text, nullable=True)
    current_medications = Column(JSONB, default=list)
    vitals = Column(JSONB, nullable=True)
    # {"blood_pressure": "120/80", "heart_rate": 72, "temperature": 36.6, "weight_kg": 75}

    # Consultation Notes
    doctor_notes = Column(Text, nullable=True)
    diagnosis = Column(Text, nullable=True)
    treatment_plan = Column(Text, nullable=True)
    follow_up_date = Column(Date, nullable=True)
    follow_up_notes = Column(Text, nullable=True)

    # Video/Chat Room
    room_id = Column(String(100), nullable=True)       # Video room reference
    meeting_url = Column(Text, nullable=True)

    # Pricing
    consultation_fee = Column(Numeric(10, 2), nullable=False)
    platform_fee = Column(Numeric(10, 2), nullable=False, default=0)
    total_amount = Column(Numeric(10, 2), nullable=False, default=0)
    payment_status = Column(String(20), default="pending")
    payment_reference = Column(String(100), nullable=True)

    # Status
    status = Column(
        Enum(ConsultationStatusEnum),
        default=ConsultationStatusEnum.PENDING,
        nullable=False,
        index=True
    )

    # Timestamps
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)

    # Rating
    rating = Column(Integer, nullable=True)           # 1-5
    review = Column(Text, nullable=True)

    # Relationships
    doctor = relationship("Doctor", back_populates="consultations")
    patient = relationship("User", foreign_keys=[patient_id])
    prescription = relationship(
        "Prescription",
        back_populates="consultation",
        uselist=False
    )

    __table_args__ = (
        CheckConstraint('consultation_fee >= 0', name='non_negative_consult_fee'),
    )


# ============================================
# PRESCRIPTION MODEL
# ============================================

class Prescription(BaseModel):
    """Prescriptions issued by doctors"""
    __tablename__ = "prescriptions"

    consultation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("consultations.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )
    doctor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("doctors.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    patient_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Prescription Reference
    prescription_code = Column(String(20), unique=True, nullable=False, index=True)

    # Medicines
    medicines = Column(JSONB, nullable=False)
    # [
    #   {
    #     "name": "Amoxicillin",
    #     "dosage": "500mg",
    #     "frequency": "3 times daily",
    #     "duration": "7 days",
    #     "quantity": 21,
    #     "instructions": "Take after meals",
    #     "refills_allowed": 0
    #   }
    # ]

    # Doctor Notes
    doctor_notes = Column(Text, nullable=True)
    special_instructions = Column(Text, nullable=True)

    # Validity
    issued_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)  # Usually 30 days

    # Fulfillment
    fulfilled_pharmacy_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pharmacies.id", ondelete="SET NULL"),
        nullable=True
    )
    fulfilled_order_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pharmacy_orders.id", ondelete="SET NULL", use_alter=True, name="fk_prescription_fulfilled_order"),
        nullable=True
    )

    # Status
    status = Column(
        Enum(PrescriptionStatusEnum),
        default=PrescriptionStatusEnum.ISSUED,
        nullable=False,
        index=True
    )

    # Relationships
    consultation = relationship("Consultation", back_populates="prescription")
    doctor = relationship("Doctor", back_populates="prescriptions")
    patient = relationship("User", foreign_keys=[patient_id])
    fulfilled_pharmacy = relationship("Pharmacy", foreign_keys=[fulfilled_pharmacy_id])
    # Relationship to orders that reference this prescription
    pharmacy_orders = relationship(
        "PharmacyOrder",
        foreign_keys="[PharmacyOrder.prescription_id]",
        back_populates="prescription"
    )
    # Relationship to the order that fulfilled this prescription
    fulfilled_order = relationship(
        "PharmacyOrder",
        foreign_keys="[Prescription.fulfilled_order_id]",
        back_populates="fulfilled_prescription"
    )


# ============================================
# PHARMACY MODEL
# ============================================

class Pharmacy(BaseModel):
    """Pharmacy listings"""
    __tablename__ = "pharmacies"

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    # Basic Info
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    license_number = Column(String(100), nullable=True)

    # Location
    address = Column(Text, nullable=False)
    city = Column(String(100), nullable=False, index=True)
    state = Column(String(100), nullable=False)
    location = Column(Geography(geometry_type='POINT', srid=4326, spatial_index=True), nullable=False)

    # Hours
    opening_time = Column(Time, nullable=True)
    closing_time = Column(Time, nullable=True)
    is_24_hours = Column(Boolean, default=False)

    # Services
    offers_delivery = Column(Boolean, default=True)
    offers_prescription_fulfillment = Column(Boolean, default=True)
    offers_teleconsultation_referral = Column(Boolean, default=False)
    delivery_fee = Column(Numeric(10, 2), default=0.00)
    free_delivery_minimum = Column(Numeric(10, 2), nullable=True)
    delivery_radius_km = Column(Numeric(5, 2), nullable=True)
    avg_delivery_time_mins = Column(Integer, nullable=True)

    # Media
    images = Column(JSONB, default=list)
    banner_image = Column(Text, nullable=True)

    # Stats
    total_orders = Column(Integer, default=0)
    average_rating = Column(Numeric(3, 2), default=0.00)
    total_reviews = Column(Integer, default=0)

    # Status
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)

    # Relationships
    business = relationship("Business", back_populates="pharmacy")
    products = relationship(
        "PharmacyProduct",
        back_populates="pharmacy",
        cascade="all, delete-orphan"
    )
    orders = relationship(
        "PharmacyOrder",
        back_populates="pharmacy"
    )


# ============================================
# PHARMACY PRODUCT MODEL
# ============================================

class PharmacyProduct(BaseModel):
    """Medicines and health products in pharmacy"""
    __tablename__ = "pharmacy_products"

    pharmacy_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pharmacies.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Product Details
    name = Column(String(255), nullable=False, index=True)
    generic_name = Column(String(255), nullable=True)
    brand_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)  # Antibiotics, Painkillers, Vitamins, etc
    sub_category = Column(String(100), nullable=True)

    # Dosage & Form
    dosage = Column(String(100), nullable=True)       # 500mg, 200ml, etc
    form = Column(String(50), nullable=True)          # Tablet, Capsule, Syrup, Injection
    unit = Column(String(50), nullable=True)          # Per Tablet, Per Pack, Per Bottle
    pack_size = Column(String(50), nullable=True)     # 30 tablets, 100ml

    # Pricing & Stock
    price = Column(Numeric(10, 2), nullable=False)
    cost_price = Column(Numeric(10, 2), nullable=True)
    stock_quantity = Column(Integer, default=0)
    reorder_level = Column(Integer, default=10)

    # Classification
    requires_prescription = Column(Boolean, default=False)
    is_otc = Column(Boolean, default=True)  # Over The Counter
    is_controlled_substance = Column(Boolean, default=False)

    # Medical Info
    manufacturer = Column(String(255), nullable=True)
    expiry_date = Column(Date, nullable=True)
    batch_number = Column(String(100), nullable=True)
    barcode = Column(String(100), nullable=True)

    # Interactions & Warnings
    contraindications = Column(JSONB, default=list)   # Conditions it can't be used with
    side_effects = Column(JSONB, default=list)
    drug_interactions = Column(JSONB, default=list)   # Other drugs it interacts with
    warnings = Column(Text, nullable=True)

    # Media
    image_url = Column(Text, nullable=True)
    images = Column(JSONB, default=list)

    # Status
    is_available = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)

    # Stats
    total_sold = Column(Integer, default=0)
    popularity_score = Column(Numeric(5, 2), default=0.00)

    # Relationships
    pharmacy = relationship("Pharmacy", back_populates="products")


# ============================================
# PHARMACY ORDER MODEL
# ============================================

class PharmacyOrder(BaseModel):
    """Orders placed at pharmacies"""
    __tablename__ = "pharmacy_orders"

    pharmacy_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pharmacies.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    prescription_id = Column(
        UUID(as_uuid=True),
        ForeignKey("prescriptions.id", ondelete="SET NULL"),
        nullable=True  # Can order without prescription for OTC
    )

    # Order Type
    order_type = Column(String(20), nullable=False)  # delivery, pickup

    # Delivery Info
    delivery_address = Column(Text, nullable=True)
    delivery_location = Column(Geography(geometry_type='POINT', srid=4326, spatial_index=True), nullable=True)
    delivery_instructions = Column(Text, nullable=True)

    # Customer Info
    customer_name = Column(String(200), nullable=False)
    customer_phone = Column(String(20), nullable=False)

    # Pricing
    subtotal = Column(Numeric(12, 2), nullable=False)
    delivery_fee = Column(Numeric(10, 2), default=0.00)
    service_charge = Column(Numeric(10, 2), default=0.00)
    discount = Column(Numeric(10, 2), default=0.00)
    total_amount = Column(Numeric(12, 2), nullable=False)

    # Payment
    payment_method = Column(String(20), default="wallet")
    payment_status = Column(String(20), default="pending")
    payment_reference = Column(String(100), nullable=True)

    # Status
    status = Column(
        Enum(PharmacyOrderStatusEnum),
        default=PharmacyOrderStatusEnum.PENDING,
        nullable=False,
        index=True
    )

    # Delivery Link
    delivery_id = Column(
        UUID(as_uuid=True),
        ForeignKey("deliveries.id", ondelete="SET NULL"),
        nullable=True
    )

    # Timestamps
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    prepared_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)

    # Rating
    rating = Column(Integer, nullable=True)
    review = Column(Text, nullable=True)

    # Relationships
    pharmacy = relationship("Pharmacy", back_populates="orders")
    customer = relationship("User", foreign_keys=[customer_id])
    # Specify foreign_keys to resolve ambiguity (Prescription also has fulfilled_order_id pointing to this table)
    prescription = relationship(
        "Prescription",
        foreign_keys=[prescription_id],
        back_populates="pharmacy_orders"
    )
    # Relationship from the other side (when this order fulfills a prescription)
    fulfilled_prescription = relationship(
        "Prescription",
        foreign_keys="[Prescription.fulfilled_order_id]",
        back_populates="fulfilled_order"
    )
    items = relationship(
        "PharmacyOrderItem",
        back_populates="order",
        cascade="all, delete-orphan"
    )


# ============================================
# PHARMACY ORDER ITEM
# ============================================

class PharmacyOrderItem(BaseModel):
    """Items in a pharmacy order"""
    __tablename__ = "pharmacy_order_items"

    order_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pharmacy_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    product_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pharmacy_products.id", ondelete="CASCADE"),
        nullable=False
    )

    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    total_price = Column(Numeric(10, 2), nullable=False)

    # Snapshot of product at order time
    product_name = Column(String(255), nullable=False)
    product_snapshot = Column(JSONB, nullable=True)

    # Prescription reference (if from prescription)
    from_prescription = Column(Boolean, default=False)

    # Relationships
    order = relationship("PharmacyOrder", back_populates="items")
    product = relationship("PharmacyProduct")


# ============================================
# LAB CENTER MODEL
# ============================================

class LabCenter(BaseModel):
    """Laboratory testing centers"""
    __tablename__ = "lab_centers"

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    # Basic Info
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    license_number = Column(String(100), nullable=True)
    accreditation = Column(JSONB, default=list)  # ["NAFDAC", "ISO 15189"]

    # Location
    address = Column(Text, nullable=False)
    city = Column(String(100), nullable=False, index=True)
    state = Column(String(100), nullable=False)
    location = Column(Geography(geometry_type='POINT', srid=4326, spatial_index=True), nullable=False)

    # Hours
    opening_time = Column(Time, nullable=True)
    closing_time = Column(Time, nullable=True)
    is_24_hours = Column(Boolean, default=False)

    # Services
    offers_home_sample_collection = Column(Boolean, default=False)
    home_collection_fee = Column(Numeric(10, 2), default=0.00)
    home_collection_radius_km = Column(Numeric(5, 2), nullable=True)
    avg_result_time_hours = Column(Integer, nullable=True)  # Average time to get results
    offers_online_results = Column(Boolean, default=True)

    # Media
    images = Column(JSONB, default=list)
    banner_image = Column(Text, nullable=True)

    # Stats
    total_bookings = Column(Integer, default=0)
    average_rating = Column(Numeric(3, 2), default=0.00)
    total_reviews = Column(Integer, default=0)

    # Status
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)

    # Relationships
    business = relationship("Business", back_populates="lab_center")
    tests = relationship(
        "LabTest",
        back_populates="lab_center",
        cascade="all, delete-orphan"
    )
    bookings = relationship(
        "LabBooking",
        back_populates="lab_center"
    )


# ============================================
# LAB TEST MODEL
# ============================================

class LabTest(BaseModel):
    """Available lab tests"""
    __tablename__ = "lab_tests"

    lab_center_id = Column(
        UUID(as_uuid=True),
        ForeignKey("lab_centers.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Test Details
    name = Column(String(255), nullable=False, index=True)
    code = Column(String(50), nullable=True)            # Lab code
    description = Column(Text, nullable=True)
    category = Column(
        Enum(LabTestCategoryEnum),
        nullable=False,
        index=True
    )

    # What it tests for
    conditions_detected = Column(JSONB, default=list)  # Diseases/conditions it can detect
    parameters_tested = Column(JSONB, default=list)    # What parameters are measured

    # Pricing
    price = Column(Numeric(10, 2), nullable=False)

    # Sample Info
    sample_type = Column(String(100), nullable=True)   # Blood, Urine, Stool
    fasting_required = Column(Boolean, default=False)
    fasting_hours = Column(Integer, nullable=True)
    preparation_instructions = Column(Text, nullable=True)

    # Results
    result_time_hours = Column(Integer, nullable=True) # How long results take
    includes_consultation = Column(Boolean, default=False)  # Whether result consultation is included

    # Status
    is_available = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)

    # Stats
    total_bookings = Column(Integer, default=0)
    popularity_score = Column(Numeric(5, 2), default=0.00)

    # Relationships
    lab_center = relationship("LabCenter", back_populates="tests")


# ============================================
# LAB BOOKING MODEL
# ============================================

class LabBooking(BaseModel):
    """Lab test bookings"""
    __tablename__ = "lab_bookings"

    lab_center_id = Column(
        UUID(as_uuid=True),
        ForeignKey("lab_centers.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Booking Reference
    booking_reference = Column(String(20), unique=True, nullable=False, index=True)

    # Tests Ordered
    tests = Column(JSONB, nullable=False)
    # [{"test_id": "uuid", "test_name": "CBC", "price": 3500}]

    # Appointment Details
    appointment_date = Column(Date, nullable=False, index=True)
    appointment_time = Column(Time, nullable=False)
    sample_collection_type = Column(String(20), nullable=False)  # center, home

    # Home Collection Details
    home_address = Column(Text, nullable=True)
    home_location = Column(Geography(geometry_type='POINT', srid=4326, spatial_index=True), nullable=True)

    # Patient Info
    patient_name = Column(String(200), nullable=False)
    patient_phone = Column(String(20), nullable=False)
    patient_email = Column(String(255), nullable=True)
    patient_dob = Column(Date, nullable=True)
    patient_gender = Column(String(10), nullable=True)

    # Referring Doctor
    referring_doctor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("doctors.id", ondelete="SET NULL"),
        nullable=True
    )
    doctor_notes = Column(Text, nullable=True)

    # Pricing
    subtotal = Column(Numeric(12, 2), nullable=False)
    home_collection_fee = Column(Numeric(10, 2), default=0.00)
    service_charge = Column(Numeric(10, 2), default=0.00)
    discount = Column(Numeric(10, 2), default=0.00)
    total_amount = Column(Numeric(12, 2), nullable=False)

    # Payment
    payment_method = Column(String(20), default="wallet")
    payment_status = Column(String(20), default="pending")
    payment_reference = Column(String(100), nullable=True)

    # Status
    status = Column(
        Enum(LabBookingStatusEnum),
        default=LabBookingStatusEnum.PENDING,
        nullable=False,
        index=True
    )

    # Timestamps
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    sample_collected_at = Column(DateTime(timezone=True), nullable=True)
    results_ready_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)

    # Rating
    rating = Column(Integer, nullable=True)
    review = Column(Text, nullable=True)

    # Relationships
    lab_center = relationship("LabCenter", back_populates="bookings")
    customer = relationship("User", foreign_keys=[customer_id])
    referring_doctor = relationship("Doctor", foreign_keys=[referring_doctor_id])
    result = relationship(
        "LabResult",
        back_populates="booking",
        uselist=False
    )


# ============================================
# LAB RESULT MODEL
# ============================================

class LabResult(BaseModel):
    """Lab test results"""
    __tablename__ = "lab_results"

    booking_id = Column(
        UUID(as_uuid=True),
        ForeignKey("lab_bookings.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    # Result Details
    results = Column(JSONB, nullable=False)
    # [
    #   {
    #     "test_name": "WBC",
    #     "value": "7.5",
    #     "unit": "x10^9/L",
    #     "reference_range": {"min": "4.5", "max": "11.0"},
    #     "status": "normal"  # normal, high, low, critical
    #   }
    # ]

    # Summary
    summary = Column(Text, nullable=True)
    overall_status = Column(String(20), nullable=True)  # normal, abnormal, critical

    # Lab Technician
    technician_name = Column(String(200), nullable=True)
    technician_notes = Column(Text, nullable=True)

    # Reviewed by Doctor
    reviewed_by_doctor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("doctors.id", ondelete="SET NULL"),
        nullable=True
    )
    doctor_interpretation = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    # Report
    report_url = Column(Text, nullable=True)  # PDF report URL

    # Visibility
    is_released = Column(Boolean, default=False)  # Released to patient
    released_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    booking = relationship("LabBooking", back_populates="result")
    reviewed_by = relationship("Doctor", foreign_keys=[reviewed_by_doctor_id])