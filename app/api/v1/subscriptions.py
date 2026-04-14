from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session
from typing import List, Union, Optional
from uuid import UUID

from app.core.database import get_db
from app.dependencies import get_current_active_user
from app.models.user_model import User
from app.schemas.subscription_schema import (
    SubscriptionPlanOut,
    SubscriptionOut,
    SubscriptionCreate,
    SubscriptionUpgrade,
    SubscriptionCancelRequest,
    AutoRenewToggle,
)
from app.schemas.common_schema import SuccessResponse, PaginatedResponse
from app.services.subscription_service import subscription_service
from app.core.exceptions import NotFoundException, ValidationException, ForbiddenException

router = APIRouter()


def _require_business(user: User) -> None:
    """Raise 403 if the user is not a business account."""
    if user.user_type != "business":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only business accounts can manage subscriptions",
        )


def _resolve_plan_uuid(db: Session, plan_identifier: Union[UUID, str]) -> UUID:
    """
    Accept either a plan UUID or a plan type string and return the plan UUID.

    Supported plan type strings: "free", "starter", "pro", "enterprise", "pro_driver"

    Examples:
        "starter"                              → looks up SubscriptionPlan by plan_type
        "48b5d6e4-82b1-4eb7-86fb-0ffd2dc3878e" → used directly as UUID

    Raises HTTP 404 if the plan type string doesn't match any active plan.
    """
    # Already a UUID — return as-is
    if isinstance(plan_identifier, UUID):
        return plan_identifier

    # Try to parse as UUID string first
    try:
        return UUID(str(plan_identifier))
    except ValueError:
        pass

    # It's a plan type string — resolve to UUID via DB lookup
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
                f"Valid plan types: free, starter, pro, enterprise, pro_driver."
            ),
        )
    return plan.id


# ─── Plans (public — no auth required) ────────────────────────────────────

@router.get("/plans", response_model=SuccessResponse[List[SubscriptionPlanOut]])
def get_subscription_plans(db: Session = Depends(get_db)):
    """Return all active subscription plans ordered by price."""
    plans = subscription_service.get_available_plans(db)
    return {"success": True, "data": plans}


# ─── My subscription ────────────────────────────────────────────────────────

@router.get("/my-subscription", response_model=SuccessResponse[SubscriptionOut])
def get_my_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Return the authenticated business user's current active subscription."""
    _require_business(user)
    try:
        subscription = subscription_service.get_user_subscription(db, user_id=user.id)
        return {"success": True, "data": subscription}
    except NotFoundException as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.detail)


# ─── Subscribe ──────────────────────────────────────────────────────────────

@router.post(
    "/subscribe",
    response_model=SuccessResponse[SubscriptionOut],
    status_code=status.HTTP_201_CREATED,
)
def subscribe_to_plan(
    payload: SubscriptionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """
    Subscribe a business to a plan.

    - **plan_id**: UUID of the target plan OR plan type string
      (e.g. `"starter"`, `"pro"`, `"enterprise"`)
    - **billing_cycle**: `monthly` or `annual`
    - **payment_method**: `wallet` (default)
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.detail)
    except ValidationException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail)


# ─── Upgrade / downgrade ───────────────────────────────────────────────────

@router.post("/upgrade", response_model=SuccessResponse[SubscriptionOut])
def upgrade_subscription(
    payload: SubscriptionUpgrade,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """
    Upgrade or downgrade the current subscription.

    Accepts either field name for the plan:
      - `plan_id`     — same as subscribe endpoint
      - `new_plan_id` — original name, kept for backwards compatibility

    Both accept a UUID or plan type string (`"starter"`, `"pro"`, `"enterprise"`).

    Per Blueprint:
    - Upgrade: immediate, prorated charge applied.
    - Downgrade: takes effect at end of billing cycle.
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.detail)
    except ValidationException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail)
    except ForbiddenException as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail)


# ─── Cancel ─────────────────────────────────────────────────────────────────

@router.post("/cancel", response_model=SuccessResponse[SubscriptionOut])
def cancel_subscription(
    # Body is fully optional — POST /cancel with no body is valid.
    # reason is informational only; omitting it is fine.
    payload: Optional[SubscriptionCancelRequest] = Body(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """
    Cancel the current subscription.
    Access to paid features continues until the billing period ends.
    """
    _require_business(user)
    try:
        subscription = subscription_service.get_user_subscription(db, user_id=user.id)
        cancelled = subscription_service.cancel_subscription(
            db,
            subscription_id=subscription.id,
            user_id=user.id,
        )
        return {"success": True, "data": cancelled}
    except NotFoundException as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.detail)
    except ValidationException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail)


# ─── Toggle auto-renew ──────────────────────────────────────────────────────

@router.patch("/auto-renew", response_model=SuccessResponse[SubscriptionOut])
def toggle_auto_renew(
    payload: AutoRenewToggle,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Enable or disable automatic renewal for the current subscription."""
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.detail)


# ─── History ─────────────────────────────────────────────────────────────────

@router.get("/history", response_model=SuccessResponse[List[SubscriptionOut]])
def get_subscription_history(
    skip: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Return paginated subscription history for the authenticated business."""
    _require_business(user)
    subscriptions, total = subscription_service.get_subscription_history(
        db, user_id=user.id, skip=skip, limit=limit
    )
    return {
        "success": True,
        "data": subscriptions,
        "meta": {"total": total, "skip": skip, "limit": limit},
    }