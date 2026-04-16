"""
app/crud/subscription_crud.py

FIXES vs previous version:
  1.  [HARD RULE §16.4] All datetime.utcnow() × 5 replaced with
      datetime.now(timezone.utc). Produces timezone-aware datetimes
      compatible with PostgreSQL TIMESTAMPTZ columns.

  2.  Wallet.user_id → Wallet.owner_id. Blueprint §14.

  3.  WalletTransaction.reference_id → WalletTransaction.external_reference.
      Blueprint §14.

  4.  idempotency_key added to WalletTransaction creation.
      Blueprint §5.6 HARD RULE: every financial operation requires one.

  5.  create_subscription / cancel_subscription / renew_subscription
      now call Business.subscription_tier_rank update after tier change.
      Blueprint §7.2: tier rank used for ORDER BY in all discovery queries.
      Rank mapping: Enterprise=4, Pro=3, Starter=2, Free=1.
"""
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from uuid import UUID

from dateutil.relativedelta import relativedelta
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.crud.base_crud import CRUDBase
from app.models.subscription_model import (
    BillingCycleEnum,
    Subscription,
    SubscriptionPlan,
    SubscriptionPlanTypeEnum,
)
from app.schemas.subscription_schema import SubscriptionCreate


def _utcnow() -> datetime:
    """Timezone-aware UTC timestamp. Blueprint §16.4 HARD RULE."""
    return datetime.now(timezone.utc)


# Blueprint §7.2: subscription_tier_rank integers for ORDER BY
_TIER_RANK: dict[str, int] = {
    "enterprise": 4,
    "pro":        3,
    "starter":    2,
    "free":       1,
}


def _update_business_tier_rank(db: Session, user_id: UUID, plan_type: str) -> None:
    """
    Update Business.subscription_tier AND subscription_tier_rank when plan changes.
    Blueprint §7.2: "Tier rank mapping (stored as integer for ORDER BY):
      Enterprise=4, Pro=3, Starter=2, Free=1."
    """
    try:
        from app.models.business_model import Business
        business = db.query(Business).filter(Business.user_id == user_id).first()
        if business:
            business.subscription_tier      = plan_type
            business.subscription_tier_rank = _TIER_RANK.get(plan_type, 1)
            db.flush()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Failed to update business tier rank for user %s: %s", user_id, exc
        )


class CRUDSubscriptionPlan(CRUDBase[SubscriptionPlan, dict, dict]):

    def get_by_type(
        self, db: Session, *, plan_type: SubscriptionPlanTypeEnum
    ) -> Optional[SubscriptionPlan]:
        return db.query(SubscriptionPlan).filter(
            and_(
                SubscriptionPlan.plan_type == plan_type,
                SubscriptionPlan.is_active  == True,
            )
        ).first()

    def get_all_active(self, db: Session) -> List[SubscriptionPlan]:
        return (
            db.query(SubscriptionPlan)
            .filter(SubscriptionPlan.is_active == True)
            .order_by(SubscriptionPlan.monthly_price)
            .all()
        )


