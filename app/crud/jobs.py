from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func, desc
from typing import Optional, List
from uuid import UUID
from slugify import slugify
import secrets

from app.crud.base import CRUDBase
from app.models.jobs import JobPosting, JobApplication, JobStatus, ApplicationStatus
from app.schemas.jobs import (
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
            obj_in: JobPostingCreate
    ) -> JobPosting:
        """Create a new job posting."""
        # Generate unique slug
        base_slug = slugify(obj_in.title)
        slug = base_slug
        counter = 1
        while db.query(JobPosting).filter(JobPosting.slug == slug).first():
            slug = f"{base_slug}-{counter}"
            counter += 1

        # Create job
        job = JobPosting(
            business_id=business_id,
            slug=slug,
            **obj_in.model_dump()
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    def get_by_slug(self, db: Session, *, slug: str) -> Optional[JobPosting]:
        """Get job by slug."""
        return db.query(JobPosting).filter(JobPosting.slug == slug).first()

    def get_business_jobs(
            self,
            db: Session,
            *,
            business_id: UUID,
            status: Optional[JobStatus] = None,
            skip: int = 0,
            limit: int = 20
    ) -> tuple[List[JobPosting], int]:
        """Get all jobs for a business."""
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
            is_remote: Optional[bool] = None,
            skills: Optional[List[str]] = None,
            skip: int = 0,
            limit: int = 20
    ) -> tuple[List[JobPosting], int]:
        """Search and filter jobs."""
        query = db.query(JobPosting).filter(JobPosting.status == JobStatus.OPEN)

        # Text search (title, description, location)
        if query_text:
            search_filter = or_(
                JobPosting.title.ilike(f"%{query_text}%"),
                JobPosting.description.ilike(f"%{query_text}%"),
                JobPosting.location.ilike(f"%{query_text}%")
            )
            query = query.filter(search_filter)

        # Filters
        if job_type:
            query = query.filter(JobPosting.job_type == job_type)

        if experience_level:
            query = query.filter(JobPosting.experience_level == experience_level)

        if location:
            query = query.filter(JobPosting.location.ilike(f"%{location}%"))

        if is_remote is not None:
            query = query.filter(JobPosting.is_remote == is_remote)

        # Skills filter (check if any skill in skills_required matches)
        if skills:
            for skill in skills:
                query = query.filter(JobPosting.skills_required.contains([skill]))

        total = query.count()
        jobs = query.order_by(desc(JobPosting.created_at)).offset(skip).limit(limit).all()

        return jobs, total

    def increment_views(self, db: Session, *, job_id: UUID) -> None:
        """Increment job views count."""
        db.query(JobPosting).filter(JobPosting.id == job_id).update(
            {"views_count": JobPosting.views_count + 1}
        )
        db.commit()

    def update_applications_count(self, db: Session, *, job_id: UUID) -> None:
        """Update applications count from actual applications."""
        count = db.query(JobApplication).filter(JobApplication.job_id == job_id).count()
        db.query(JobPosting).filter(JobPosting.id == job_id).update(
            {"applications_count": count}
        )
        db.commit()


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
            obj_in: JobApplicationCreate
    ) -> JobApplication:
        """Submit job application."""
        application = JobApplication(
            job_id=job_id,
            applicant_id=applicant_id,
            **obj_in.model_dump()
        )
        db.add(application)
        db.commit()
        db.refresh(application)

        # Update job applications count
        job_crud.update_applications_count(db, job_id=job_id)

        return application

    def get_user_application(
            self,
            db: Session,
            *,
            job_id: UUID,
            applicant_id: UUID
    ) -> Optional[JobApplication]:
        """Check if user already applied to this job."""
        return db.query(JobApplication).filter(
            and_(
                JobApplication.job_id == job_id,
                JobApplication.applicant_id == applicant_id
            )
        ).first()

    def get_job_applications(
            self,
            db: Session,
            *,
            job_id: UUID,
            status: Optional[ApplicationStatus] = None,
            skip: int = 0,
            limit: int = 20
    ) -> tuple[List[JobApplication], int]:
        """Get all applications for a job."""
        query = db.query(JobApplication).filter(JobApplication.job_id == job_id)

        if status:
            query = query.filter(JobApplication.status == status)

        total = query.count()
        applications = query.order_by(desc(JobApplication.created_at)).offset(skip).limit(limit).all()

        return applications, total

    def get_user_applications(
            self,
            db: Session,
            *,
            applicant_id: UUID,
            status: Optional[ApplicationStatus] = None,
            skip: int = 0,
            limit: int = 20
    ) -> tuple[List[JobApplication], int]:
        """Get all applications by a user."""
        query = db.query(JobApplication).filter(JobApplication.applicant_id == applicant_id)

        if status:
            query = query.filter(JobApplication.status == status)

        total = query.count()
        applications = (
            query
            .options(joinedload(JobApplication.job))
            .order_by(desc(JobApplication.created_at))
            .offset(skip)
            .limit(limit)
            .all()
        )

        return applications, total

    def mark_as_viewed(self, db: Session, *, application_id: UUID) -> JobApplication:
        """Mark application as viewed by employer."""
        from datetime import datetime

        application = self.get(db, id=application_id)
        if application and not application.viewed_by_employer:
            application.viewed_by_employer = True
            application.viewed_at = datetime.utcnow().isoformat()
            db.commit()
            db.refresh(application)

        return application

    def update_status(
            self,
            db: Session,
            *,
            application_id: UUID,
            status: ApplicationStatus,
            notes: Optional[str] = None
    ) -> JobApplication:
        """Update application status."""
        application = self.get(db, id=application_id)
        if application:
            application.status = status
            if notes is not None:
                application.notes = notes
            db.commit()
            db.refresh(application)

        return application


# Singleton instances
job_crud = CRUDJobPosting(JobPosting)
application_crud = CRUDJobApplication(JobApplication)