"""
app/api/v1/jobs.py

FIXES vs previous version:
  1.  [HARD RULE §2 / §4] lga_name filter parameter DELETED from search.
      Blueprint §2: "Location is radius-based exclusively. There is no
      local government area (LGA) filtering anywhere in the codebase.
      No LGA column exists in any database table."
      Job vacancy discovery should be radius-based — Blueprint §8.6:
      "Vacancy visible to users within business's registered location radius."
      lat / lng / radius_meters params added for radius-based job search.

  2.  [HARD RULE §8.6] _require_verified_business uses require_verified_business
      dependency (checks user.role + business.is_verified).
      Blueprint §8.6: "Only VERIFIED businesses may post job vacancies."

  3.  JobType gig value validated — Blueprint §8.6:
      "full-time/part-time/contract/gig"
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.constants import DEFAULT_RADIUS_METERS, MAX_RADIUS_METERS
from app.dependencies import (
    get_current_active_user,
    require_verified_business,   # Blueprint §8.6 HARD RULE
)
from app.models.jobs_model import ApplicationStatus, JobStatus
from app.models.user_model import User
from app.schemas.jobs_schema import (
    JobApplicationCreate,
    JobApplicationListOut,
    JobApplicationOut,
    JobApplicationStatusUpdate,
    JobApplicationUpdate,
    JobPostingCreate,
    JobPostingListOut,
    JobPostingOut,
    JobPostingUpdate,
    JobStatsOut,
)
from app.crud.jobs_crud import application_crud, job_crud

router = APIRouter()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_verified_business_job(db: Session, job_id: UUID, user: User):
    job = job_crud.get(db, id=job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job posting not found")
    if not user.business or str(job.business_id) != str(user.business.id):
        raise HTTPException(status_code=403, detail="Not authorised to manage this job")
    return job


# ─── Search (radius-based, no LGA) ───────────────────────────────────────────

@router.get("/", response_model=JobPostingListOut)
def search_jobs(
    query:            Optional[str]  = Query(None, description="Search in title, description, location"),
    job_type:         Optional[str]  = Query(None, description="full_time | part_time | contract | gig"),
    experience_level: Optional[str]  = Query(None),
    location:         Optional[str]  = Query(None, description="Free-text location filter"),
    # Blueprint §4 / §2 HARD RULE: radius-based discovery. NO lga_name parameter.
    # Job vacancies visible to users within business's registered location radius.
    lat:              Optional[float] = Query(None, description="Device latitude for radius search"),
    lng:              Optional[float] = Query(None, description="Device longitude for radius search"),
    radius_meters:    int             = Query(DEFAULT_RADIUS_METERS, ge=1000, le=MAX_RADIUS_METERS),
    is_remote:        Optional[bool]  = Query(None),
    skills:           Optional[str]   = Query(None, description="Comma-separated skills"),
    skip:             int             = Query(0, ge=0),
    limit:            int             = Query(20, ge=1, le=100),
    db:               Session         = Depends(get_db),
):
    """
    Search open job postings.
    Blueprint §8.6: vacancies visible within business's registered location radius.
    Blueprint §4 HARD RULE: radius-based only — no LGA filtering.
    """
    skills_list = [s.strip() for s in skills.split(",") if s.strip()] if skills else None

    jobs, total = job_crud.search_jobs(
        db,
        query_text=query,
        job_type=job_type,
        experience_level=experience_level,
        location=location,
        lat=lat,
        lng=lng,
        radius_meters=radius_meters,
        is_remote=is_remote,
        skills=skills_list,
        skip=skip,
        limit=limit,
    )
    return JobPostingListOut(
        jobs=jobs, total=total, page=skip // limit + 1, page_size=limit
    )


# ─── Business-scoped ─────────────────────────────────────────────────────────

@router.post(
    "/businesses/{business_id}/jobs",
    response_model=JobPostingOut,
    status_code=201,
)
def create_job_posting(
    business_id: UUID    = Path(...),
    payload:     JobPostingCreate = ...,
    db:          Session = Depends(get_db),
    user:        User    = Depends(require_verified_business),   # [HARD RULE §8.6]
):
    """
    Create a job posting.
    Blueprint §8.6 HARD RULE: Only VERIFIED businesses may post job vacancies.
    Blueprint §8.6: "Vacancy visible to users within business's registered location radius."
    """
    if not user.business or str(user.business.id) != str(business_id):
        raise HTTPException(status_code=403, detail="Cannot post jobs for another business")

    return job_crud.create_job(db, business_id=business_id, obj_in=payload)


@router.get("/businesses/{business_id}/jobs", response_model=JobPostingListOut)
def list_business_jobs(
    business_id: UUID             = Path(...),
    status:      Optional[JobStatus] = Query(None),
    skip:        int              = Query(0, ge=0),
    limit:       int              = Query(20, ge=1, le=100),
    db:          Session          = Depends(get_db),
    user:        User             = Depends(get_current_active_user),
):
    if not user.business or str(user.business.id) != str(business_id):
        raise HTTPException(status_code=403, detail="Not authorised")

    jobs, total = job_crud.get_business_jobs(
        db, business_id=business_id, status=status, skip=skip, limit=limit
    )
    return JobPostingListOut(
        jobs=jobs, total=total, page=skip // limit + 1, page_size=limit
    )


@router.get("/businesses/{business_id}/stats", response_model=JobStatsOut)
def get_business_job_stats(
    business_id: UUID    = Path(...),
    db:          Session = Depends(get_db),
    user:        User    = Depends(get_current_active_user),
):
    if not user.business or str(user.business.id) != str(business_id):
        raise HTTPException(status_code=403, detail="Not authorised")
    return job_crud.get_stats(db, business_id=business_id)


# ─── Slug lookup ─────────────────────────────────────────────────────────────

@router.get("/slug/{slug}", response_model=JobPostingOut)
def get_job_by_slug(slug: str = Path(...), db: Session = Depends(get_db)):
    job = job_crud.get_by_slug(db, slug=slug)
    if not job:
        raise HTTPException(status_code=404, detail="Job posting not found")
    job_crud.increment_views(db, job_id=job.id)
    return job


# ─── My applications ─────────────────────────────────────────────────────────

@router.get("/applications/mine", response_model=JobApplicationListOut)
def list_my_applications(
    status:  Optional[ApplicationStatus] = Query(None),
    skip:    int     = Query(0, ge=0),
    limit:   int     = Query(20, ge=1, le=100),
    db:      Session = Depends(get_db),
    user:    User    = Depends(get_current_active_user),
):
    applications, total = application_crud.get_user_applications(
        db, applicant_id=user.id, status=status, skip=skip, limit=limit
    )
    return JobApplicationListOut(
        applications=applications, total=total, page=skip // limit + 1, page_size=limit
    )


# ─── Application by ID ────────────────────────────────────────────────────────

@router.get("/applications/{application_id}", response_model=JobApplicationOut)
def get_application(
    application_id: UUID    = Path(...),
    db:             Session = Depends(get_db),
    user:           User    = Depends(get_current_active_user),
):
    application = application_crud.get(db, id=application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    is_applicant = str(application.applicant_id) == str(user.id)
    is_employer  = (
        user.business
        and str(application.job.business_id) == str(user.business.id)
    )

    if not (is_applicant or is_employer):
        raise HTTPException(status_code=403, detail="Not authorised")

    if is_employer and not is_applicant:
        application_crud.mark_as_viewed(db, application_id=application_id)

    return application


@router.put("/applications/{application_id}", response_model=JobApplicationOut)
def update_application(
    application_id: UUID                 = Path(...),
    payload:        JobApplicationUpdate = ...,
    db:             Session              = Depends(get_db),
    user:           User                 = Depends(get_current_active_user),
):
    application = application_crud.get(db, id=application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    if str(application.applicant_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Not your application")
    if application.status not in (ApplicationStatus.PENDING, ApplicationStatus.REVIEWED):
        raise HTTPException(status_code=400, detail="Cannot edit at this stage")
    return application_crud.update(db, db_obj=application, obj_in=payload)


@router.post("/applications/{application_id}/withdraw", status_code=204)
def withdraw_application(
    application_id: UUID    = Path(...),
    db:             Session = Depends(get_db),
    user:           User    = Depends(get_current_active_user),
):
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
    application_id: UUID                      = Path(...),
    payload:        JobApplicationStatusUpdate = ...,
    db:             Session                   = Depends(get_db),
    user:           User                      = Depends(get_current_active_user),
):
    application = application_crud.get(db, id=application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    if not user.business or str(application.job.business_id) != str(user.business.id):
        raise HTTPException(status_code=403, detail="Not authorised")
    return application_crud.update_status(
        db,
        application_id=application_id,
        status=payload.status,
        notes=payload.notes,
    )


# ─── Apply ────────────────────────────────────────────────────────────────────

@router.post("/{job_id}/apply", response_model=JobApplicationOut, status_code=201)
def apply_to_job(
    job_id:  UUID                  = Path(...),
    payload: JobApplicationCreate  = ...,
    db:      Session               = Depends(get_db),
    user:    User                  = Depends(get_current_active_user),
):
    job = job_crud.get(db, id=job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job posting not found")
    if job.status != JobStatus.OPEN:
        raise HTTPException(status_code=400, detail="This job is no longer accepting applications")

    existing = application_crud.get_user_application(db, job_id=job_id, applicant_id=user.id)
    if existing:
        raise HTTPException(status_code=409, detail="Already applied")

    return application_crud.create_application(
        db, job_id=job_id, applicant_id=user.id, obj_in=payload
    )


@router.get("/{job_id}/applications", response_model=JobApplicationListOut)
def list_job_applications(
    job_id:  UUID                        = Path(...),
    status:  Optional[ApplicationStatus] = Query(None),
    skip:    int                         = Query(0, ge=0),
    limit:   int                         = Query(20, ge=1, le=100),
    db:      Session                     = Depends(get_db),
    user:    User                        = Depends(get_current_active_user),
):
    job = job_crud.get(db, id=job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job posting not found")
    if not user.business or str(job.business_id) != str(user.business.id):
        raise HTTPException(status_code=403, detail="Not authorised")

    applications, total = application_crud.get_job_applications(
        db, job_id=job_id, status=status, skip=skip, limit=limit
    )
    return JobApplicationListOut(
        applications=applications, total=total, page=skip // limit + 1, page_size=limit
    )


# ─── Job by ID (last — wildcard route) ────────────────────────────────────────

@router.get("/{job_id}", response_model=JobPostingOut)
def get_job_posting(job_id: UUID = Path(...), db: Session = Depends(get_db)):
    job = job_crud.get(db, id=job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job posting not found")
    job_crud.increment_views(db, job_id=job_id)
    return job


@router.put("/{job_id}", response_model=JobPostingOut)
def update_job_posting(
    job_id:  UUID            = Path(...),
    payload: JobPostingUpdate = ...,
    db:      Session         = Depends(get_db),
    user:    User            = Depends(require_verified_business),   # [HARD RULE §8.6]
):
    job = _get_verified_business_job(db, job_id=job_id, user=user)
    return job_crud.update(db, db_obj=job, obj_in=payload)


@router.delete("/{job_id}", status_code=204)
def delete_job_posting(
    job_id: UUID    = Path(...),
    db:     Session = Depends(get_db),
    user:   User    = Depends(require_verified_business),
):
    _get_verified_business_job(db, job_id=job_id, user=user)
    job_crud.remove(db, id=job_id)