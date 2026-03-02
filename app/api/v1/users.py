from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.common_schema import SuccessResponse
from app.schemas.user_schema import UserWithProfileResponse, UpdateCustomerProfileRequest
from app.dependencies import get_current_active_user, require_customer
from app.models.user_model import User
from app.crud.user_crud import user_crud

router = APIRouter()


@router.get("/profile", response_model=SuccessResponse[UserWithProfileResponse])
def get_user_profile(
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_active_user)
) -> dict:
    """Get current user profile with all details"""
    user = user_crud.get_with_profile(db, user_id=current_user.id)

    return {
        "success": True,
        "data": user
    }


@router.put("/profile", response_model=SuccessResponse[dict])
def update_customer_profile(
        *,
        db: Session = Depends(get_db),
        profile_in: UpdateCustomerProfileRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """Update customer profile"""
    # TODO: Implement profile update logic

    return {
        "success": True,
        "data": {
            "message": "Profile updated successfully"
        }
    }