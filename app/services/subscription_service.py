"""
Subscription management service.

FIX: _sync_business_tier previously called business_crud.get_by_user() which
doesn't exist on CRUDBusiness. CRUDBusiness.get_by_user_id() is async and
cannot be awaited from this sync service. Fixed to use a direct sync
db.query(Business) call — consistent with _debit_wallet_sync which already
uses db.query() for the same reason.
"""
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from uuid import UUID
from decimal import Decimal

from app.models.subscription_model import (
    SubscriptionPlan,
    Subscription,
    BillingCycleEnum,
    SubscriptionPlanTypeEnum,
)
from app.models.business_model import Business
from app.crud.subscription_crud import subscription_crud, subscription_plan_crud
from app.models.wallet_model import Wallet, WalletTransaction, TransactionType, TransactionStatus
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    PermissionDeniedException,   # FIX: was ForbiddenException — doesn't exist in codebase
    InsufficientBalanceException, # FIX: was InsufficientFundsException — doesn't exist in codebase
)
from app.core.utils import generate_reference_code

logger = logging.getLogger(__name__)

# Tier ranks for upgrade/downgrade logic
_TIER_RANK: dict[str, int] = {
    "free": 0,
    "starter": 1,
    "pro": 2,
    "enterprise": 3,
    "pro_driver": 1,
}


