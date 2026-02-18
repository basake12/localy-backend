from sqlalchemy.orm import Session
from typing import Optional, List, Tuple
from uuid import UUID

from app.models.user import User
from app.models.jobs import JobPosting, JobApplication, JobStatus, ApplicationStatus
from app.schemas.jobs import (
    JobPostingCreate,
    JobPostingUpdate,
    JobApplicationCreate,
    JobApplicationUpdate,
    JobApplicationStatusUpdate,
    JobStatsOut,
)
from app.crud.jobs import job_crud, application_crud
from app.crud.business import business_crud
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    AlreadyExistsException,
    ValidationException,
)


# ============================================
# JOB POSTING SERVICE
# ============================================

class JobPostingService:
    """Business logic for job postings."""

    def create_job(
            self,
            db: Session,
            *,
            business_id: UUID,
            user: User,
            payload: JobPostingCreate
    ) -> JobPosting:
        """Create a new job posting."""
        # Verify business ownership
        business = business_crud.get(db, id=business_id)
        if not business:
            raise NotFoundException("Business")

        if business.user_id != user.id:
            raise PermissionDeniedException("You don't own this business")

        # Create job
        job = job_crud.create_job(db, business_id=business_id, obj_in=payload)
        return job

    def get_job(self, db: Session, *, job_id: UUID, increment_views: bool = False) -> JobPosting:
        """Get job by ID."""
        job = job_crud.get(db, id=job_id)
        if not job:
            raise NotFoundException("Job posting")

        # Increment views if requested
        if increment_views and job.status == JobStatus.OPEN:
            job_crud.increment_views(db, job_id=job_id)

        return job

    def get_job_by_slug(
            self,
            db: Session,
            *,
            slug: str,
            increment_views: bool = False
    ) -> JobPosting:
        """Get job by slug."""
        job = job_crud.get_by_slug(db, slug=slug)
        if not job:
            raise NotFoundException("Job posting")

        # Increment views if requested
        if increment_views and job.status == JobStatus.OPEN:
            job_crud.increment_views(db, job_id=job.id)

        return job

    def update_job(
            self,
            db: Session,
            *,
            job_id: UUID,
            user: User,
            payload: JobPostingUpdate
    ) -> JobPosting:
        """Update job posting."""
        job = self.get_job(db, job_id=job_id)

        # Verify ownership
        if job.business.user_id != user.id:
            raise PermissionDeniedException("You don't own this job posting")

        # Update
        updated_job = job_crud.update(db, db_obj=job, obj_in=payload)
        return updated_job

    def delete_job(self, db: Session, *, job_id: UUID, user: User) -> None:
        """Delete job posting."""
        job = self.get_job(db, job_id=job_id)

        # Verify ownership
        if job.business.user_id != user.id:
            raise PermissionDeniedException("You don't own this job posting")

        # Delete
        job_crud.remove(db, id=job_id)

    def list_business_jobs(
            self,
            db: Session,
            *,
            business_id: UUID,
            user: User,
            status: Optional[JobStatus] = None,
            skip: int = 0,
            limit: int = 20
    ) -> Tuple[List[JobPosting], int]:
        """List all jobs for a business."""
        # Verify business ownership
        business = business_crud.get(db, id=business_id)
        if not business:
            raise NotFoundException("Business")

        if business.user_id != user.id:
            raise PermissionDeniedException("You don't own this business")

        # Get jobs
        jobs, total = job_crud.get_business_jobs(
            db,
            business_id=business_id,
            status=status,
            skip=skip,
            limit=limit
        )

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
    ) -> Tuple[List[JobPosting], int]:
        """Search and filter jobs (public endpoint)."""
        jobs, total = job_crud.search_jobs(
            db,
            query_text=query_text,
            job_type=job_type,
            experience_level=experience_level,
            location=location,
            is_remote=is_remote,
            skills=skills,
            skip=skip,
            limit=limit
        )

        return jobs, total

    def get_business_stats(self, db: Session, *, business_id: UUID, user: User) -> JobStatsOut:
        """Get job statistics for a business."""
        # Verify ownership
        business = business_crud.get(db, id=business_id)
        if not business:
            raise NotFoundException("Business")

        if business.user_id != user.id:
            raise PermissionDeniedException("You don't own this business")

        # Get all jobs for business
        all_jobs, _ = job_crud.get_business_jobs(db, business_id=business_id, skip=0, limit=10000)

        # Calculate stats
        total_jobs = len(all_jobs)
        open_jobs = sum(1 for j in all_jobs if j.status == JobStatus.OPEN)
        draft_jobs = sum(1 for j in all_jobs if j.status == JobStatus.DRAFT)
        closed_jobs = sum(1 for j in all_jobs if j.status == JobStatus.CLOSED)
        total_applications = sum(j.applications_count for j in all_jobs)
        total_views = sum(j.views_count for j in all_jobs)

        # Count pending applications
        from sqlalchemy import and_
        pending_applications = db.query(JobApplication).join(JobPosting).filter(
            and_(
                JobPosting.business_id == business_id,
                JobApplication.status == ApplicationStatus.PENDING
            )
        ).count()

        return JobStatsOut(
            total_jobs=total_jobs,
            open_jobs=open_jobs,
            draft_jobs=draft_jobs,
            closed_jobs=closed_jobs,
            total_applications=total_applications,
            pending_applications=pending_applications,
            total_views=total_views
        )


