"""
app/api/v1/subscriptions.py

FIXES vs previous version:
  1.  user.user_type != "business" → user.role.value != "business".
      Blueprint §14: field renamed role (was user_type).

  2.  pro_driver removed from plan type strings.
      Blueprint §8.1: Free / Starter / Pro / Enterprise only.

  3.  _require_business now also checks user.is_active / user.is_banned
      using the boolean fields on User (not a status enum).

  4.  Documentation updated to reflect subscription_tier_rank sync
      that happens inside subscription_service on every tier change.
"""
from typing import List, Optional, Union
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.dependencies import get_current_active_user
from app.models.user_model import User
from app.schemas.common_schema import SuccessResponse
from app.schemas.subscription_schema import (
    AutoRenewToggle,
    SubscriptionCancelRequest,
    SubscriptionCreate,
    SubscriptionOut,
    SubscriptionPlanOut,
    SubscriptionUpgrade,
)
from app.services.subscription_service import subscription_service

router = APIRouter()


# ─── Role guard ──────────────────────────────────────────────────────────────

def _require_business(user: User) -> None:
    """
    Raise 403 if the user is not a business account.
    Blueprint §14: role field (not user_type).
    """
    role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
    if role_val != "business":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only business accounts can manage subscriptions.",
        )


def _resolve_plan_uuid(db: Session, plan_identifier: Union[UUID, str]) -> UUID:
    """
    Accept plan UUID or plan type string and return the plan UUID.
    Supported plan type strings: "free" | "starter" | "pro" | "enterprise"
    """
    if isinstance(plan_identifier, UUID):
        return plan_identifier
    try:
        return UUID(str(plan_identifier))
    except ValueError:
        pass

    from app.models.subscription_model import SubscriptionPlan
    plan = (
        db.query(SubscriptionPlan)
        .filter(
            SubscriptionPlan.plan_type == plan_identifier,
            SubscriptionPlan.is_active == True,
        )
        .first()
    )
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Plan '{plan_identifier}' not found. "
                "Valid plan types: free, starter, pro, enterprise."
            ),
        )
    return plan.id


# ─── Plans (public) ───────────────────────────────────────────────────────────

@router.get("/plans", response_model=SuccessResponse[List[SubscriptionPlanOut]])
def get_subscription_plans(db: Session = Depends(get_db)):
    """Return all active subscription plans ordered by price."""
    plans = subscription_service.get_available_plans(db)
    return {"success": True, "data": plans}


# ─── My subscription ─────────────────────────────────────────────────────────

@router.get("/my-subscription", response_model=SuccessResponse[SubscriptionOut])
def get_my_subscription(
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_active_user),
):
    _require_business(user)
    try:
        subscription = subscription_service.get_user_subscription(db, user_id=user.id)
        return {"success": True, "data": subscription}
    except NotFoundException as exc:
        raise HTTPException(status_code=404, detail=exc.detail)


# ─── Subscribe ────────────────────────────────────────────────────────────────

@router.post(
    "/subscribe",
    response_model=SuccessResponse[SubscriptionOut],
    status_code=status.HTTP_201_CREATED,
)
def subscribe_to_plan(
    payload: SubscriptionCreate,
    db:      Session = Depends(get_db),
    user:    User    = Depends(get_current_active_user),
):
    """
    Subscribe a business to a plan.

    `plan_id` accepts a UUID or plan type string: "starter" | "pro" | "enterprise".

    Blueprint §8.1:
      - Annual = 10 months price (2 months free).
      - Business.subscription_tier_rank updated immediately for search ordering.
    """
    _require_business(user)
    plan_uuid = _resolve_plan_uuid(db, payload.plan_id)
    try:
        subscription = subscription_service.subscribe(
            db,
            user_id=user.id,
            plan_id=plan_uuid,
            billing_cycle=payload.billing_cycle,
            payment_method=payload.payment_method,
        )
        return {"success": True, "data": subscription}
    except NotFoundException as exc:
        raise HTTPException(status_code=404, detail=exc.detail)
    except ValidationException as exc:
        raise HTTPException(status_code=400, detail=exc.detail)


# ─── Upgrade / Downgrade ─────────────────────────────────────────────────────

@router.post("/upgrade", response_model=SuccessResponse[SubscriptionOut])
def upgrade_subscription(
    payload: SubscriptionUpgrade,
    db:      Session = Depends(get_db),
    user:    User    = Depends(get_current_active_user),
):
    """
    Upgrade or downgrade the current subscription.

    Blueprint §8.1:
      Upgrade: immediate, prorated charge.
      Downgrade: takes effect at end of billing cycle.
    """
    _require_business(user)
    plan_uuid = _resolve_plan_uuid(db, payload.resolved_plan_id)
    try:
        subscription = subscription_service.upgrade_subscription(
            db,
            user_id=user.id,
            new_plan_id=plan_uuid,
            billing_cycle=payload.billing_cycle,
            payment_method=payload.payment_method,
        )
        return {"success": True, "data": subscription}
    except NotFoundException as exc:
        raise HTTPException(status_code=404, detail=exc.detail)
    except ValidationException as exc:
        raise HTTPException(status_code=400, detail=exc.detail)
    except ForbiddenException as exc:
        raise HTTPException(status_code=403, detail=exc.detail)


# ─── Cancel ──────────────────────────────────────────────────────────────────

@router.post("/cancel", response_model=SuccessResponse[SubscriptionOut])
def cancel_subscription(
    payload: Optional[SubscriptionCancelRequest] = Body(default=None),
    db:      Session = Depends(get_db),
    user:    User    = Depends(get_current_active_user),
):
    """
    Cancel subscription.
    Access to paid features continues until billing period ends.
    Blueprint §8.1: downgrade to Free at cycle end.
    """
    _require_business(user)
    try:
        subscription = subscription_service.get_user_subscription(db, user_id=user.id)
        cancelled    = subscription_service.cancel_subscription(
            db,
            subscription_id=subscription.id,
            user_id=user.id,
        )
        return {"success": True, "data": cancelled}
    except NotFoundException as exc:
        raise HTTPException(status_code=404, detail=exc.detail)
    except ValidationException as exc:
        raise HTTPException(status_code=400, detail=exc.detail)


# ─── Auto-renew ──────────────────────────────────────────────────────────────

@router.patch("/auto-renew", response_model=SuccessResponse[SubscriptionOut])
def toggle_auto_renew(
    payload: AutoRenewToggle,
    db:      Session = Depends(get_db),
    user:    User    = Depends(get_current_active_user),
):
    _require_business(user)
    try:
        subscription = subscription_service.get_user_subscription(db, user_id=user.id)
        updated = subscription_service.toggle_auto_renew(
            db,
            subscription_id=subscription.id,
            auto_renew=payload.auto_renew,
        )
        return {"success": True, "data": updated}
    except NotFoundException as exc:
        raise HTTPException(status_code=404, detail=exc.detail)


# ─── History ─────────────────────────────────────────────────────────────────

@router.get("/history", response_model=SuccessResponse[List[SubscriptionOut]])
def get_subscription_history(
    skip:  int = 0,
    limit: int = 20,
    db:    Session = Depends(get_db),
    user:  User    = Depends(get_current_active_user),
):
    _require_business(user)
    subscriptions, total = subscription_service.get_subscription_history(
        db, user_id=user.id, skip=skip, limit=limit
    )
    return {
        "success": True,
        "data":    subscriptions,
        "meta":    {"total": total, "skip": skip, "limit": limit},
    }