from fastapi import APIRouter, Depends, Query, Path, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID

from app.core.database import get_db
from app.dependencies import get_current_active_user
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
from app.crud.jobs_crud import job_crud, application_crud

router = APIRouter()


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _get_verified_business_job(
        db: Session,
        job_id: UUID,
        user: User,
) -> object:
    """Fetch a job and verify the requesting user owns the business."""
    job = job_crud.get(db, id=job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job posting not found")
    if str(job.business_id) != str(user.business_id):
        raise HTTPException(status_code=403, detail="Not authorised to manage this job")
    return job


def _require_verified_business(user: User) -> None:
    """Raise 403 if the user does not own a verified business."""
    if not user.business_id:
        raise HTTPException(
            status_code=403,
            detail="Only verified businesses can perform this action",
        )
    if not user.business or not user.business.is_verified:
        raise HTTPException(
            status_code=403,
            detail="Your business must be verified before posting jobs",
        )


# ──────────────────────────────────────────────────────────────────────────────
# NOTE ON ROUTE ORDER: FastAPI matches routes top-down.
# Fixed-path routes (/businesses/..., /slug/..., /applications/...) MUST come
# before any wildcard route (/{job_id}) or they will be swallowed.
# ──────────────────────────────────────────────────────────────────────────────


# ============================================
# SEARCH  (most generic GET, no path params)
# ============================================

@router.get("/", response_model=JobPostingListOut)
def search_jobs(
        query: Optional[str] = Query(None, description="Search in title, description, location"),
        job_type: Optional[str] = Query(None),
        experience_level: Optional[str] = Query(None),
        location: Optional[str] = Query(None),
        # ------------------------------------------------------------------ #
        # BUG FIX                                                              #
        #                                                                      #
        # Root cause: lga_id was typed Optional[UUID]. The JobPosting ORM     #
        # model has no UUID-based LGA FK — it stores LGA as:                  #
        #   lga_name = Column(String(100), nullable=True)                      #
        # LGAs are plain string constants, not a DB table with UUIDs.         #
        #                                                                      #
        # FastAPI tried to parse the incoming string "Uyo" as a UUID and      #
        # returned 422 Unprocessable Entity, causing:                          #
        #   - The 307 → 422 redirect chain visible in the server logs         #
        #   - A Dart Uuid.parse("Uyo") crash in the Flutter exception handler  #
        #     ("invalid character: expected urn:uuid:, found 'U' at 1")       #
        #                                                                      #
        # Fix: rename to lga_name, type as Optional[str], and pass it to the  #
        # CRUD as lga_name= so it filters on the correct column.              #
        # ------------------------------------------------------------------ #
        lga_name: Optional[str] = Query(None, description="Filter by Local Government Area name"),
        is_remote: Optional[bool] = Query(None),
        skills: Optional[str] = Query(None, description="Comma-separated skill list"),
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        db: Session = Depends(get_db),
):
    """Search open job postings. LGA-scoped when lga_name is provided."""
    skills_list = [s.strip() for s in skills.split(",") if s.strip()] if skills else None

    jobs, total = job_crud.search_jobs(
        db,
        query_text=query,
        job_type=job_type,
        experience_level=experience_level,
        location=location,
        # FIX: was lga_id=lga_id (UUID) — now lga_name=lga_name (str)
        lga_name=lga_name,
        is_remote=is_remote,
        skills=skills_list,
        skip=skip,
        limit=limit,
    )
    return JobPostingListOut(jobs=jobs, total=total, page=skip // limit + 1, page_size=limit)


# ============================================
# BUSINESS-SCOPED  (fixed prefix /businesses/)
# MUST be before /{job_id}
# ============================================

@router.post("/businesses/{business_id}/jobs", response_model=JobPostingOut, status_code=201)
def create_job_posting(
        business_id: UUID = Path(...),
        payload: JobPostingCreate = ...,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """Create a job posting. Requires a verified business account."""
    _require_verified_business(user)
    if str(user.business_id) != str(business_id):
        raise HTTPException(status_code=403, detail="Cannot post jobs for another business")

    job = job_crud.create_job(db, business_id=business_id, obj_in=payload)
    return job


@router.get("/businesses/{business_id}/jobs", response_model=JobPostingListOut)
def list_business_jobs(
        business_id: UUID = Path(...),
        status: Optional[JobStatus] = Query(None),
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """List all jobs for a business (owner only)."""
    if str(user.business_id) != str(business_id):
        raise HTTPException(status_code=403, detail="Not authorised")

    jobs, total = job_crud.get_business_jobs(
        db, business_id=business_id, status=status, skip=skip, limit=limit
    )
    return JobPostingListOut(jobs=jobs, total=total, page=skip // limit + 1, page_size=limit)


@router.get("/businesses/{business_id}/stats", response_model=JobStatsOut)
def get_business_job_stats(
        business_id: UUID = Path(...),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """Job statistics for a business (owner only)."""
    if str(user.business_id) != str(business_id):
        raise HTTPException(status_code=403, detail="Not authorised")

    return job_crud.get_stats(db, business_id=business_id)


# ============================================
# SLUG LOOKUP  (fixed prefix /slug/)
# MUST be before /{job_id}
# ============================================

@router.get("/slug/{slug}", response_model=JobPostingOut)
def get_job_by_slug(
        slug: str = Path(...),
        db: Session = Depends(get_db),
):
    """Get a job posting by its URL slug (public). Increments view count."""
    job = job_crud.get_by_slug(db, slug=slug)
    if not job:
        raise HTTPException(status_code=404, detail="Job posting not found")
    job_crud.increment_views(db, job_id=job.id)
    return job


# ============================================
# APPLICATIONS — MY LIST  (fixed path)
# MUST be before /applications/{application_id}
# ============================================

@router.get("/applications/mine", response_model=JobApplicationListOut)
def list_my_applications(
        status: Optional[ApplicationStatus] = Query(None),
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """List all job applications submitted by the current user."""
    applications, total = application_crud.get_user_applications(
        db, applicant_id=user.id, status=status, skip=skip, limit=limit
    )
    return JobApplicationListOut(
        applications=applications, total=total, page=skip // limit + 1, page_size=limit
    )


# ============================================
# APPLICATIONS — BY ID  (parameterised)
# MUST be after /applications/mine
# ============================================

@router.get("/applications/{application_id}", response_model=JobApplicationOut)
def get_application(
        application_id: UUID = Path(...),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """
    Get an application by ID.
    Accessible by the applicant or the employing business owner.
    Employer viewing marks the application as viewed.
    """
    application = application_crud.get(db, id=application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    is_applicant = str(application.applicant_id) == str(user.id)
    is_employer = (
        user.business_id
        and str(application.job.business_id) == str(user.business_id)
    )

    if not (is_applicant or is_employer):
        raise HTTPException(status_code=403, detail="Not authorised")

    if is_employer and not is_applicant:
        application_crud.mark_as_viewed(db, application_id=application_id)

    return application


@router.put("/applications/{application_id}", response_model=JobApplicationOut)
def update_application(
        application_id: UUID = Path(...),
        payload: JobApplicationUpdate = ...,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """Update your own application (only while pending or reviewed)."""
    application = application_crud.get(db, id=application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    if str(application.applicant_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Not your application")
    if application.status not in (ApplicationStatus.PENDING, ApplicationStatus.REVIEWED):
        raise HTTPException(
            status_code=400, detail="Cannot edit an application in its current state"
        )

    return application_crud.update(db, db_obj=application, obj_in=payload)


@router.post("/applications/{application_id}/withdraw", status_code=204)
def withdraw_application(
        application_id: UUID = Path(...),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """Withdraw your application."""
    application = application_crud.get(db, id=application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    if str(application.applicant_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Not your application")

    application_crud.update_status(
        db, application_id=application_id, status=ApplicationStatus.WITHDRAWN
    )


@router.put("/applications/{application_id}/status", response_model=JobApplicationOut)
def update_application_status(
        application_id: UUID = Path(...),
        payload: JobApplicationStatusUpdate = ...,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """Update application status (employer only)."""
    application = application_crud.get(db, id=application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    if not user.business_id or str(application.job.business_id) != str(user.business_id):
        raise HTTPException(status_code=403, detail="Not authorised")

    return application_crud.update_status(
        db,
        application_id=application_id,
        status=payload.status,
        notes=payload.notes,
    )


# ============================================
# JOB POSTING — APPLY  (/{job_id}/apply)
# MUST be before /{job_id} GET to avoid conflict on same prefix
# ============================================

@router.post("/{job_id}/apply", response_model=JobApplicationOut, status_code=201)
def apply_to_job(
        job_id: UUID = Path(...),
        payload: JobApplicationCreate = ...,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """Submit an application for a job posting."""
    job = job_crud.get(db, id=job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job posting not found")
    if job.status != JobStatus.OPEN:
        raise HTTPException(status_code=400, detail="This job is no longer accepting applications")

    existing = application_crud.get_user_application(
        db, job_id=job_id, applicant_id=user.id
    )
    if existing:
        raise HTTPException(status_code=409, detail="You have already applied for this job")

    return application_crud.create_application(
        db, job_id=job_id, applicant_id=user.id, obj_in=payload
    )


@router.get("/{job_id}/applications", response_model=JobApplicationListOut)
def list_job_applications(
        job_id: UUID = Path(...),
        status: Optional[ApplicationStatus] = Query(None),
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """List all applications for a job posting (employer only)."""
    job = job_crud.get(db, id=job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job posting not found")
    if not user.business_id or str(job.business_id) != str(user.business_id):
        raise HTTPException(status_code=403, detail="Not authorised")

    applications, total = application_crud.get_job_applications(
        db, job_id=job_id, status=status, skip=skip, limit=limit
    )
    return JobApplicationListOut(
        applications=applications, total=total, page=skip // limit + 1, page_size=limit
    )


# ============================================
# JOB POSTING — BY ID
# MUST be last among GETs (wildcard /{job_id})
# ============================================

@router.get("/{job_id}", response_model=JobPostingOut)
def get_job_posting(
        job_id: UUID = Path(...),
        db: Session = Depends(get_db),
):
    """Get a job posting by ID (public). Increments view count."""
    job = job_crud.get(db, id=job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job posting not found")
    job_crud.increment_views(db, job_id=job_id)
    return job


@router.put("/{job_id}", response_model=JobPostingOut)
def update_job_posting(
        job_id: UUID = Path(...),
        payload: JobPostingUpdate = ...,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """Update a job posting (owner only)."""
    job = _get_verified_business_job(db, job_id=job_id, user=user)
    return job_crud.update(db, db_obj=job, obj_in=payload)


@router.delete("/{job_id}", status_code=204)
def delete_job_posting(
        job_id: UUID = Path(...),
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user),
):
    """Delete a job posting (owner only)."""
    _get_verified_business_job(db, job_id=job_id, user=user)
    job_crud.remove(db, id=job_id)