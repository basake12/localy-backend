"""
Subscription management service.
"""
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from app.models.user import User
from app.models.subscription import SubscriptionPlan, Subscription, SubscriptionPlanTypeEnum, BillingCycleEnum
from app.crud.subscription import subscription_crud, subscription_plan_crud
from app.services.wallet_service import wallet_service
from app.services.payment_service import payment_service
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    InsufficientFundsException
)
from app.core.utils import generate_reference_code


class SubscriptionService:
    """Subscription business logic."""

    def get_available_plans(self, db: Session) -> list[SubscriptionPlan]:
        """Get all available subscription plans."""
        return subscription_plan_crud.get_all_active(db)

    def get_user_subscription(self, db: Session, *, user_id: UUID) -> Subscription:
        """Get user's current subscription."""
        subscription = subscription_crud.get_active_subscription(db, user_id=user_id)
        if not subscription:
            raise NotFoundException("No active subscription found")
        return subscription

    def subscribe(
        self,
        db: Session,
        *,
        user_id: UUID,
        plan_type: SubscriptionPlanTypeEnum,
        billing_cycle: BillingCycleEnum,
        payment_method: str = "wallet"
    ) -> Subscription:
        """
        Subscribe user to a plan.

        Args:
            user_id: User ID
            plan_type: Plan type (free, starter, pro, enterprise, pro_driver)
            billing_cycle: Billing cycle (monthly, annual)
            payment_method: Payment method (wallet, card)

        Returns:
            Created subscription
        """
        # Get plan
        plan = subscription_plan_crud.get_by_type(db, plan_type=plan_type)
        if not plan:
            raise NotFoundException("Subscription plan not found")

        # Check if user already has active subscription
        existing = subscription_crud.get_active_subscription(db, user_id=user_id)
        if existing:
            raise ValidationException("User already has an active subscription")

        # Determine price based on billing cycle
        if billing_cycle == BillingCycleEnum.MONTHLY:
            price = plan.monthly_price
        else:  # ANNUAL
            price = plan.annual_price

        # Process payment if not free plan
        if price > 0:
            if payment_method == "wallet":
                # Debit wallet
                try:
                    wallet_service.debit_wallet(
                        db,
                        user_id=user_id,
                        amount=price,
                        description=f"Subscription to {plan.name} ({billing_cycle.value})",
                        reference=generate_reference_code("SUB"),
                        metadata={
                            "plan_id": str(plan.id),
                            "billing_cycle": billing_cycle.value
                        }
                    )
                except InsufficientFundsException:
                    raise ValidationException("Insufficient wallet balance")
            elif payment_method == "card":
                # Initialize Paystack payment
                # This would return payment URL for frontend
                pass

        # Create subscription
        subscription = subscription_crud.create_subscription(
            db,
            user_id=user_id,
            plan=plan,
            billing_cycle=billing_cycle
        )

        return subscription

    def cancel_subscription(
        self,
        db: Session,
        *,
        subscription_id: UUID,
        user_id: UUID
    ) -> Subscription:
        """Cancel a subscription."""
        subscription = subscription_crud.get(db, id=subscription_id)
        if not subscription:
            raise NotFoundException("Subscription not found")

        # Verify ownership
        if subscription.user_id != user_id:
            raise ValidationException("Not your subscription")

        # Cancel
        return subscription_crud.cancel_subscription(db, subscription_id=subscription_id)

    def renew_subscription(
        self,
        db: Session,
        *,
        subscription_id: UUID,
        payment_method: str = "wallet"
    ) -> Subscription:
        """Manually renew a subscription."""
        subscription = subscription_crud.get(db, id=subscription_id)
        if not subscription:
            raise NotFoundException("Subscription not found")

        # Get plan
        plan = subscription_plan_crud.get(db, id=subscription.plan_id)

        # Determine price
        if subscription.billing_cycle == BillingCycleEnum.MONTHLY:
            price = plan.monthly_price
        else:
            price = plan.annual_price

        # Process payment
        if price > 0:
            if payment_method == "wallet":
                wallet_service.debit_wallet(
                    db,
                    user_id=subscription.user_id,
                    amount=price,
                    description=f"Subscription renewal: {plan.name} ({subscription.billing_cycle.value})",
                    reference=generate_reference_code("RENEW")
                )

        # Renew subscription
        return subscription_crud.renew_subscription(db, subscription=subscription, plan=plan)

    def auto_renew_subscriptions(self, db: Session) -> int:
        """Auto-renew subscriptions expiring soon. Returns count of renewed."""
        # Get subscriptions expiring in next 24 hours
        expiring = subscription_crud.get_expiring_soon(db, days=1)

        renewed_count = 0
        for subscription in expiring:
            try:
                # Attempt auto-renewal
                plan = subscription_plan_crud.get(db, id=subscription.plan_id)

                # Determine price
                if subscription.billing_cycle == BillingCycleEnum.MONTHLY:
                    price = plan.monthly_price
                else:
                    price = plan.annual_price

                # Try to debit wallet
                wallet_service.debit_wallet(
                    db,
                    user_id=subscription.user_id,
                    amount=price,
                    description=f"Auto-renewal: {plan.name} ({subscription.billing_cycle.value})",
                    reference=generate_reference_code("AUTORENEW")
                )

                # Renew subscription
                subscription_crud.renew_subscription(db, subscription=subscription, plan=plan)
                renewed_count += 1

            except Exception as e:
                # Log error but continue with other subscriptions
                print(f"Auto-renewal failed for subscription {subscription.id}: {e}")
                # Optionally send notification to user
                continue

        return renewed_count

    def check_and_expire_subscriptions(self, db: Session) -> int:
        """Mark expired subscriptions. Returns count."""
        return subscription_crud.mark_expired(db)


# Singleton instance
subscription_service = SubscriptionService()