class SubscriptionService:
    """Subscription business logic."""

    # ─── Helpers ──────────────────────────────────────────────────────────

    # Blueprint §8.1 plan comparison table — tier → badge
    _TIER_TO_BADGE: dict[str, str] = {
        "free":       "none",
        "starter":    "starter",
        "pro":        "pro",
        "enterprise": "enterprise",
        "pro_driver": "none",
    }

    def _sync_business_tier(self, db: Session, user_id: UUID, plan_type: str) -> None:
        """
        Update business.subscription_tier AND verification_badge to match the active plan.

        Per Blueprint §8.1:
          Free       -> no badge
          Starter    -> light blue badge
          Pro        -> Gold Pro badge
          Enterprise -> Platinum animated badge
        """
        from app.models.business_model import VerificationBadgeEnum
        try:
            business = (
                db.query(Business)
                .filter(Business.user_id == user_id)
                .first()
            )
            if business:
                business.subscription_tier  = plan_type
                badge_value                 = self._TIER_TO_BADGE.get(plan_type, "none")
                business.verification_badge = VerificationBadgeEnum(badge_value)
                db.commit()
                logger.info(
                    "Synced subscription_tier=%s verification_badge=%s for user=%s",
                    plan_type, badge_value, user_id,
                )
        except Exception:
            logger.exception(
                "Failed to sync business subscription_tier for user %s", user_id
            )

    def _debit_wallet_sync(
        self,
        db: Session,
        *,
        user_id: UUID,
        amount_ngn: Decimal,
        description: str,
        reference: str | None = None,
        metadata: dict | None = None,
    ) -> WalletTransaction:
        """
        Sync wallet debit for subscription charges.

        Mirrors wallet_service.debit_wallet() but uses the sync Session so
        subscription_service stays fully synchronous. The async WalletService
        uses AsyncSession and cannot be called from sync context.
        """
        wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()
        if not wallet:
            raise NotFoundException("Wallet")

        # Idempotency — don't double-charge the same reference
        if reference:
            existing = (
                db.query(WalletTransaction)
                .filter(WalletTransaction.reference_id == reference)
                .first()
            )
            if existing:
                logger.info("Duplicate subscription charge skipped: %s", reference)
                return existing

        if wallet.balance < amount_ngn:
            raise InsufficientBalanceException()

        balance_before = wallet.balance
        wallet.balance -= amount_ngn
        balance_after  = wallet.balance

        txn = WalletTransaction(
            wallet_id=wallet.id,
            amount=amount_ngn,
            transaction_type=TransactionType.PAYMENT,
            status=TransactionStatus.COMPLETED,
            balance_before=balance_before,
            balance_after=balance_after,
            description=description,
            reference_id=reference,
            meta_data=metadata or {},
        )
        db.add(txn)
        # Caller (subscribe / upgrade / renew) commits the full unit of work
        db.flush()
        return txn

    def _charge(
        self,
        db: Session,
        *,
        user_id: UUID,
        amount: Decimal,
        description: str,
        payment_method: str,
        reference: str,
        metadata: dict | None = None,
    ) -> None:
        """Debit user wallet for subscription charge."""
        if amount <= 0:
            return

        if payment_method == "wallet":
            try:
                self._debit_wallet_sync(
                    db,
                    user_id=user_id,
                    amount_ngn=amount,
                    description=description,
                    reference=reference,
                    metadata=metadata or {},
                )
            except InsufficientBalanceException:
                raise ValidationException("Insufficient wallet balance")
        elif payment_method == "card":
            raise ValidationException(
                "Card payment must be initiated via the payment gateway. "
                "Top up your wallet and retry with payment_method='wallet'."
            )
        else:
            raise ValidationException(f"Unsupported payment method: {payment_method}")

    # ─── Queries ──────────────────────────────────────────────────────────

    def get_available_plans(self, db: Session) -> list[SubscriptionPlan]:
        """Return all active plans ordered by price."""
        return subscription_plan_crud.get_all_active(db)

    def get_user_subscription(self, db: Session, *, user_id: UUID) -> Subscription:
        """Return the user's current active subscription or raise 404."""
        subscription = subscription_crud.get_active_subscription(db, user_id=user_id)
        if not subscription:
            raise NotFoundException("Active subscription")
        return subscription

    def get_subscription_history(
        self,
        db: Session,
        *,
        user_id: UUID,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[Subscription], int]:
        """Return paginated subscription history."""
        return subscription_crud.get_user_subscription_history(
            db, user_id=user_id, skip=skip, limit=limit
        )

    # ─── Subscribe ────────────────────────────────────────────────────────

    def subscribe(
        self,
        db: Session,
        *,
        user_id: UUID,
        plan_id: UUID,
        billing_cycle: BillingCycleEnum,
        payment_method: str = "wallet",
    ) -> Subscription:
        """
        Subscribe a user to a plan.
        Raises ValidationException if user already has an active subscription.
        """
        plan = subscription_plan_crud.get(db, id=plan_id)
        if not plan or not plan.is_active:
            raise NotFoundException("Subscription plan")

        existing = subscription_crud.get_active_subscription(db, user_id=user_id)
        if existing:
            raise ValidationException(
                "User already has an active subscription. Use /upgrade to change plan."
            )

        price: Decimal = (
            plan.monthly_price
            if billing_cycle == BillingCycleEnum.MONTHLY
            else plan.annual_price
        )

        self._charge(
            db,
            user_id=user_id,
            amount=price,
            description=f"Subscription: {plan.name} ({billing_cycle.value})",
            payment_method=payment_method,
            reference=generate_reference_code("SUB"),
            metadata={"plan_id": str(plan.id), "billing_cycle": billing_cycle.value},
        )

        subscription = subscription_crud.create_subscription(
            db, user_id=user_id, plan=plan, billing_cycle=billing_cycle
        )

        # Sync business tier
        self._sync_business_tier(db, user_id, plan.plan_type.value)

        return subscription

    # ─── Upgrade ──────────────────────────────────────────────────────────

    def upgrade_subscription(
        self,
        db: Session,
        *,
        user_id: UUID,
        new_plan_id: UUID,
        billing_cycle: BillingCycleEnum,
        payment_method: str = "wallet",
    ) -> Subscription:
        """
        Upgrade or downgrade the current subscription.

        Per Blueprint:
        - Upgrade: immediate, prorated charge applied.
        - Downgrade: takes effect at end of billing cycle.
        """
        new_plan = subscription_plan_crud.get(db, id=new_plan_id)
        if not new_plan or not new_plan.is_active:
            raise NotFoundException("Target subscription plan")

        current = subscription_crud.get_active_subscription(db, user_id=user_id)

        new_price: Decimal = (
            new_plan.monthly_price
            if billing_cycle == BillingCycleEnum.MONTHLY
            else new_plan.annual_price
        )

        if current:
            if current.plan_id == new_plan_id and current.billing_cycle == billing_cycle:
                raise ValidationException("Already subscribed to this plan and cycle")

            # Calculate proration credit for remaining time on current plan
            current_plan = subscription_plan_crud.get(db, id=current.plan_id)
            if current_plan:
                current_price: Decimal = (
                    current_plan.monthly_price
                    if current.billing_cycle == BillingCycleEnum.MONTHLY
                    else current_plan.annual_price
                )
                now = datetime.utcnow()
                total_seconds = (
                    current.expires_at.replace(tzinfo=None)
                    - current.started_at.replace(tzinfo=None)
                ).total_seconds()
                remaining_seconds = max(
                    0,
                    (current.expires_at.replace(tzinfo=None) - now).total_seconds(),
                )
                remaining_fraction = (
                    Decimal(str(remaining_seconds / total_seconds))
                    if total_seconds > 0
                    else Decimal("0")
                )
                credit = current_price * remaining_fraction
                charge = max(Decimal("0"), new_price - credit)
            else:
                charge = new_price

            # Cancel current subscription immediately (upgrade replaces it)
            subscription_crud.cancel_subscription(db, subscription_id=current.id)
        else:
            charge = new_price

        self._charge(
            db,
            user_id=user_id,
            amount=charge,
            description=f"Plan upgrade to {new_plan.name} ({billing_cycle.value})",
            payment_method=payment_method,
            reference=generate_reference_code("UPGRADE"),
            metadata={"plan_id": str(new_plan.id), "billing_cycle": billing_cycle.value},
        )

        new_subscription = subscription_crud.create_subscription(
            db, user_id=user_id, plan=new_plan, billing_cycle=billing_cycle
        )

        # Sync business tier immediately (upgrade is immediate per Blueprint)
        self._sync_business_tier(db, user_id, new_plan.plan_type.value)

        return new_subscription

    # ─── Cancel ───────────────────────────────────────────────────────────

    def cancel_subscription(
        self,
        db: Session,
        *,
        subscription_id: UUID,
        user_id: UUID,
    ) -> Subscription:
        """
        Cancel a subscription. Access continues until billing period ends.
        business.subscription_tier is NOT changed here — it downgrades when
        the subscription expires (handled by check_and_expire_subscriptions).
        """
        subscription = subscription_crud.get(db, id=subscription_id)
        if not subscription:
            raise NotFoundException("Subscription")

        if subscription.user_id != user_id:
            raise PermissionDeniedException("You do not own this subscription")

        if subscription.status == "cancelled":
            raise ValidationException("Subscription is already cancelled")

        return subscription_crud.cancel_subscription(db, subscription_id=subscription_id)

    # ─── Toggle auto-renew ────────────────────────────────────────────────

    def toggle_auto_renew(
        self,
        db: Session,
        *,
        subscription_id: UUID,
        auto_renew: bool,
    ) -> Subscription:
        """Enable or disable auto-renewal."""
        subscription = subscription_crud.get(db, id=subscription_id)
        if not subscription:
            raise NotFoundException("Subscription")

        subscription.auto_renew = auto_renew
        db.commit()
        db.refresh(subscription)
        return subscription

    # ─── Renewal (Celery task entry point) ────────────────────────────────

    def renew_subscription(
        self,
        db: Session,
        *,
        subscription_id: UUID,
        payment_method: str = "wallet",
    ) -> Subscription:
        """Manually renew a subscription."""
        subscription = subscription_crud.get(db, id=subscription_id)
        if not subscription:
            raise NotFoundException("Subscription")

        plan = subscription_plan_crud.get(db, id=subscription.plan_id)
        if not plan:
            raise NotFoundException("Associated plan")

        price: Decimal = (
            plan.monthly_price
            if subscription.billing_cycle == BillingCycleEnum.MONTHLY
            else plan.annual_price
        )

        self._charge(
            db,
            user_id=subscription.user_id,
            amount=price,
            description=f"Subscription renewal: {plan.name} ({subscription.billing_cycle.value})",
            payment_method=payment_method,
            reference=generate_reference_code("RENEW"),
        )

        return subscription_crud.renew_subscription(
            db, subscription=subscription, plan=plan
        )

    def auto_renew_subscriptions(self, db: Session) -> int:
        """Auto-renew subscriptions expiring within 24 hours. Returns count renewed."""
        expiring = subscription_crud.get_expiring_soon(db, days=1)
        renewed_count = 0

        for subscription in expiring:
            try:
                plan = subscription_plan_crud.get(db, id=subscription.plan_id)
                if not plan:
                    logger.warning(
                        "Auto-renewal skipped for subscription %s: plan not found",
                        subscription.id,
                    )
                    continue

                price: Decimal = (
                    plan.monthly_price
                    if subscription.billing_cycle == BillingCycleEnum.MONTHLY
                    else plan.annual_price
                )

                self._charge(
                    db,
                    user_id=subscription.user_id,
                    amount=price,
                    description=(
                        f"Auto-renewal: {plan.name} ({subscription.billing_cycle.value})"
                    ),
                    payment_method="wallet",
                    reference=generate_reference_code("AUTORENEW"),
                )

                subscription_crud.renew_subscription(
                    db, subscription=subscription, plan=plan
                )
                renewed_count += 1

            except Exception:
                logger.exception(
                    "Auto-renewal failed for subscription %s", subscription.id
                )
                continue

        return renewed_count

    def check_and_expire_subscriptions(self, db: Session) -> int:
        """
        Mark past-expiry subscriptions as expired and downgrade business tiers.
        Returns count updated.
        """
        from app.models.subscription_model import Subscription as SubModel
        from sqlalchemy import and_
        from sqlalchemy.orm import joinedload

        now = datetime.utcnow()
        expiring = (
            db.query(SubModel)
            .options(joinedload(SubModel.plan))
            .filter(
                and_(
                    SubModel.status == "active",
                    SubModel.expires_at < now,
                )
            )
            .all()
        )

        count = 0
        for sub in expiring:
            sub.status = "expired"
            count += 1
            # Downgrade business tier to free when subscription expires
            self._sync_business_tier(db, sub.user_id, SubscriptionPlanTypeEnum.FREE.value)

        if count > 0:
            db.commit()

        return count


# Singleton instance
subscription_service = SubscriptionService()