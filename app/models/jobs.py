from sqlalchemy import Column, String, Text, Integer, Numeric, Boolean, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum

from app.models.base import BaseModel


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
    Job vacancy posted by a business.
    Can be for any type of position across all business categories.
    """

    __tablename__ = "job_postings"

    # Ownership
    business_id = Column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False,
                         index=True)

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
    salary_min = Column(Numeric(15, 2), nullable=True)  # NGN
    salary_max = Column(Numeric(15, 2), nullable=True)  # NGN
    salary_currency = Column(String(3), default="NGN")
    show_salary = Column(Boolean, default=False)  # Whether to display salary publicly

    # Requirements & benefits
    requirements = Column(JSONB, default=list)  # ["Bachelor's degree", "3+ years exp", ...]
    responsibilities = Column(JSONB, default=list)  # ["Manage team", "Lead projects", ...]
    benefits = Column(JSONB, default=list)  # ["Health insurance", "Pension", ...]
    skills_required = Column(JSONB, default=list)  # ["Python", "React", "SQL", ...]

    # Metadata
    positions_available = Column(Integer, default=1)
    application_deadline = Column(String(50), nullable=True)  # ISO date string or "Ongoing"

    # Status
    status = Column(SQLEnum(JobStatus), nullable=False, default=JobStatus.DRAFT, index=True)
    views_count = Column(Integer, default=0)
    applications_count = Column(Integer, default=0)

    # SEO
    slug = Column(String(300), unique=True, index=True)

    # Relationships
    business = relationship("Business", back_populates="job_postings")
    applications = relationship("JobApplication", back_populates="job", cascade="all, delete-orphan")


class JobApplication(BaseModel):
    """
    User's application to a job posting.
    """

    __tablename__ = "job_applications"

    # References
    job_id = Column(UUID(as_uuid=True), ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False, index=True)
    applicant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Application content
    cover_letter = Column(Text, nullable=True)
    resume_url = Column(String(500), nullable=True)  # S3/MinIO URL
    portfolio_url = Column(String(500), nullable=True)

    # Contact info (pre-filled from user, editable)
    applicant_name = Column(String(200), nullable=False)
    applicant_email = Column(String(255), nullable=False)
    applicant_phone = Column(String(20), nullable=True)

    # Additional fields
    years_of_experience = Column(Integer, nullable=True)
    expected_salary = Column(Numeric(15, 2), nullable=True)  # NGN
    availability_date = Column(String(50), nullable=True)  # e.g., "Immediate", "2 weeks notice"

    # Status tracking
    status = Column(SQLEnum(ApplicationStatus), nullable=False, default=ApplicationStatus.PENDING, index=True)
    notes = Column(Text, nullable=True)  # Internal notes from employer

    # Metadata
    viewed_by_employer = Column(Boolean, default=False)
    viewed_at = Column(String(50), nullable=True)  # ISO timestamp

    # Relationships
    job = relationship("JobPosting", back_populates="applications")
    applicant = relationship("User", back_populates="job_applications")