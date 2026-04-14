from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID
from pydantic import BaseModel

from app.core.database import get_db
from app.dependencies import get_current_active_user, require_role
from app.models.user_model import User
from app.schemas.rider_schema import (
    RiderOut,
    RiderUpdate,
    RiderLocationUpdate,
    RiderOnlineStatusUpdate,
    RiderStatsOut,
)
from app.schemas.delivery_schema import DeliveryOut, EarningsSummaryOut
from app.schemas.common_schema import SuccessResponse
from app.crud.rider_crud import rider_crud
from app.crud.delivery_crud import delivery_crud

router = APIRouter()

# ---------------------------------------------------------------------------
# Reusable helpers
# ---------------------------------------------------------------------------

_rider_only = Depends(require_role("rider"))


def _get_rider_or_404(db: Session, user_id):
    rider = rider_crud.get_by_user_id(db, user_id=user_id)
    if not rider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No rider profile found for this user",
        )
    return rider


# ---------------------------------------------------------------------------
# Inline schema for job status update
# FIX: replaced bare `dict` with a proper Pydantic model.
# FastAPI cannot reliably parse a raw dict as a JSON body — it requires a
# BaseModel subclass or an explicit Body(...) annotation.
# ---------------------------------------------------------------------------

class DeliveryStatusUpdate(BaseModel):
    status: str  # picked_up | in_transit | delivered | cancelled


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@router.get("/my-profile", response_model=SuccessResponse[RiderOut])
def get_my_rider_profile(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    _: None = _rider_only,
):
    """Get the current rider's profile."""
    rider = _get_rider_or_404(db, user.id)
    return {"success": True, "data": rider}


@router.put("/my-profile", response_model=SuccessResponse[RiderOut])
def update_my_rider_profile(
    payload: RiderUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    _: None = _rider_only,
):
    """Update the current rider's profile."""
    rider = _get_rider_or_404(db, user.id)
    updated = rider_crud.update(db, db_obj=rider, obj_in=payload)
    return {"success": True, "data": updated}


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

@router.post("/update-location", response_model=SuccessResponse[dict])
def update_location(
    payload: RiderLocationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    _: None = _rider_only,
):
    """
    Update the rider's current GPS location.
    Called frequently while online — kept intentionally lightweight.
    """
    rider = _get_rider_or_404(db, user.id)
    rider_crud.update_location(
        db,
        rider_id=rider.id,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    return {"success": True, "data": {"message": "Location updated"}}


# ---------------------------------------------------------------------------
# Online / offline status
# ---------------------------------------------------------------------------

@router.post("/status", response_model=SuccessResponse[dict])
def set_online_status(
    payload: RiderOnlineStatusUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    _: None = _rider_only,
):
    """
    Toggle the rider's availability.
    Replaces the old /go-online and /go-offline split so the Flutter client
    can use a single call with {'is_online': true/false}.
    """
    rider = _get_rider_or_404(db, user.id)

    if payload.is_online and not rider.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account must be verified before going online",
        )

    rider_crud.set_online_status(db, rider_id=rider.id, is_online=payload.is_online)
    message = "You are now online" if payload.is_online else "You are now offline"
    return {"success": True, "data": {"message": message, "is_online": payload.is_online}}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.get("/stats", response_model=SuccessResponse[RiderStatsOut])
def get_my_stats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    _: None = _rider_only,
):
    """Aggregated performance stats for the current rider."""
    rider = _get_rider_or_404(db, user.id)
    cancelled = max(rider.total_deliveries - rider.completed_deliveries, 0)
    stats = RiderStatsOut(
        total_deliveries=rider.total_deliveries,
        completed_deliveries=rider.completed_deliveries,
        cancelled_deliveries=cancelled,
        total_earnings=0,           # Populated by Wallet ledger query
        average_rating=rider.average_rating,
        completion_rate=rider.completion_rate,
    )
    return {"success": True, "data": stats}


# ---------------------------------------------------------------------------
# Job feed  (delivery jobs assigned to / available for this rider)
# ---------------------------------------------------------------------------

@router.get("/jobs", response_model=SuccessResponse[List[DeliveryOut]])
def get_job_feed(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    _: None = _rider_only,
):
    """
    Return pending delivery jobs near the rider's current location.
    Only returned when rider is online and verified.
    """
    rider = _get_rider_or_404(db, user.id)

    if not rider.is_online:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Go online to see available jobs",
        )

    jobs = delivery_crud.get_available_jobs_for_rider(db, rider=rider)
    return {"success": True, "data": jobs}


@router.get("/jobs/active", response_model=SuccessResponse[DeliveryOut])
def get_active_job(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    _: None = _rider_only,
):
    """
    Return the rider's currently active delivery job, or 404 if none.
    The Flutter client handles 404 by returning null — no active job.
    """
    rider = _get_rider_or_404(db, user.id)
    job = delivery_crud.get_active_job_for_rider(db, rider_id=rider.id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active delivery job",
        )
    return {"success": True, "data": job}


@router.post("/jobs/{job_id}/accept", response_model=SuccessResponse[DeliveryOut])
def accept_job(
    job_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    _: None = _rider_only,
):
    """
    Accept a pending delivery job.
    Fails if the job is already accepted by another rider or the
    rider already has an active job.
    """
    rider = _get_rider_or_404(db, user.id)

    existing = delivery_crud.get_active_job_for_rider(db, rider_id=rider.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have an active delivery. Complete it first.",
        )

    job = delivery_crud.accept_job(db, job_id=job_id, rider_id=rider.id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found or already taken",
        )
    return {"success": True, "data": job}


@router.patch("/jobs/{job_id}/status", response_model=SuccessResponse[DeliveryOut])
def update_job_status(
    job_id: UUID,
    payload: DeliveryStatusUpdate,   # FIX: was bare dict — FastAPI can't parse that
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    _: None = _rider_only,
):
    """
    Advance the delivery status.
    Valid transitions: accepted → picked_up → in_transit → delivered.
    A rider may also cancel with status='cancelled'.
    """
    rider = _get_rider_or_404(db, user.id)

    valid_statuses = {"picked_up", "in_transit", "delivered", "cancelled"}
    if payload.status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid status. Must be one of: {valid_statuses}",
        )

    job = delivery_crud.update_job_status(
        db,
        job_id=job_id,
        rider_id=rider.id,
        new_status=payload.status,
    )
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found or does not belong to you",
        )
    return {"success": True, "data": job}


# ---------------------------------------------------------------------------
# Earnings
# ---------------------------------------------------------------------------

@router.get("/earnings", response_model=SuccessResponse[EarningsSummaryOut])
def get_earnings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
    _: None = _rider_only,
):
    """
    Return aggregated earnings for the current rider broken down by
    today / this week / this month / lifetime, plus total distance covered.
    Sourced from the Wallet ledger via delivery_crud.
    """
    rider = _get_rider_or_404(db, user.id)
    summary = delivery_crud.get_earnings_summary(db, rider_id=rider.id)
    return {"success": True, "data": summary}