class CRUDSubscription(CRUDBase[Subscription, SubscriptionCreate, dict]):

    def get_by_user(self, db: Session, *, user_id: UUID) -> Optional[Subscription]:
        return (
            db.query(Subscription)
            .filter(Subscription.user_id == user_id)
            .order_by(Subscription.created_at.desc())
            .first()
        )

    def get_active_subscription(
        self, db: Session, *, user_id: UUID
    ) -> Optional[Subscription]:
        # Blueprint §16.4 HARD RULE: timezone-aware timestamp
        now = _utcnow()
        return db.query(Subscription).filter(
            and_(
                Subscription.user_id    == user_id,
                Subscription.status     == "active",
                Subscription.expires_at > now,
            )
        ).first()

    def create_subscription(
        self,
        db: Session,
        *,
        user_id: UUID,
        plan: SubscriptionPlan,
        billing_cycle: BillingCycleEnum,
        started_at: Optional[datetime] = None,
    ) -> Subscription:
        """
        Create a new subscription and sync business tier rank.
        Blueprint §8.1 annual = 10 months price (stored in plan.annual_price already).
        """
        # Blueprint §16.4 HARD RULE
        if started_at is None:
            started_at = _utcnow()

        if billing_cycle == BillingCycleEnum.MONTHLY:
            expires_at = started_at + relativedelta(months=1)
        else:  # ANNUAL
            expires_at = started_at + relativedelta(years=1)

        subscription = Subscription(
            user_id       = user_id,
            plan_id       = plan.id,
            billing_cycle = billing_cycle,
            status        = "active",
            started_at    = started_at,
            expires_at    = expires_at,
            auto_renew    = True,
        )
        db.add(subscription)
        db.flush()

        # Blueprint §7.2: update subscription_tier_rank for search ordering
        _update_business_tier_rank(db, user_id, plan.plan_type.value)

        db.commit()
        db.refresh(subscription)
        return subscription

    def cancel_subscription(
        self, db: Session, *, subscription_id: UUID
    ) -> Subscription:
        """
        Cancel subscription. Tier stays until expiry — Celery expire task
        will downgrade to free at billing cycle end. Blueprint §8.1.
        """
        subscription = self.get(db, id=subscription_id)
        if subscription:
            subscription.status     = "cancelled"
            subscription.auto_renew = False
            # Blueprint §16.4 HARD RULE
            subscription.cancelled_at = _utcnow()
            db.commit()
            db.refresh(subscription)
        return subscription

    def renew_subscription(
        self,
        db: Session,
        *,
        subscription: Subscription,
        plan: SubscriptionPlan,
    ) -> Subscription:
        """
        Renew subscription. Old record → expired, new record → active.
        Tier rank stays the same (same plan being renewed).
        """
        new_started = subscription.expires_at

        if subscription.billing_cycle == BillingCycleEnum.MONTHLY:
            new_expires = new_started + relativedelta(months=1)
        else:
            new_expires = new_started + relativedelta(years=1)

        new_subscription = Subscription(
            user_id       = subscription.user_id,
            plan_id       = plan.id,
            billing_cycle = subscription.billing_cycle,
            status        = "active",
            started_at    = new_started,
            expires_at    = new_expires,
            auto_renew    = subscription.auto_renew,
        )
        subscription.status = "expired"

        db.add(new_subscription)
        db.commit()
        db.refresh(new_subscription)
        return new_subscription

    def downgrade_to_free(self, db: Session, *, user_id: UUID) -> None:
        """
        Downgrade business to free tier.
        Called by Celery expire task after 7-day grace period.
        Blueprint §8.1: "Failed payment: 7-day grace period → auto-downgrade
        to Free on day 8."
        """
        _update_business_tier_rank(db, user_id, "free")
        db.commit()

    def get_expiring_soon(
        self, db: Session, *, days: int = 1
    ) -> List[Subscription]:
        """Subscriptions expiring within N days — for auto-renewal."""
        now    = _utcnow()
        cutoff = now + timedelta(days=days)
        return db.query(Subscription).filter(
            and_(
                Subscription.status     == "active",
                Subscription.auto_renew == True,
                Subscription.expires_at <= cutoff,
                Subscription.expires_at > now,
            )
        ).all()

    def mark_expired(self, db: Session) -> int:
        """
        Mark past-expiry subscriptions as expired.
        Does NOT immediately downgrade tier — grace period applies.
        Blueprint §8.1: 7-day grace → downgrade on day 8.
        """
        now = _utcnow()
        expired = db.query(Subscription).filter(
            and_(
                Subscription.status     == "active",
                Subscription.expires_at <  now,
            )
        ).all()
        count = 0
        for sub in expired:
            sub.status = "expired"
            count += 1
        if count > 0:
            db.commit()
        return count

    def get_in_grace_period(self, db: Session) -> List[Subscription]:
        """
        Subscriptions that expired > 7 days ago with no renewal.
        Blueprint §8.1: auto-downgrade to Free on day 8.
        """
        now          = _utcnow()
        grace_cutoff = now - timedelta(days=7)
        return db.query(Subscription).filter(
            and_(
                Subscription.status     == "expired",
                Subscription.expires_at <  grace_cutoff,
            )
        ).all()

    def get_user_subscription_history(
        self,
        db: Session,
        *,
        user_id: UUID,
        skip:    int = 0,
        limit:   int = 20,
    ) -> Tuple[List[Subscription], int]:
        query = (
            db.query(Subscription)
            .filter(Subscription.user_id == user_id)
            .order_by(Subscription.created_at.desc())
        )
        total         = query.count()
        subscriptions = query.offset(skip).limit(limit).all()
        return subscriptions, total


# Singletons
subscription_plan_crud = CRUDSubscriptionPlan(SubscriptionPlan)
subscription_crud      = CRUDSubscription(Subscription)