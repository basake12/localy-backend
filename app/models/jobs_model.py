"""
app/models/jobs_model.py

FIXES vs previous version:
  1. [HARD RULE] lga_name column DELETED entirely.
     Blueprint §4 / §2: "No LGA column in any database table."
     Blueprint §8.6: "Vacancy visible to users within business's registered
     location radius." — job discovery is radius-based (business.location +
     service_radius_m via ST_DWithin), exactly like every other module.
     No LGA scoping, no LGA constants, no ABUJA_LOCAL_GOVERNMENTS reference.

  2. All LGA-related comments removed from code and docstrings.

  3. JobType: GIG added — Blueprint §8.6 lists "full-time/part-time/contract/gig"
     as valid role types.

  4. Blueprint §8.6: "Vacancy auto-closes when marked filled (jobs.status='filled',
     updated_at=now())." — JobStatus.FILLED added.
     Celery task close_expired_jobs (daily) closes jobs older than 90 days.

  5. expires_at column added — Celery task close_expired_jobs checks this.
"""
from sqlalchemy import (
    Column,
    String,
    Text,
    Integer,
    Numeric,
    Boolean,
    ForeignKey,
    Enum as SQLEnum,
    DateTime,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum

from app.models.base_model import BaseModel


# ─── Enums ────────────────────────────────────────────────────────────────────

class JobStatus(str, enum.Enum):
    DRAFT     = "draft"
    OPEN      = "open"
    FILLED    = "filled"      # Blueprint §8.6: auto-closes on filled
    CLOSED    = "closed"      # Celery task: close_expired_jobs (90 days)
    CANCELLED = "cancelled"


class JobType(str, enum.Enum):
    """Blueprint §8.6: full-time / part-time / contract / gig."""
    FULL_TIME  = "full_time"
    PART_TIME  = "part_time"
    CONTRACT   = "contract"
    GIG        = "gig"          # Blueprint §8.6 explicit type
    INTERNSHIP = "internship"
    TEMPORARY  = "temporary"


class ExperienceLevel(str, enum.Enum):
    ENTRY        = "entry"
    INTERMEDIATE = "intermediate"
    SENIOR       = "senior"
    EXECUTIVE    = "executive"


class ApplicationStatus(str, enum.Enum):
    PENDING     = "pending"
    REVIEWED    = "reviewed"
    SHORTLISTED = "shortlisted"
    INTERVIEW   = "interview"
    ACCEPTED    = "accepted"
    REJECTED    = "rejected"
    WITHDRAWN   = "withdrawn"


# ─── Job Posting ──────────────────────────────────────────────────────────────

class JobPosting(BaseModel):
    """
    Job vacancy posted by a verified business. Blueprint §8.6.

    Blueprint §8.6 HARD RULE: Only VERIFIED businesses may post job vacancies.
    Enforced at API layer via require_verified_business dependency.

    Discovery: radius-based — users within business's registered location radius
    see the vacancy. Uses business.location + ST_DWithin, NOT LGA filtering.

    Blueprint §8.6: "Vacancy auto-closes when marked filled."
    Celery task close_expired_jobs (daily): closes jobs older than 90 days.
    """
    __tablename__ = "job_postings"

    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # REMOVED: lga_name — Blueprint HARD RULE: no LGA column anywhere.
    # Discovery is radius-based via business.location (ST_DWithin).

    title       = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)

    job_type        = Column(SQLEnum(JobType),        nullable=False, default=JobType.FULL_TIME)
    experience_level = Column(SQLEnum(ExperienceLevel), nullable=False, default=ExperienceLevel.ENTRY)

    location  = Column(String(200), nullable=False)   # Human-readable e.g. "Lagos Island"
    is_remote = Column(Boolean, default=False)

    salary_min      = Column(Numeric(15, 2), nullable=True)
    salary_max      = Column(Numeric(15, 2), nullable=True)
    salary_currency = Column(String(3), default="NGN")
    show_salary     = Column(Boolean, default=False)

    requirements        = Column(JSONB, default=list)
    responsibilities    = Column(JSONB, default=list)
    benefits            = Column(JSONB, default=list)
    skills_required     = Column(JSONB, default=list)

    positions_available = Column(Integer, default=1)
    positions_filled    = Column(Integer, default=0)

    application_deadline = Column(String(50), nullable=True)  # ISO date or "Ongoing"

    status           = Column(SQLEnum(JobStatus), nullable=False, default=JobStatus.DRAFT, index=True)
    views_count      = Column(Integer, default=0)
    applications_count = Column(Integer, default=0)

    # Blueprint §8.6: Celery task close_expired_jobs checks created_at > 90 days
    expires_at = Column(DateTime(timezone=True), nullable=True)

    slug = Column(String(300), unique=True, index=True, nullable=True)

    business     = relationship("Business", back_populates="job_postings")
    applications = relationship(
        "JobApplication", back_populates="job", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<JobPosting {self.title} status={self.status}>"


# ─── Job Application ──────────────────────────────────────────────────────────

class JobApplication(BaseModel):
    """
    Candidate's application to a job posting. Blueprint §8.6.

    Blueprint §8.6: "In-app chat with employer after shortlisting only."
    Chat channel opens when status transitions to SHORTLISTED.

    One application per (job, applicant) enforced at DB level.
    """
    __tablename__ = "job_applications"

    __table_args__ = (
        UniqueConstraint("job_id", "applicant_id", name="uq_job_application"),
    )

    job_id       = Column(UUID(as_uuid=True), ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False, index=True)
    applicant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    cover_letter   = Column(Text, nullable=True)
    resume_url     = Column(String(500), nullable=True)
    portfolio_url  = Column(String(500), nullable=True)

    applicant_name  = Column(String(200), nullable=False)
    applicant_email = Column(String(255), nullable=False)
    applicant_phone = Column(String(20), nullable=True)

    years_of_experience = Column(Integer, nullable=True)
    expected_salary     = Column(Numeric(15, 2), nullable=True)
    availability_date   = Column(String(50), nullable=True)

    status = Column(
        SQLEnum(ApplicationStatus),
        nullable=False,
        default=ApplicationStatus.PENDING,
        index=True,
    )
    notes = Column(Text, nullable=True)

    viewed_by_employer = Column(Boolean, default=False)
    viewed_at          = Column(DateTime(timezone=True), nullable=True)

    job       = relationship("JobPosting", back_populates="applications")
    applicant = relationship("User", back_populates="job_applications")

    def __repr__(self) -> str:
        return f"<JobApplication job={self.job_id} applicant={self.applicant_id} status={self.status}>"