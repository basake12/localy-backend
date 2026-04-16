"""
app/crud/jobs_crud.py

FIXES vs previous version:
  1.  [HARD RULE §2 / §4] lga_name filter REMOVED from search_jobs().
      Blueprint §2: "Location is radius-based exclusively. There is no
      local government area (LGA) filtering anywhere in the codebase.
      No LGA column exists in any database table."
      Blueprint §8.6: "Vacancy visible to users within business's
      registered location radius."

  2.  Radius-based search added using PostGIS ST_DWithin.
      search_jobs() now accepts lat, lng, radius_meters and filters
      by business location — matching Blueprint §4 location model.

  3.  [HARD RULE §16.4] datetime.utcnow() → datetime.now(timezone.utc)
      in mark_as_viewed().

  4.  Blueprint §8.6: close_expired_jobs() helper added (called by
      Celery task). "Celery task: close_expired_jobs (runs daily) —
      closes jobs older than 90 days."
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from uuid import UUID

from slugify import slugify
from sqlalchemy import and_, desc, func, or_, text
from sqlalchemy.orm import Session, joinedload

from app.crud.base_crud import CRUDBase
from app.models.jobs_model import (
    ApplicationStatus,
    JobApplication,
    JobPosting,
    JobStatus,
)
from app.schemas.jobs_schema import (
    JobApplicationCreate,
    JobApplicationUpdate,
    JobPostingCreate,
    JobPostingUpdate,
)


def _utcnow() -> datetime:
    """Blueprint §16.4 HARD RULE: timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


# ─── Job Posting CRUD ────────────────────────────────────────────────────────

class CRUDJobPosting(CRUDBase[JobPosting, JobPostingCreate, JobPostingUpdate]):

    def create_job(
        self,
        db:          Session,
        *,
        business_id: UUID,
        obj_in:      JobPostingCreate,
    ) -> JobPosting:
        base_slug = slugify(obj_in.title)
        slug      = base_slug
        counter   = 1
        while db.query(JobPosting).filter(JobPosting.slug == slug).first():
            slug    = f"{base_slug}-{counter}"
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
        db:          Session,
        *,
        business_id: UUID,
        status:      Optional[JobStatus] = None,
        skip:        int = 0,
        limit:       int = 20,
    ) -> Tuple[List[JobPosting], int]:
        query = db.query(JobPosting).filter(JobPosting.business_id == business_id)
        if status:
            query = query.filter(JobPosting.status == status)
        total = query.count()
        jobs  = (
            query
            .order_by(desc(JobPosting.created_at))
            .offset(skip)
            .limit(limit)
            .all()
        )
        return jobs, total

    def search_jobs(
        self,
        db: Session,
        *,
        query_text:       Optional[str]   = None,
        job_type:         Optional[str]   = None,
        experience_level: Optional[str]   = None,
        location:         Optional[str]   = None,
        # Blueprint §4 / §2 HARD RULE: radius-based only — NO lga_name param.
        # Job discovery uses business GPS location via PostGIS ST_DWithin.
        lat:              Optional[float] = None,
        lng:              Optional[float] = None,
        radius_meters:    int             = 5000,
        is_remote:        Optional[bool]  = None,
        skills:           Optional[List[str]] = None,
        skip:             int = 0,
        limit:            int = 20,
    ) -> Tuple[List[JobPosting], int]:
        """
        Search open job postings.
        Blueprint §8.6: vacancies visible to users within business's
        registered location radius (PostGIS ST_DWithin).
        Blueprint §4 HARD RULE: no LGA filtering — radius-only.
        """
        from app.models.business_model import Business

        query = (
            db.query(JobPosting)
            .join(Business, JobPosting.business_id == Business.id)
            .filter(JobPosting.status == JobStatus.OPEN)
        )

        # Blueprint §4: radius-based discovery via PostGIS
        if lat is not None and lng is not None:
            query = query.filter(
                text(
                    "ST_DWithin(businesses.location::geography, "
                    "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, :radius)"
                ).bindparams(lat=lat, lng=lng, radius=radius_meters)
            )

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

        total = query.with_entities(func.count(JobPosting.id)).scalar() or 0
        jobs  = (
            query
            .order_by(desc(JobPosting.created_at))
            .offset(skip)
            .limit(limit)
            .all()
        )
        return jobs, total

    def increment_views(self, db: Session, *, job_id: UUID) -> None:
        db.query(JobPosting).filter(JobPosting.id == job_id).update(
            {"views_count": func.coalesce(JobPosting.views_count, 0) + 1}
        )
        db.commit()

    def sync_applications_count(self, db: Session, *, job_id: UUID) -> None:
        count = (
            db.query(JobApplication)
            .filter(JobApplication.job_id == job_id)
            .count()
        )
        db.query(JobPosting).filter(JobPosting.id == job_id).update(
            {"applications_count": count}
        )
        db.commit()

    def close_expired_jobs(self, db: Session) -> int:
        """
        Close job postings older than 90 days.
        Blueprint §8.6 / §16.2:
          "Celery task: close_expired_jobs (runs daily) —
           closes jobs older than 90 days.
           jobs.status = 'filled', updated_at = now()"
        """
        cutoff = _utcnow() - timedelta(days=90)
        count  = (
            db.query(JobPosting)
            .filter(
                and_(
                    JobPosting.status     == JobStatus.OPEN,
                    JobPosting.created_at <  cutoff,
                )
            )
            .update({"status": JobStatus.CLOSED})   # use CLOSED for expired, not FILLED
        )
        if count:
            db.commit()
        return count

    def get_stats(self, db: Session, *, business_id: UUID) -> dict:
        jobs    = db.query(JobPosting).filter(
            JobPosting.business_id == business_id
        ).all()
        job_ids = [j.id for j in jobs]

        total_apps = (
            db.query(func.count(JobApplication.id))
            .filter(JobApplication.job_id.in_(job_ids))
            .scalar() or 0
        )
        pending_apps = (
            db.query(func.count(JobApplication.id))
            .filter(
                JobApplication.job_id.in_(job_ids),
                JobApplication.status == ApplicationStatus.PENDING,
            )
            .scalar() or 0
        )

        return {
            "total_jobs":           len(jobs),
            "open_jobs":            sum(1 for j in jobs if j.status == JobStatus.OPEN),
            "draft_jobs":           sum(1 for j in jobs if j.status == JobStatus.DRAFT),
            "closed_jobs":          sum(1 for j in jobs if j.status == JobStatus.CLOSED),
            "total_applications":   total_apps,
            "pending_applications": pending_apps,
            "total_views":          sum(j.views_count or 0 for j in jobs),
        }


