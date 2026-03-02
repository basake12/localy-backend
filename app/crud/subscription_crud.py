from sqlalchemy.orm import Session
from sqlalchemy import and_
from typing import Optional, List, Tuple
from uuid import UUID
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

from app.crud.base_crud import CRUDBase
from app.models.subscription_model import Subscription, SubscriptionPlan, SubscriptionPlanTypeEnum, BillingCycleEnum
from app.schemas.subscription_schema import SubscriptionCreate


class CRUDSubscriptionPlan(CRUDBase[SubscriptionPlan, dict, dict]):
    """CRUD operations for subscription plans."""

    def get_by_type(self, db: Session, *, plan_type: SubscriptionPlanTypeEnum) -> Optional[SubscriptionPlan]:
        """Get plan by type."""
        return db.query(SubscriptionPlan).filter(
            and_(
                SubscriptionPlan.plan_type == plan_type,
                SubscriptionPlan.is_active == True
            )
        ).first()

    def get_all_active(self, db: Session) -> List[SubscriptionPlan]:
        """Get all active plans."""
        return db.query(SubscriptionPlan).filter(
            SubscriptionPlan.is_active == True
        ).order_by(SubscriptionPlan.monthly_price).all()


class CRUDSubscription(CRUDBase[Subscription, SubscriptionCreate, dict]):
    """CRUD operations for subscriptions."""

    def get_by_user(self, db: Session, *, user_id: UUID) -> Optional[Subscription]:
        """Get user's current subscription."""
        return db.query(Subscription).filter(
            Subscription.user_id == user_id
        ).order_by(Subscription.created_at.desc()).first()

    def get_active_subscription(self, db: Session, *, user_id: UUID) -> Optional[Subscription]:
        """Get user's active subscription."""
        now = datetime.utcnow()
        return db.query(Subscription).filter(
            and_(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.expires_at > now
            )
        ).first()

    def create_subscription(
            self,
            db: Session,
            *,
            user_id: UUID,
            plan: SubscriptionPlan,
            billing_cycle: BillingCycleEnum,
            started_at: Optional[datetime] = None
    ) -> Subscription:
        """Create a new subscription."""
        if started_at is None:
            started_at = datetime.utcnow()

        # Calculate expiration based on billing cycle
        if billing_cycle == BillingCycleEnum.MONTHLY:
            expires_at = started_at + relativedelta(months=1)
        else:  # ANNUAL
            expires_at = started_at + relativedelta(years=1)

        subscription = Subscription(
            user_id=user_id,
            plan_id=plan.id,
            billing_cycle=billing_cycle,
            status="active",
            started_at=started_at,
            expires_at=expires_at,
            auto_renew=True
        )

        db.add(subscription)
        db.commit()
        db.refresh(subscription)
        return subscription

    def cancel_subscription(
            self,
            db: Session,
            *,
            subscription_id: UUID
    ) -> Subscription:
        """Cancel a subscription."""
        subscription = self.get(db, id=subscription_id)
        if subscription:
            subscription.status = "cancelled"
            subscription.auto_renew = False
            subscription.cancelled_at = datetime.utcnow()
            db.commit()
            db.refresh(subscription)
        return subscription

    def renew_subscription(
            self,
            db: Session,
            *,
            subscription: Subscription,
            plan: SubscriptionPlan
    ) -> Subscription:
        """Renew an existing subscription."""
        # Create new subscription starting when current one expires
        new_started = subscription.expires_at

        if subscription.billing_cycle == BillingCycleEnum.MONTHLY:
            new_expires = new_started + relativedelta(months=1)
        else:  # ANNUAL
            new_expires = new_started + relativedelta(years=1)

        new_subscription = Subscription(
            user_id=subscription.user_id,
            plan_id=plan.id,
            billing_cycle=subscription.billing_cycle,
            status="active",
            started_at=new_started,
            expires_at=new_expires,
            auto_renew=subscription.auto_renew
        )

        # Mark old subscription as expired
        subscription.status = "expired"

        db.add(new_subscription)
        db.commit()
        db.refresh(new_subscription)
        return new_subscription

    def get_expiring_soon(
            self,
            db: Session,
            *,
            days: int = 3
    ) -> List[Subscription]:
        """Get subscriptions expiring within N days."""
        now = datetime.utcnow()
        expiry_date = now + timedelta(days=days)

        return db.query(Subscription).filter(
            and_(
                Subscription.status == "active",
                Subscription.auto_renew == True,
                Subscription.expires_at <= expiry_date,
                Subscription.expires_at > now
            )
        ).all()

    def mark_expired(self, db: Session) -> int:
        """Mark expired subscriptions. Returns count of updated subscriptions."""
        now = datetime.utcnow()

        expired = db.query(Subscription).filter(
            and_(
                Subscription.status == "active",
                Subscription.expires_at < now
            )
        ).all()

        count = 0
        for sub in expired:
            sub.status = "expired"
            count += 1

        if count > 0:
            db.commit()

        return count

    def get_user_subscription_history(
            self,
            db: Session,
            *,
            user_id: UUID,
            skip: int = 0,
            limit: int = 20
    ) -> Tuple[List[Subscription], int]:
        """Get user's subscription history."""
        query = db.query(Subscription).filter(
            Subscription.user_id == user_id
        ).order_by(Subscription.created_at.desc())

        total = query.count()
        subscriptions = query.offset(skip).limit(limit).all()

        return subscriptions, total


# Singleton instances
subscription_plan_crud = CRUDSubscriptionPlan(SubscriptionPlan)
subscription_crud = CRUDSubscription(Subscription)