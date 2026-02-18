from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from app.core.database import get_db
from app.dependencies import get_current_active_user
from app.models.user import User
from app.models.subscription import SubscriptionPlanTypeEnum, BillingCycleEnum
from app.schemas.subscription import SubscriptionPlanOut, SubscriptionOut, SubscriptionCreate, SubscriptionCancelRequest
from app.schemas.common import SuccessResponse
from app.services.subscription_service import subscription_service
from app.crud.subscription import subscription_plan_crud
router = APIRouter()


@router.get("/plans", response_model=SuccessResponse[List[SubscriptionPlanOut]])
def get_subscription_plans(db: Session = Depends(get_db)):
    """Get all available subscription plans."""
    plans = subscription_service.get_available_plans(db)
    return {"success": True, "data": plans}


@router.get("/my-subscription", response_model=SuccessResponse[SubscriptionOut])
def get_my_subscription(
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """Get current user's active subscription."""
    try:
        subscription = subscription_service.get_user_subscription(db, user_id=user.id)
        return {"success": True, "data": subscription}
    except:
        return {"success": False, "error": {"message": "No active subscription"}}


@router.post("/subscribe", response_model=SuccessResponse[SubscriptionOut])
def subscribe_to_plan(
        payload: SubscriptionCreate,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """
    Subscribe to a plan.

    Request body should include:
    - plan_id: UUID of the subscription plan
    - billing_cycle: "monthly" or "annual"
    - payment_method: "wallet" or "card"
    """
    # Get plan to determine plan_type

    plan = subscription_plan_crud.get(db, id=payload.plan_id)
    if not plan:
        return {"success": False, "error": {"message": "Plan not found"}}

    subscription = subscription_service.subscribe(
        db,
        user_id=user.id,
        plan_type=plan.plan_type,
        billing_cycle=payload.billing_cycle,
        payment_method=payload.payment_method
    )
    return {"success": True, "data": subscription}


@router.post("/cancel", response_model=SuccessResponse[SubscriptionOut])
def cancel_subscription(
        payload: SubscriptionCancelRequest,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_active_user)
):
    """Cancel current subscription."""
    subscription = subscription_service.get_user_subscription(db, user_id=user.id)
    cancelled = subscription_service.cancel_subscription(
        db,
        subscription_id=subscription.id,
        user_id=user.id
    )
    return {"success": True, "data": cancelled}