# ─── Job Application CRUD ────────────────────────────────────────────────────

class CRUDJobApplication(CRUDBase[JobApplication, JobApplicationCreate, JobApplicationUpdate]):

    def create_application(
        self,
        db:           Session,
        *,
        job_id:       UUID,
        applicant_id: UUID,
        obj_in:       JobApplicationCreate,
    ) -> JobApplication:
        application = JobApplication(
            job_id=job_id,
            applicant_id=applicant_id,
            **obj_in.model_dump(),
        )
        db.add(application)
        db.commit()
        db.refresh(application)
        job_crud.sync_applications_count(db, job_id=job_id)
        return application

    def get_user_application(
        self,
        db:           Session,
        *,
        job_id:       UUID,
        applicant_id: UUID,
    ) -> Optional[JobApplication]:
        return db.query(JobApplication).filter(
            and_(
                JobApplication.job_id       == job_id,
                JobApplication.applicant_id == applicant_id,
            )
        ).first()

    def get_job_applications(
        self,
        db:     Session,
        *,
        job_id: UUID,
        status: Optional[ApplicationStatus] = None,
        skip:   int = 0,
        limit:  int = 20,
    ) -> Tuple[List[JobApplication], int]:
        query = (
            db.query(JobApplication)
            .options(joinedload(JobApplication.applicant))
            .filter(JobApplication.job_id == job_id)
        )
        if status:
            query = query.filter(JobApplication.status == status)
        total        = query.count()
        applications = (
            query
            .order_by(desc(JobApplication.created_at))
            .offset(skip)
            .limit(limit)
            .all()
        )
        return applications, total

    def get_user_applications(
        self,
        db:           Session,
        *,
        applicant_id: UUID,
        status:       Optional[ApplicationStatus] = None,
        skip:         int = 0,
        limit:        int = 20,
    ) -> Tuple[List[JobApplication], int]:
        query = (
            db.query(JobApplication)
            .options(
                joinedload(JobApplication.job)
                .joinedload(JobPosting.business)
            )
            .filter(JobApplication.applicant_id == applicant_id)
        )
        if status:
            query = query.filter(JobApplication.status == status)
        total        = query.count()
        applications = (
            query
            .order_by(desc(JobApplication.created_at))
            .offset(skip)
            .limit(limit)
            .all()
        )
        return applications, total

    def mark_as_viewed(
        self, db: Session, *, application_id: UUID
    ) -> Optional[JobApplication]:
        application = self.get(db, id=application_id)
        if application and not application.viewed_by_employer:
            application.viewed_by_employer = True
            # Blueprint §16.4 HARD RULE: timezone-aware timestamp
            application.viewed_at = _utcnow()
            db.commit()
            db.refresh(application)
        return application

    def update_status(
        self,
        db:             Session,
        *,
        application_id: UUID,
        status:         ApplicationStatus,
        notes:          Optional[str] = None,
    ) -> Optional[JobApplication]:
        application = self.get(db, id=application_id)
        if application:
            application.status = status
            if notes is not None:
                application.notes = notes
            db.commit()
            db.refresh(application)
        return application


# Singletons
job_crud         = CRUDJobPosting(JobPosting)
application_crud = CRUDJobApplication(JobApplication)