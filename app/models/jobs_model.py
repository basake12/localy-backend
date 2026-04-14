from sqlalchemy import (
    Column, String, Text, Integer, Numeric, Boolean,
    ForeignKey, Enum as SQLEnum, DateTime, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum

from app.models.base_model import BaseModel


# ============================================
# ENUMS
# ============================================

class JobStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class JobType(str, enum.Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"


class ExperienceLevel(str, enum.Enum):
    ENTRY = "entry"
    INTERMEDIATE = "intermediate"
    SENIOR = "senior"
    EXECUTIVE = "executive"


class ApplicationStatus(str, enum.Enum):
    PENDING = "pending"
    REVIEWED = "reviewed"
    SHORTLISTED = "shortlisted"
    INTERVIEW = "interview"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


# ============================================
# MODELS
# ============================================

class JobPosting(BaseModel):
    """
    Job vacancy posted by a verified business.
    LGA-scoped by default per platform location rules.
    LGA is stored as a plain string validated against ABUJA_LOCAL_GOVERNMENTS
    in constants.py — no separate DB table needed.
    """

    __tablename__ = "job_postings"

    # Ownership
    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Location scoping (LGA-aware per blueprint)
    # Stored as a string matched against ABUJA_LOCAL_GOVERNMENTS in constants.py.
    # No FK needed — LGAs are managed as constants, not a DB table.
    lga_name = Column(String(100), nullable=True, index=True)

    # Basic info
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)

    # Job details
    job_type = Column(SQLEnum(JobType), nullable=False, default=JobType.FULL_TIME)
    experience_level = Column(SQLEnum(ExperienceLevel), nullable=False, default=ExperienceLevel.ENTRY)

    # Location
    location = Column(String(200), nullable=False)  # e.g., "Abuja, FCT"
    is_remote = Column(Boolean, default=False)

    # Compensation
    salary_min = Column(Numeric(15, 2), nullable=True)
    salary_max = Column(Numeric(15, 2), nullable=True)
    salary_currency = Column(String(3), default="NGN")
    show_salary = Column(Boolean, default=False)

    # Requirements & benefits
    requirements = Column(JSONB, default=list)
    responsibilities = Column(JSONB, default=list)
    benefits = Column(JSONB, default=list)
    skills_required = Column(JSONB, default=list)

    # Positions
    positions_available = Column(Integer, default=1)
    positions_filled = Column(Integer, default=0)

    application_deadline = Column(String(50), nullable=True)  # ISO date or "Ongoing"

    # Status & metrics
    status = Column(SQLEnum(JobStatus), nullable=False, default=JobStatus.DRAFT, index=True)
    views_count = Column(Integer, default=0)
    applications_count = Column(Integer, default=0)

    # SEO
    slug = Column(String(300), unique=True, index=True)

    # Relationships
    business = relationship("Business", back_populates="job_postings")
    applications = relationship(
        "JobApplication", back_populates="job", cascade="all, delete-orphan"
    )


class JobApplication(BaseModel):
    """
    Candidate's application to a job posting.
    One application per (job, applicant) pair enforced at DB level.
    """

    __tablename__ = "job_applications"

    # UniqueConstraint prevents duplicate applications at DB level.
    __table_args__ = (
        UniqueConstraint("job_id", "applicant_id", name="uq_job_application"),
    )

    # References
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("job_postings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    applicant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Application content
    cover_letter = Column(Text, nullable=True)
    resume_url = Column(String(500), nullable=True)
    portfolio_url = Column(String(500), nullable=True)

    # Contact info (pre-filled from user profile, editable)
    applicant_name = Column(String(200), nullable=False)
    applicant_email = Column(String(255), nullable=False)
    applicant_phone = Column(String(20), nullable=True)

    # Candidate details
    years_of_experience = Column(Integer, nullable=True)
    expected_salary = Column(Numeric(15, 2), nullable=True)
    availability_date = Column(String(50), nullable=True)  # "Immediate", "2 weeks notice", etc.

    # Status tracking
    status = Column(
        SQLEnum(ApplicationStatus),
        nullable=False,
        default=ApplicationStatus.PENDING,
        index=True,
    )
    notes = Column(Text, nullable=True)  # Internal employer notes

    viewed_by_employer = Column(Boolean, default=False)
    viewed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    job = relationship("JobPosting", back_populates="applications")
    applicant = relationship("User", back_populates="job_applications")