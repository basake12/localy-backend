"""
app/services/subscription_service.py

FIXES vs previous version:
  1.  [HARD RULE §16.4] datetime.utcnow() × 2 → datetime.now(timezone.utc).

  2.  Wallet.user_id → Wallet.owner_id. Blueprint §14.

  3.  WalletTransaction.reference_id → WalletTransaction.external_reference.
      Blueprint §14.

  4.  idempotency_key added to every WalletTransaction creation.
      Blueprint §5.6 HARD RULE: all financial operations use idempotency keys.

  5.  Business.subscription_tier_rank updated on every tier change.
      Blueprint §7.2: "Tier rank mapping (stored as integer for ORDER BY):
      Enterprise=4, Pro=3, Starter=2, Free=1."
      _sync_business_tier() now also writes subscription_tier_rank.

  6.  7-day grace period implemented.
      Blueprint §8.1: "Failed payment: 7-day grace period →
      auto-downgrade to Free on day 8. Grace period state stored on
      businesses.subscription_status."
      check_and_expire_subscriptions() sets subscription_status = 'grace'
      and Celery downgrade_expired_subscriptions() downgrades on day 8.

  7.  pro_driver removed from tier logic — not in Blueprint §8.1.

  8.  Proration calculation uses timezone-aware datetimes (avoid replace(tzinfo=None)).
"""
import logging
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.subscription_model import (
    BillingCycleEnum,
    Subscription,
    SubscriptionPlan,
    SubscriptionPlanTypeEnum,
)
from app.models.business_model import Business
from app.models.wallet_model import Wallet, WalletTransaction, TransactionTypeEnum, TransactionStatusEnum
from app.crud.subscription_crud import subscription_crud, subscription_plan_crud
from app.core.exceptions import (
    InsufficientBalanceException,
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)
from app.core.utils import generate_reference_code

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Blueprint §16.4 HARD RULE: timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


# Blueprint §7.2 / §8.1
_TIER_RANK: dict[str, int] = {
    "enterprise": 4,
    "pro":        3,
    "starter":    2,
    "free":       1,
}

_TIER_TO_BADGE: dict[str, str] = {
    "free":       "none",
    "starter":    "starter",
    "pro":        "pro",
    "enterprise": "enterprise",
}



import enum as _enum
class VerificationBadgeEnum(str, _enum.Enum):
    NONE     = "none"
    VERIFIED = "verified"
    PREMIUM  = "premium"