# ============================================
# JOB APPLICATION SERVICE
# ============================================

class JobApplicationService:
    """Business logic for job applications."""

    def apply_to_job(
            self,
            db: Session,
            *,
            job_id: UUID,
            user: User,
            payload: JobApplicationCreate
    ) -> JobApplication:
        """Submit application to a job."""
        # Check job exists and is open
        job = job_crud.get(db, id=job_id)
        if not job:
            raise NotFoundException("Job posting")

        if job.status != JobStatus.OPEN:
            raise ValidationException("This job is not accepting applications")

        # Check if user already applied
        existing = application_crud.get_user_application(
            db,
            job_id=job_id,
            applicant_id=user.id
        )
        if existing:
            raise AlreadyExistsException("You have already applied to this job")

        # Create application
        application = application_crud.create_application(
            db,
            job_id=job_id,
            applicant_id=user.id,
            obj_in=payload
        )

        return application

    def get_application(
            self,
            db: Session,
            *,
            application_id: UUID,
            user: User,
            mark_viewed: bool = False
    ) -> JobApplication:
        """Get application by ID."""
        application = application_crud.get(db, id=application_id)
        if not application:
            raise NotFoundException("Application")

        # Check permission (applicant or business owner)
        is_applicant = application.applicant_id == user.id
        is_employer = application.job.business.user_id == user.id

        if not (is_applicant or is_employer):
            raise PermissionDeniedException("You don't have access to this application")

        # Mark as viewed if employer is viewing
        if is_employer and mark_viewed:
            application_crud.mark_as_viewed(db, application_id=application_id)

        return application

    def update_application(
            self,
            db: Session,
            *,
            application_id: UUID,
            user: User,
            payload: JobApplicationUpdate
    ) -> JobApplication:
        """Update own application (applicant only)."""
        application = application_crud.get(db, id=application_id)
        if not application:
            raise NotFoundException("Application")

        # Only applicant can update
        if application.applicant_id != user.id:
            raise PermissionDeniedException("You can only update your own application")

        # Can't update if already processed
        if application.status not in [ApplicationStatus.PENDING, ApplicationStatus.REVIEWED]:
            raise ValidationException("Cannot update application at this stage")

        # Update
        updated = application_crud.update(db, db_obj=application, obj_in=payload)
        return updated

    def withdraw_application(self, db: Session, *, application_id: UUID, user: User) -> None:
        """Withdraw application (applicant only)."""
        application = application_crud.get(db, id=application_id)
        if not application:
            raise NotFoundException("Application")

        # Only applicant can withdraw
        if application.applicant_id != user.id:
            raise PermissionDeniedException("You can only withdraw your own application")

        # Update status
        application_crud.update_status(
            db,
            application_id=application_id,
            status=ApplicationStatus.WITHDRAWN
        )

    def update_application_status(
            self,
            db: Session,
            *,
            application_id: UUID,
            user: User,
            payload: JobApplicationStatusUpdate
    ) -> JobApplication:
        """Update application status (employer only)."""
        application = application_crud.get(db, id=application_id)
        if not application:
            raise NotFoundException("Application")

        # Only employer can update status
        if application.job.business.user_id != user.id:
            raise PermissionDeniedException("Only the employer can update application status")

        # Update
        updated = application_crud.update_status(
            db,
            application_id=application_id,
            status=payload.status,
            notes=payload.notes
        )

        return updated

    def list_job_applications(
            self,
            db: Session,
            *,
            job_id: UUID,
            user: User,
            status: Optional[ApplicationStatus] = None,
            skip: int = 0,
            limit: int = 20
    ) -> Tuple[List[JobApplication], int]:
        """List all applications for a job (employer only)."""
        # Verify job exists and user is employer
        job = job_crud.get(db, id=job_id)
        if not job:
            raise NotFoundException("Job posting")

        if job.business.user_id != user.id:
            raise PermissionDeniedException("You don't own this job posting")

        # Get applications
        applications, total = application_crud.get_job_applications(
            db,
            job_id=job_id,
            status=status,
            skip=skip,
            limit=limit
        )

        return applications, total

    def list_user_applications(
            self,
            db: Session,
            *,
            user: User,
            status: Optional[ApplicationStatus] = None,
            skip: int = 0,
            limit: int = 20
    ) -> Tuple[List[JobApplication], int]:
        """List all applications by current user."""
        applications, total = application_crud.get_user_applications(
            db,
            applicant_id=user.id,
            status=status,
            skip=skip,
            limit=limit
        )

        return applications, total


# Singleton instances
job_posting_service = JobPostingService()
job_application_service = JobApplicationService()