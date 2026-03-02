from fastapi import APIRouter, Depends, Query, Path
from sqlalchemy.orm import Session
from typing import Optional, List
from uuid import UUID

from app.core.database import get_db
from app.dependencies import get_current_user, get_current_active_user
from app.models.user_model import User
from app.models.jobs_model import JobStatus, ApplicationStatus
from app.schemas.jobs_schema import (
    JobPostingCreate,
    JobPostingUpdate,
    JobPostingOut,
    JobPostingListOut,
    JobApplicationCreate,
    JobApplicationUpdate,
    JobApplicationStatusUpdate,
    JobApplicationOut,
    JobApplicationListOut,
    JobStatsOut,
)
from app.services.job_service import job_posting_service, job_application_service

router = APIRouter()


# ============================================
# JOB POSTINGS - CRUD
# ============================================

@router.post("/businesses/{business_id}/jobs", response_model=JobPostingOut, status_code=201)
def create_job_posting(
        business_id: UUID = Path(...),
        payload: JobPostingCreate = ...,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """
    Create a new job posting for your business.
    """
    job = job_posting_service.create_job(
        db,
        business_id=business_id,
        user=user,
        payload=payload
    )
    return job


@router.get("/{job_id}", response_model=JobPostingOut)
def get_job_posting(
        job_id: UUID = Path(...),
        db: Session = Depends(get_db),
):
    """
    Get job posting by ID (public endpoint).
    View count is incremented.
    """
    job = job_posting_service.get_job(db, job_id=job_id, increment_views=True)
    return job


@router.get("/slug/{slug}", response_model=JobPostingOut)
def get_job_by_slug(
        slug: str = Path(...),
        db: Session = Depends(get_db),
):
    """
    Get job posting by slug (public endpoint).
    View count is incremented.
    """
    job = job_posting_service.get_job_by_slug(db, slug=slug, increment_views=True)
    return job


@router.put("/{job_id}", response_model=JobPostingOut)
def update_job_posting(
        job_id: UUID = Path(...),
        payload: JobPostingUpdate = ...,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """
    Update your job posting.
    """
    job = job_posting_service.update_job(
        db,
        job_id=job_id,
        user=user,
        payload=payload
    )
    return job


@router.delete("/{job_id}", status_code=204)
def delete_job_posting(
        job_id: UUID = Path(...),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """
    Delete your job posting.
    """
    job_posting_service.delete_job(db, job_id=job_id, user=user)


# ============================================
# JOB POSTINGS - LISTING
# ============================================

@router.get("/businesses/{business_id}/jobs", response_model=JobPostingListOut)
def list_business_jobs(
        business_id: UUID = Path(...),
        status: Optional[JobStatus] = Query(None),
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """
    List all jobs for your business.
    """
    jobs, total = job_posting_service.list_business_jobs(
        db,
        business_id=business_id,
        user=user,
        status=status,
        skip=skip,
        limit=limit
    )

    return JobPostingListOut(
        jobs=jobs,
        total=total,
        page=skip // limit + 1,
        page_size=limit
    )


@router.get("", response_model=JobPostingListOut)
def search_jobs(
        query: Optional[str] = Query(None, description="Search in title, description, location"),
        job_type: Optional[str] = Query(None),
        experience_level: Optional[str] = Query(None),
        location: Optional[str] = Query(None),
        is_remote: Optional[bool] = Query(None),
        skills: Optional[str] = Query(None, description="Comma-separated skills"),
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        db: Session = Depends(get_db),
):
    """
    Search and filter job postings (public endpoint).
    Only returns jobs with status = OPEN.
    """
    # Parse skills
    skills_list = None
    if skills:
        skills_list = [s.strip() for s in skills.split(",") if s.strip()]

    jobs, total = job_posting_service.search_jobs(
        db,
        query_text=query,
        job_type=job_type,
        experience_level=experience_level,
        location=location,
        is_remote=is_remote,
        skills=skills_list,
        skip=skip,
        limit=limit
    )

    return JobPostingListOut(
        jobs=jobs,
        total=total,
        page=skip // limit + 1,
        page_size=limit
    )


@router.get("/businesses/{business_id}/stats", response_model=JobStatsOut)
def get_business_job_stats(
        business_id: UUID = Path(...),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """
    Get job statistics for your business.
    """
    stats = job_posting_service.get_business_stats(
        db,
        business_id=business_id,
        user=user
    )
    return stats


# ============================================
# JOB APPLICATIONS - CRUD
# ============================================

@router.post("/{job_id}/apply", response_model=JobApplicationOut, status_code=201)
def apply_to_job(
        job_id: UUID = Path(...),
        payload: JobApplicationCreate = ...,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """
    Submit application to a job posting.
    """
    application = job_application_service.apply_to_job(
        db,
        job_id=job_id,
        user=user,
        payload=payload
    )
    return application


@router.get("/applications/{application_id}", response_model=JobApplicationOut)
def get_application(
        application_id: UUID = Path(...),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """
    Get application by ID.
    Accessible by applicant or employer.
    Employer viewing marks as viewed.
    """
    application = job_application_service.get_application(
        db,
        application_id=application_id,
        user=user,
        mark_viewed=True
    )
    return application


@router.put("/applications/{application_id}", response_model=JobApplicationOut)
def update_application(
        application_id: UUID = Path(...),
        payload: JobApplicationUpdate = ...,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """
    Update your own application.
    Only works for pending/reviewed applications.
    """
    application = job_application_service.update_application(
        db,
        application_id=application_id,
        user=user,
        payload=payload
    )
    return application


@router.post("/applications/{application_id}/withdraw", status_code=204)
def withdraw_application(
        application_id: UUID = Path(...),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """
    Withdraw your application.
    """
    job_application_service.withdraw_application(
        db,
        application_id=application_id,
        user=user
    )


@router.put("/applications/{application_id}/status", response_model=JobApplicationOut)
def update_application_status(
        application_id: UUID = Path(...),
        payload: JobApplicationStatusUpdate = ...,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """
    Update application status (employer only).
    Used to mark applications as shortlisted, interview, accepted, rejected.
    """
    application = job_application_service.update_application_status(
        db,
        application_id=application_id,
        user=user,
        payload=payload
    )
    return application


# ============================================
# JOB APPLICATIONS - LISTING
# ============================================

@router.get("/{job_id}/applications", response_model=JobApplicationListOut)
def list_job_applications(
        job_id: UUID = Path(...),
        status: Optional[ApplicationStatus] = Query(None),
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """
    List all applications for your job posting (employer only).
    """
    applications, total = job_application_service.list_job_applications(
        db,
        job_id=job_id,
        user=user,
        status=status,
        skip=skip,
        limit=limit
    )

    return JobApplicationListOut(
        applications=applications,
        total=total,
        page=skip // limit + 1,
        page_size=limit
    )


@router.get("/applications/mine", response_model=JobApplicationListOut)
def list_my_applications(
        status: Optional[ApplicationStatus] = Query(None),
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """
    List all your job applications.
    """
    applications, total = job_application_service.list_user_applications(
        db,
        user=user,
        status=status,
        skip=skip,
        limit=limit
    )

    return JobApplicationListOut(
        applications=applications,
        total=total,
        page=skip // limit + 1,
        page_size=limit
    )