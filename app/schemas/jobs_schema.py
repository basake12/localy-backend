"""
app/schemas/jobs_schema.py

BUG FIX — lga_id: Optional[UUID] → lga_name: Optional[str]

Root cause:
  The JobPosting ORM model (jobs_model.py) stores LGA as:
    lga_name = Column(String(100), nullable=True, index=True)
  There is no UUID-based LGA foreign key anywhere in the model. LGAs are
  plain string constants validated against ABUJA_LOCAL_GOVERNMENTS in
  constants.py — no separate DB table, no UUID.

  JobPostingBase declared `lga_id: Optional[UUID]` — this caused two bugs:

  1. Backend 422 on GET /api/v1/jobs?lga_id=Uyo:
     FastAPI tried to parse "Uyo" as a UUID and rejected it with
     422 Unprocessable Entity, making all job listing requests fail
     for any client passing a plain LGA name string.

  2. Flutter UUID parse crash (jobs_repository.dart line 39):
     AppException.fromDio received a DioException carrying the 422
     response body. The error body contained lga_id="Uyo" and somewhere
     in the error-handling chain Dart's Uuid.parse() was called on "Uyo",
     throwing:
       "invalid character: expected urn:uuid:, found 'U' at 1"

  Fix:
    - Replace `lga_id: Optional[UUID]` with `lga_name: Optional[str]`
      in JobPostingBase, JobPostingCreate, JobPostingUpdate, JobPostingOut.
    - The Flutter repository now sends ?lga_name=Uyo and the jobs router
      filters by the lga_name column directly — no UUID parsing attempted.
    - The jobs router query parameter must also be renamed from lga_id → lga_name.

Also retains the bug #1 fix: Pydantic v2 coercion of NULL int columns
(views_count, applications_count, positions_filled) → 0 via field_validator.
"""

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from typing import Optional, List
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from app.models.jobs_model import JobStatus, JobType, ExperienceLevel, ApplicationStatus


# ============================================
# JOB POSTING SCHEMAS
# ============================================

class JobPostingBase(BaseModel):
    title: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=50)
    job_type: JobType
    experience_level: ExperienceLevel
    location: str = Field(..., min_length=3, max_length=200)
    # FIX: was `lga_id: Optional[UUID]` — the JobPosting ORM model has no
    # UUID-based LGA FK. The actual column is `lga_name = Column(String(100))`.
    # Typed as UUID, FastAPI rejected plain LGA name strings (e.g. "Uyo")
    # with 422, breaking every job search from the Flutter client.
    lga_name: Optional[str] = None
    is_remote: bool = False
    salary_min: Optional[Decimal] = None
    salary_max: Optional[Decimal] = None
    salary_currency: str = Field("NGN", max_length=3)
    show_salary: bool = False
    requirements: List[str] = []
    responsibilities: List[str] = []
    benefits: List[str] = []
    skills_required: List[str] = []
    positions_available: int = Field(1, ge=1, le=100)
    application_deadline: Optional[str] = None


class JobPostingCreate(JobPostingBase):
    status: JobStatus = JobStatus.DRAFT

    @field_validator("salary_max")
    @classmethod
    def validate_salary_range(cls, v, info):
        if v is not None and info.data.get("salary_min") is not None:
            if v < info.data["salary_min"]:
                raise ValueError("salary_max must be >= salary_min")
        return v


class JobPostingUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=5, max_length=200)
    description: Optional[str] = Field(None, min_length=50)
    job_type: Optional[JobType] = None
    experience_level: Optional[ExperienceLevel] = None
    location: Optional[str] = None
    # FIX: was Optional[UUID] — must be Optional[str] to match the ORM column
    lga_name: Optional[str] = None
    is_remote: Optional[bool] = None
    salary_min: Optional[Decimal] = None
    salary_max: Optional[Decimal] = None
    salary_currency: Optional[str] = Field(None, max_length=3)
    show_salary: Optional[bool] = None
    requirements: Optional[List[str]] = None
    responsibilities: Optional[List[str]] = None
    benefits: Optional[List[str]] = None
    skills_required: Optional[List[str]] = None
    positions_available: Optional[int] = Field(None, ge=1, le=100)
    application_deadline: Optional[str] = None
    status: Optional[JobStatus] = None


