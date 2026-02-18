from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_current_active_user
from app.models.user import User
from app.schemas.rider import RiderOut, RiderUpdate, RiderLocationUpdate, RiderStatsOut
from app.schemas.common import SuccessResponse
from app.crud.rider import rider_crud

router = APIRouter()


@router.get("/my-profile", response_model=SuccessResponse[RiderOut])
def get_my_rider_profile(
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """Get current rider's profile."""
    rider = rider_crud.get_by_user_id(db, user_id=user.id)
    if not rider:
        return {"success": False, "error": {"message": "No rider profile found"}}

    return {"success": True, "data": rider}


@router.put("/my-profile", response_model=SuccessResponse[RiderOut])
def update_my_rider_profile(
        payload: RiderUpdate,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """Update current rider's profile."""
    rider = rider_crud.get_by_user_id(db, user_id=user.id)
    if not rider:
        return {"success": False, "error": {"message": "No rider profile found"}}

    updated = rider_crud.update(db, db_obj=rider, obj_in=payload)
    return {"success": True, "data": updated}


@router.post("/update-location", response_model=SuccessResponse[dict])
def update_location(
        payload: RiderLocationUpdate,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """Update rider's current location."""
    rider = rider_crud.get_by_user_id(db, user_id=user.id)
    if not rider:
        return {"success": False, "error": {"message": "No rider profile found"}}

    rider_crud.update_location(
        db,
        rider_id=rider.id,
        latitude=payload.latitude,
        longitude=payload.longitude
    )

    return {"success": True, "data": {"message": "Location updated"}}


@router.post("/go-online", response_model=SuccessResponse[dict])
def go_online(
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """Set rider status to online."""
    rider = rider_crud.get_by_user_id(db, user_id=user.id)
    if not rider:
        return {"success": False, "error": {"message": "No rider profile found"}}

    rider_crud.set_online_status(db, rider_id=rider.id, is_online=True)
    return {"success": True, "data": {"message": "You are now online"}}


@router.post("/go-offline", response_model=SuccessResponse[dict])
def go_offline(
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """Set rider status to offline."""
    rider = rider_crud.get_by_user_id(db, user_id=user.id)
    if not rider:
        return {"success": False, "error": {"message": "No rider profile found"}}

    rider_crud.set_online_status(db, rider_id=rider.id, is_online=False)
    return {"success": True, "data": {"message": "You are now offline"}}