from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_current_active_user
from app.models.user import User
from app.schemas.business import BusinessOut, BusinessUpdate, BusinessListOut
from app.schemas.common import SuccessResponse
from app.crud.business import business_crud

router = APIRouter()


@router.get("/my-business", response_model=SuccessResponse[BusinessOut])
def get_my_business(
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """Get current user's business profile."""
    business = business_crud.get_by_user_id(db, user_id=user.id)
    if not business:
        return {"success": False, "error": {"message": "No business profile found"}}

    return {"success": True, "data": business}


@router.put("/my-business", response_model=SuccessResponse[BusinessOut])
def update_my_business(
        payload: BusinessUpdate,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """Update current user's business profile."""
    business = business_crud.get_by_user_id(db, user_id=user.id)
    if not business:
        return {"success": False, "error": {"message": "No business profile found"}}

    updated = business_crud.update(db, db_obj=business, obj_in=payload)
    return {"success": True, "data": updated}


@router.get("", response_model=SuccessResponse[BusinessListOut])
def list_businesses(
        category: str = Query(None),
        search: str = Query(None),
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        db: Session = Depends(get_db)
):
    """List all businesses with filters."""
    businesses, total = business_crud.search_businesses(
        db,
        category=category,
        search_query=search,
        skip=skip,
        limit=limit
    )

    return {
        "success": True,
        "data": {
            "businesses": businesses,
            "total": total,
            "page": skip // limit + 1,
            "page_size": limit
        }
    }