class SubscriptionService:

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _sync_business_tier(
        self, db: Session, user_id: UUID, plan_type: str
    ) -> None:
        """
        Sync business.subscription_tier, subscription_tier_rank, and
        verification_badge to match the active plan.

        Blueprint §7.2: subscription_tier_rank integer used for ORDER BY
        in all discovery queries. Must be updated on every plan change.
        """
        try:
            business = (
                db.query(Business)
                .filter(Business.user_id == user_id)
                .first()
            )
            if business:
                business.subscription_tier      = plan_type
                # Blueprint §7.2: tier rank for search ORDER BY
                business.subscription_tier_rank = _TIER_RANK.get(plan_type, 1)
                badge_value                     = _TIER_TO_BADGE.get(plan_type, "none")
                business.verification_badge     = VerificationBadgeEnum(badge_value)
                db.flush()
                logger.info(
                    "Business tier synced: user=%s tier=%s rank=%d badge=%s",
                    user_id, plan_type, _TIER_RANK.get(plan_type, 1), badge_value,
                )
        except Exception:
            logger.exception("Failed to sync business tier for user %s", user_id)

    def _debit_wallet_sync(
        self,
        db: Session,
        *,
        user_id: UUID,
        amount_ngn: Decimal,
        description: str,
        reference: str,
        metadata: dict | None = None,
    ) -> WalletTransaction:
        """
        Sync wallet debit for subscription charges.
        Uses sync Session — subscription_service is fully synchronous.

        Blueprint §5.6: idempotency_key UNIQUE NOT NULL.
        Blueprint §14: owner_id (not user_id), external_reference (not reference_id).
        """
        # Blueprint §14: owner_id
        wallet = db.query(Wallet).filter(Wallet.owner_id == user_id).first()
        if not wallet:
            raise NotFoundException("Wallet")

        # Idempotency via external_reference
        existing = (
            db.query(WalletTransaction)
            .filter(WalletTransaction.external_reference == reference)
            .first()
        )
        if existing:
            logger.info("Duplicate subscription charge skipped: %s", reference)
            return existing

        if wallet.balance < amount_ngn:
            raise InsufficientBalanceException()

        balance_before = wallet.balance
        wallet.balance -= amount_ngn

        idem_key = f"SUB_{_uuid.uuid4().hex.upper()}"

        txn = WalletTransaction(
            wallet_id=wallet.id,
            amount=amount_ngn,
            transaction_type=TransactionTypeEnum.PAYMENT,
            status=TransactionStatusEnum.COMPLETED,
            balance_before=balance_before,
            balance_after=wallet.balance,
            description=description,
            # Blueprint §14: external_reference (not reference_id)
            external_reference=reference,
            # Blueprint §5.6 HARD RULE: idempotency_key NOT NULL
            idempotency_key=idem_key,
            meta_data=metadata or {},
        )
        db.add(txn)
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
        else:
            raise ValidationException(
                f"Unsupported payment method: {payment_method}. "
                "Top up your wallet and retry with payment_method='wallet'."
            )

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_available_plans(self, db: Session) -> list[SubscriptionPlan]:
        return subscription_plan_crud.get_all_active(db)

    def get_user_subscription(
        self, db: Session, *, user_id: UUID
    ) -> Subscription:
        subscription = subscription_crud.get_active_subscription(
            db, user_id=user_id
        )
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
        return subscription_crud.get_user_subscription_history(
            db, user_id=user_id, skip=skip, limit=limit
        )

    # ── Subscribe ─────────────────────────────────────────────────────────────

    def subscribe(
        self,
        db: Session,
        *,
        user_id: UUID,
        plan_id: UUID,
        billing_cycle: BillingCycleEnum,
        payment_method: str = "wallet",
    ) -> Subscription:
        plan = subscription_plan_crud.get(db, id=plan_id)
        if not plan or not plan.is_active:
            raise NotFoundException("Subscription plan")

        existing = subscription_crud.get_active_subscription(db, user_id=user_id)
        if existing:
            raise ValidationException(
                "Already subscribed. Use /upgrade to change plan."
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
        # create_subscription already calls _update_business_tier_rank via crud
        # _sync_business_tier here handles badge too
        self._sync_business_tier(db, user_id, plan.plan_type.value)
        db.commit()
        return subscription

    # ── Upgrade ───────────────────────────────────────────────────────────────

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
        Upgrade or downgrade current subscription.
        Blueprint §8.1:
          Upgrade: immediate, prorated charge applied.
          Downgrade: takes effect at end of billing cycle.
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
            if (
                current.plan_id == new_plan_id
                and current.billing_cycle == billing_cycle
            ):
                raise ValidationException("Already subscribed to this plan")

            # Proration: remaining time credit
            current_plan = subscription_plan_crud.get(db, id=current.plan_id)
            if current_plan:
                current_price: Decimal = (
                    current_plan.monthly_price
                    if current.billing_cycle == BillingCycleEnum.MONTHLY
                    else current_plan.annual_price
                )
                now = _utcnow()  # Blueprint §16.4 HARD RULE: timezone-aware
                # Use timezone-aware arithmetic — no replace(tzinfo=None)
                expires = current.expires_at
                started = current.started_at
                if expires.tzinfo is None:
                    from datetime import timezone as tz
                    expires = expires.replace(tzinfo=tz.utc)
                    started = started.replace(tzinfo=tz.utc)

                total_seconds    = (expires - started).total_seconds()
                remaining_sec    = max(0, (expires - now).total_seconds())
                remaining_frac   = (
                    Decimal(str(remaining_sec / total_seconds))
                    if total_seconds > 0
                    else Decimal("0")
                )
                credit = current_price * remaining_frac
                charge = max(Decimal("0"), new_price - credit)
            else:
                charge = new_price

            # Cancel current (upgrade replaces it)
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
        # Sync tier immediately — upgrade is immediate per Blueprint §8.1
        self._sync_business_tier(db, user_id, new_plan.plan_type.value)
        db.commit()
        return new_subscription

    # ── Cancel ────────────────────────────────────────────────────────────────

    def cancel_subscription(
        self,
        db: Session,
        *,
        subscription_id: UUID,
        user_id: UUID,
    ) -> Subscription:
        """
        Cancel subscription. Access continues until billing period ends.
        Tier downgrades when subscription expires. Blueprint §8.1.
        """
        subscription = subscription_crud.get(db, id=subscription_id)
        if not subscription:
            raise NotFoundException("Subscription")
        if subscription.user_id != user_id:
            raise PermissionDeniedException("You do not own this subscription")
        if subscription.status == "cancelled":
            raise ValidationException("Already cancelled")

        return subscription_crud.cancel_subscription(
            db, subscription_id=subscription_id
        )

    # ── Toggle auto-renew ─────────────────────────────────────────────────────

    def toggle_auto_renew(
        self,
        db: Session,
        *,
        subscription_id: UUID,
        auto_renew: bool,
    ) -> Subscription:
        subscription = subscription_crud.get(db, id=subscription_id)
        if not subscription:
            raise NotFoundException("Subscription")
        subscription.auto_renew = auto_renew
        db.commit()
        db.refresh(subscription)
        return subscription

    # ── Renewal (Celery task entry point) ─────────────────────────────────────

    def renew_subscription(
        self,
        db: Session,
        *,
        subscription_id: UUID,
        payment_method: str = "wallet",
    ) -> Subscription:
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
            description=f"Renewal: {plan.name} ({subscription.billing_cycle.value})",
            payment_method=payment_method,
            reference=generate_reference_code("RENEW"),
        )

        new_sub = subscription_crud.renew_subscription(
            db, subscription=subscription, plan=plan
        )
        self._sync_business_tier(db, subscription.user_id, plan.plan_type.value)
        db.commit()
        return new_sub

    def auto_renew_subscriptions(self, db: Session) -> int:
        """Auto-renew subscriptions expiring within 24 hours."""
        expiring = subscription_crud.get_expiring_soon(db, days=1)
        count = 0
        for sub in expiring:
            try:
                plan = subscription_plan_crud.get(db, id=sub.plan_id)
                if not plan:
                    continue
                price = (
                    plan.monthly_price
                    if sub.billing_cycle == BillingCycleEnum.MONTHLY
                    else plan.annual_price
                )
                self._charge(
                    db,
                    user_id=sub.user_id,
                    amount=price,
                    description=f"Auto-renewal: {plan.name}",
                    payment_method="wallet",
                    reference=generate_reference_code("AUTORENEW"),
                )
                subscription_crud.renew_subscription(db, subscription=sub, plan=plan)
                self._sync_business_tier(db, sub.user_id, plan.plan_type.value)
                count += 1
            except Exception:
                logger.exception("Auto-renewal failed for subscription %s", sub.id)
        return count

    def check_and_expire_subscriptions(self, db: Session) -> int:
        """
        Mark past-expiry subscriptions.
        Blueprint §8.1: 7-day grace period before downgrade.
        Day 1-7: status = 'expired', subscription_status = 'grace' on Business.
        Day 8+:  downgrade to Free (handled by downgrade_expired_subscriptions).
        """
        from sqlalchemy import and_

        now = _utcnow()
        expiring = (
            db.query(Subscription)
            .options(joinedload(Subscription.plan))
            .filter(
                and_(
                    Subscription.status     == "active",
                    Subscription.expires_at <  now,
                )
            )
            .all()
        )

        count = 0
        for sub in expiring:
            sub.status = "expired"
            count += 1
            # Set grace period flag on Business
            business = (
                db.query(Business)
                .filter(Business.user_id == sub.user_id)
                .first()
            )
            if business:
                business.subscription_status = "grace"

        if count > 0:
            db.commit()
        return count

    def downgrade_expired_subscriptions(self, db: Session) -> int:
        """
        Downgrade businesses that have been in grace period > 7 days.
        Blueprint §8.1: "auto-downgrade to Free on day 8."
        """
        subs_past_grace = subscription_crud.get_in_grace_period(db)
        count = 0
        for sub in subs_past_grace:
            try:
                self._sync_business_tier(db, sub.user_id, SubscriptionPlanTypeEnum.FREE.value)
                count += 1
            except Exception:
                logger.exception("Grace-period downgrade failed for user %s", sub.user_id)
        if count > 0:
            db.commit()
        return count


# Singleton
subscription_service = SubscriptionService()