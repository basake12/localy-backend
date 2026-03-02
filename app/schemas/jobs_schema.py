from pydantic import BaseModel, EmailStr, Field, field_validator
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
    is_remote: bool = False
    salary_min: Optional[Decimal] = None
    salary_max: Optional[Decimal] = None
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
    is_remote: Optional[bool] = None
    salary_min: Optional[Decimal] = None
    salary_max: Optional[Decimal] = None
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
    slug: str
    views_count: int
    applications_count: int
    created_at: datetime
    updated_at: datetime

    # Optional business details (if eager loaded)
    business_name: Optional[str] = None
    business_logo: Optional[str] = None

    class Config:
        from_attributes = True


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
    cover_letter: Optional[str] = None
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
    viewed_at: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    # Optional job details (if eager loaded)
    job_title: Optional[str] = None
    business_name: Optional[str] = None

    class Config:
        from_attributes = True


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