"""
app/api/v1/users.py

FIXES:
  1. Profile responses now use UserWithProfileResponse.from_user() which
     extracts wallet_balance from the eager-loaded wallet relation.

  2. Address endpoints now use street/city/lga_name field names matching
     Flutter's CustomerAddress.fromJson() — the old `address` single-string
     field caused Flutter to always show empty street/city/lga_name.

  3. reset_password added to user_crud (called from change_password endpoint).
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from app.core.database import get_db
from app.schemas.common_schema import SuccessResponse
from app.schemas.user_schema import (
    UserWithProfileResponse,
    UpdateCustomerProfileRequest,
    CustomerAddressOut,
    CustomerAddressCreate,
    CustomerAddressUpdate,
    CustomerSettingsUpdate,
    CustomerSettingsResponse,
)
from app.dependencies import get_current_active_user, require_customer
from app.models.user_model import User
from app.crud.user_crud import user_crud
from app.crud.address_crud import address_crud
from app.services.upload_service import upload_service

router = APIRouter()


# ─── Profile ───────────────────────────────────────────────────────────────

@router.get("/profile", response_model=SuccessResponse[UserWithProfileResponse])
def get_user_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    user = user_crud.get_with_profile(db, user_id=current_user.id)
    return {"success": True, "data": UserWithProfileResponse.from_user(user)}


@router.put("/profile", response_model=SuccessResponse[UserWithProfileResponse])
def update_customer_profile(
    profile_in: UpdateCustomerProfileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
) -> dict:
    update_data = profile_in.model_dump(exclude_none=True)

    if "location" in update_data:
        loc = update_data.pop("location")
        update_data["latitude"]  = loc.get("latitude")
        update_data["longitude"] = loc.get("longitude")

    user_crud.update_customer_profile(db, user=current_user, update_data=update_data)
    updated_user = user_crud.get_with_profile(db, user_id=current_user.id)
    return {"success": True, "data": UserWithProfileResponse.from_user(updated_user)}


@router.post("/profile/avatar", response_model=SuccessResponse[dict])
def upload_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
) -> dict:
    if file.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only JPEG, PNG, or WebP images are accepted",
        )

    avatar_url = upload_service.upload_image(file, folder="avatars")

    profile = current_user.customer_profile
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    profile.profile_picture = avatar_url
    db.commit()

    return {"success": True, "data": {"avatar_url": avatar_url}}


# ─── Addresses ─────────────────────────────────────────────────────────────

@router.get("/addresses", response_model=SuccessResponse[List[CustomerAddressOut]])
def get_addresses(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
) -> dict:
    addresses = address_crud.get_by_user(db, user_id=current_user.id)
    return {"success": True, "data": addresses}


@router.post(
    "/addresses",
    response_model=SuccessResponse[CustomerAddressOut],
    status_code=status.HTTP_201_CREATED,
)
def add_address(
    payload: CustomerAddressCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
) -> dict:
    address = address_crud.create_for_user(db, user_id=current_user.id, obj_in=payload)
    return {"success": True, "data": address}


@router.put("/addresses/{address_id}", response_model=SuccessResponse[CustomerAddressOut])
def update_address(
    address_id: UUID,
    payload: CustomerAddressUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
) -> dict:
    address = address_crud.get_for_user(db, address_id=address_id, user_id=current_user.id)
    if not address:
        raise HTTPException(status_code=404, detail="Address not found")
    updated = address_crud.update(db, db_obj=address, obj_in=payload)
    return {"success": True, "data": updated}


@router.delete("/addresses/{address_id}", response_model=SuccessResponse[dict])
def delete_address(
    address_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
) -> dict:
    address = address_crud.get_for_user(db, address_id=address_id, user_id=current_user.id)
    if not address:
        raise HTTPException(status_code=404, detail="Address not found")
    address_crud.remove(db, id=address_id)
    return {"success": True, "data": {"message": "Address deleted"}}


@router.patch("/addresses/{address_id}/default", response_model=SuccessResponse[dict])
def set_default_address(
    address_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
) -> dict:
    address = address_crud.get_for_user(db, address_id=address_id, user_id=current_user.id)
    if not address:
        raise HTTPException(status_code=404, detail="Address not found")
    address_crud.set_default(db, user_id=current_user.id, address_id=address_id)
    return {"success": True, "data": {"message": "Default address updated"}}


# ─── Settings ──────────────────────────────────────────────────────────────

@router.put("/settings", response_model=SuccessResponse[CustomerSettingsResponse])
def update_settings(
    payload: CustomerSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
) -> dict:
    profile = current_user.customer_profile
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    existing = profile.settings or {}
    existing.update(payload.model_dump(exclude_none=True))
    profile.settings = existing
    db.commit()

    return {"success": True, "data": existing}