class JobPostingOut(JobPostingBase):
    id: UUID
    business_id: UUID
    status: JobStatus
    slug: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Bug #1 fix retained: Pydantic v2 NULL int coercion                   #
    # views_count / applications_count / positions_filled may be NULL in   #
    # legacy DB rows. field_validator(mode="before") converts None → 0.    #
    # ------------------------------------------------------------------ #
    views_count: Optional[int] = 0
    applications_count: Optional[int] = 0
    positions_filled: Optional[int] = 0

    created_at: datetime
    updated_at: datetime

    # Override strict Base constraints for legacy/seed DB rows
    description: Optional[str] = None
    skills_required: Optional[List[str]] = None
    positions_available: Optional[int] = None

    # Denormalised business fields
    business_name: Optional[str] = None
    business_logo: Optional[str] = None

    @field_validator("views_count", "applications_count", "positions_filled", mode="before")
    @classmethod
    def coerce_none_to_zero(cls, v):
        return v if v is not None else 0

    @model_validator(mode="before")
    @classmethod
    def extract_business_fields(cls, data):
        """Pull business_name / business_logo from the ORM relationship if present."""
        if hasattr(data, "business") and data.business is not None:
            object.__setattr__(
                data,
                "business_name",
                getattr(data.business, "business_name", None),
            )
            object.__setattr__(
                data,
                "business_logo",
                getattr(data.business, "logo_url", None),
            )
        return data

    model_config = {"from_attributes": True}


class JobPostingListOut(BaseModel):
    jobs: List[JobPostingOut]
    total: int
    page: int
    page_size: int


# ============================================
# JOB APPLICATION SCHEMAS
# ============================================

class JobApplicationBase(BaseModel):
    cover_letter: Optional[str] = Field(None, max_length=5000)
    resume_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    applicant_name: str = Field(..., min_length=2, max_length=200)
    applicant_email: EmailStr
    applicant_phone: Optional[str] = Field(None, max_length=20)
    years_of_experience: Optional[int] = Field(None, ge=0, le=50)
    expected_salary: Optional[Decimal] = None
    availability_date: Optional[str] = None


class JobApplicationCreate(JobApplicationBase):
    pass


class JobApplicationUpdate(BaseModel):
    cover_letter: Optional[str] = Field(None, max_length=5000)
    resume_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    expected_salary: Optional[Decimal] = None
    availability_date: Optional[str] = None


class JobApplicationStatusUpdate(BaseModel):
    status: ApplicationStatus
    notes: Optional[str] = None


class JobApplicationOut(JobApplicationBase):
    id: UUID
    job_id: UUID
    applicant_id: UUID
    status: ApplicationStatus
    viewed_by_employer: bool
    viewed_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    job_title: Optional[str] = None
    business_name: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def extract_job_fields(cls, data):
        if hasattr(data, "job") and data.job is not None:
            object.__setattr__(data, "job_title", getattr(data.job, "title", None))
            if data.job.business is not None:
                object.__setattr__(
                    data,
                    "business_name",
                    getattr(data.job.business, "business_name", None),
                )
        return data

    model_config = {"from_attributes": True}


class JobApplicationListOut(BaseModel):
    applications: List[JobApplicationOut]
    total: int
    page: int
    page_size: int


# ============================================
# STATISTICS
# ============================================

class JobStatsOut(BaseModel):
    total_jobs: int
    open_jobs: int
    draft_jobs: int
    closed_jobs: int
    total_applications: int
    pending_applications: int
    total_views: int