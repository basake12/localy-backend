from datetime import datetime, timezone
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func, desc
from typing import Optional, List
from uuid import UUID
from slugify import slugify

from app.crud.base_crud import CRUDBase
from app.models.jobs_model import (
    JobPosting,
    JobApplication,
    JobStatus,
    ApplicationStatus,
)
from app.schemas.jobs_schema import (
    JobPostingCreate,
    JobPostingUpdate,
    JobApplicationCreate,
    JobApplicationUpdate,
)


# ============================================
# JOB POSTING CRUD
# ============================================

class CRUDJobPosting(CRUDBase[JobPosting, JobPostingCreate, JobPostingUpdate]):

    def create_job(
            self,
            db: Session,
            *,
            business_id: UUID,
            obj_in: JobPostingCreate,
    ) -> JobPosting:
        """Create a new job posting with a unique slug."""
        base_slug = slugify(obj_in.title)
        slug = base_slug
        counter = 1
        while db.query(JobPosting).filter(JobPosting.slug == slug).first():
            slug = f"{base_slug}-{counter}"
            counter += 1

        job = JobPosting(
            business_id=business_id,
            slug=slug,
            **obj_in.model_dump(),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    def get_by_slug(self, db: Session, *, slug: str) -> Optional[JobPosting]:
        return db.query(JobPosting).filter(JobPosting.slug == slug).first()

    def get_business_jobs(
            self,
            db: Session,
            *,
            business_id: UUID,
            status: Optional[JobStatus] = None,
            skip: int = 0,
            limit: int = 20,
    ) -> tuple[List[JobPosting], int]:
        query = db.query(JobPosting).filter(JobPosting.business_id == business_id)
        if status:
            query = query.filter(JobPosting.status == status)
        total = query.count()
        jobs = query.order_by(desc(JobPosting.created_at)).offset(skip).limit(limit).all()
        return jobs, total

    def search_jobs(
            self,
            db: Session,
            *,
            query_text: Optional[str] = None,
            job_type: Optional[str] = None,
            experience_level: Optional[str] = None,
            location: Optional[str] = None,
            # FIX: was lga_id: Optional[UUID] — the JobPosting model stores LGA as
            # lga_name = Column(String(100)), not a UUID FK. Renamed to match the
            # actual column; router and schema already updated to pass lga_name.
            lga_name: Optional[str] = None,
            is_remote: Optional[bool] = None,
            skills: Optional[List[str]] = None,
            skip: int = 0,
            limit: int = 20,
    ) -> tuple[List[JobPosting], int]:
        """Search and filter open jobs, with LGA-scoped filtering."""
        query = db.query(JobPosting).filter(JobPosting.status == JobStatus.OPEN)

        # FIX: was JobPosting.lga_id — correct column is lga_name (String).
        if lga_name is not None:
            query = query.filter(JobPosting.lga_name == lga_name)

        if query_text:
            search_filter = or_(
                JobPosting.title.ilike(f"%{query_text}%"),
                JobPosting.description.ilike(f"%{query_text}%"),
                JobPosting.location.ilike(f"%{query_text}%"),
            )
            query = query.filter(search_filter)

        if job_type:
            query = query.filter(JobPosting.job_type == job_type)

        if experience_level:
            query = query.filter(JobPosting.experience_level == experience_level)

        if location:
            query = query.filter(JobPosting.location.ilike(f"%{location}%"))

        if is_remote is not None:
            query = query.filter(JobPosting.is_remote == is_remote)

        if skills:
            for skill in skills:
                query = query.filter(JobPosting.skills_required.contains([skill]))

        total = query.count()
        jobs = query.order_by(desc(JobPosting.created_at)).offset(skip).limit(limit).all()
        return jobs, total

    def increment_views(self, db: Session, *, job_id: UUID) -> None:
        from sqlalchemy import func as sa_func
        db.query(JobPosting).filter(JobPosting.id == job_id).update(
            {"views_count": sa_func.coalesce(JobPosting.views_count, 0) + 1}
        )
        db.commit()

    def sync_applications_count(self, db: Session, *, job_id: UUID) -> None:
        """Re-compute applications_count from the actual applications table."""
        count = db.query(JobApplication).filter(JobApplication.job_id == job_id).count()
        db.query(JobPosting).filter(JobPosting.id == job_id).update(
            {"applications_count": count}
        )
        db.commit()

    def get_stats(self, db: Session, *, business_id: UUID) -> dict:
        jobs = db.query(JobPosting).filter(JobPosting.business_id == business_id).all()
        job_ids = [j.id for j in jobs]

        total_apps = (
            db.query(func.count(JobApplication.id))
            .filter(JobApplication.job_id.in_(job_ids))
            .scalar()
            or 0
        )
        pending_apps = (
            db.query(func.count(JobApplication.id))
            .filter(
                JobApplication.job_id.in_(job_ids),
                JobApplication.status == ApplicationStatus.PENDING,
            )
            .scalar()
            or 0
        )

        return {
            "total_jobs": len(jobs),
            "open_jobs": sum(1 for j in jobs if j.status == JobStatus.OPEN),
            "draft_jobs": sum(1 for j in jobs if j.status == JobStatus.DRAFT),
            "closed_jobs": sum(1 for j in jobs if j.status == JobStatus.CLOSED),
            "total_applications": total_apps,
            "pending_applications": pending_apps,
            "total_views": sum(j.views_count or 0 for j in jobs),
        }


# ============================================
# JOB APPLICATION CRUD
# ============================================

class CRUDJobApplication(CRUDBase[JobApplication, JobApplicationCreate, JobApplicationUpdate]):

    def create_application(
            self,
            db: Session,
            *,
            job_id: UUID,
            applicant_id: UUID,
            obj_in: JobApplicationCreate,
    ) -> JobApplication:
        """Submit a job application."""
        application = JobApplication(
            job_id=job_id,
            applicant_id=applicant_id,
            **obj_in.model_dump(),
        )
        db.add(application)
        db.commit()
        db.refresh(application)

        # FIX: Call job_crud directly via its query — no forward-reference issue.
        # (job_crud is defined below but this method runs at call-time, not import-time.)
        job_crud.sync_applications_count(db, job_id=job_id)

        return application

    def get_user_application(
            self,
            db: Session,
            *,
            job_id: UUID,
            applicant_id: UUID,
    ) -> Optional[JobApplication]:
        """Check if user already applied to this job."""
        return db.query(JobApplication).filter(
            and_(
                JobApplication.job_id == job_id,
                JobApplication.applicant_id == applicant_id,
            )
        ).first()

    def get_job_applications(
            self,
            db: Session,
            *,
            job_id: UUID,
            status: Optional[ApplicationStatus] = None,
            skip: int = 0,
            limit: int = 20,
    ) -> tuple[List[JobApplication], int]:
        query = (
            db.query(JobApplication)
            .options(joinedload(JobApplication.applicant))
            .filter(JobApplication.job_id == job_id)
        )
        if status:
            query = query.filter(JobApplication.status == status)
        total = query.count()
        applications = (
            query.order_by(desc(JobApplication.created_at))
            .offset(skip)
            .limit(limit)
            .all()
        )
        return applications, total

    def get_user_applications(
            self,
            db: Session,
            *,
            applicant_id: UUID,
            status: Optional[ApplicationStatus] = None,
            skip: int = 0,
            limit: int = 20,
    ) -> tuple[List[JobApplication], int]:
        query = (
            db.query(JobApplication)
            .options(
                joinedload(JobApplication.job).joinedload(JobPosting.business)
            )
            .filter(JobApplication.applicant_id == applicant_id)
        )
        if status:
            query = query.filter(JobApplication.status == status)
        total = query.count()
        applications = (
            query.order_by(desc(JobApplication.created_at))
            .offset(skip)
            .limit(limit)
            .all()
        )
        return applications, total

    def mark_as_viewed(self, db: Session, *, application_id: UUID) -> Optional[JobApplication]:
        """Mark application as viewed by employer; record timestamp."""
        application = self.get(db, id=application_id)
        if application and not application.viewed_by_employer:
            # FIX: Store a proper datetime object, not an isoformat string.
            application.viewed_by_employer = True
            application.viewed_at = datetime.now(tz=timezone.utc)
            db.commit()
            db.refresh(application)
        return application

    def update_status(
            self,
            db: Session,
            *,
            application_id: UUID,
            status: ApplicationStatus,
            notes: Optional[str] = None,
    ) -> Optional[JobApplication]:
        application = self.get(db, id=application_id)
        if application:
            application.status = status
            if notes is not None:
                application.notes = notes
            db.commit()
            db.refresh(application)
        return application


# Singleton instances — forward-reference in create_application is safe
# because it resolves at call-time, not at class-definition time.
job_crud = CRUDJobPosting(JobPosting)
application_crud = CRUDJobApplication(JobApplication)