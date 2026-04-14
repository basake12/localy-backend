"""
app/services/promotions_service.py

Business logic for all promotion types.

Per Blueprint v2.0 Section 4.1.3 & 7.3:
  - Funding bonus: triggered by wallet_service after successful top-up
  - Cashback event: triggered by order/booking completion services
  - Double referral: queried by referral_service before crediting referrer
  - Streak reward: triggered by order/booking completion services

Public hook methods (called by other services):
  on_wallet_funded(db, user_id, amount)             → credits funding bonus if eligible
  on_order_completed(db, user_id, amount, module)   → credits cashback if eligible
  on_booking_completed(db, user_id, amount, module) → credits cashback + increments streak
  get_referral_multiplier(db)                       → returns active multiplier (1.0 = none)
  on_referral_rewarded(db, user_id, base_amount)    → credits multiplied reward
"""
import logging
import uuid
from decimal import Decimal
from typing import Optional, Tuple
from uuid import UUID
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.promotions_crud import (
    promotion_crud,
    promotion_redemption_crud,
    streak_progress_crud,
)
from app.crud.wallet_crud import wallet_crud
from app.models.promotions_model import (
    Promotion,
    PromotionType,
    PromotionStatus,
    StreakActionType,
)
from app.models.wallet_model import TransactionType, TransactionStatus
from app.schemas.promotions_schema import (
    PromotionCreate,
    PromotionUpdate,
    PromotionOut,
    StreakProgressOut,
    ActiveReferralMultiplier,
    FundingBonusCheck,
    CashbackCheck,
    PromotionAnalytics,
)
from app.core.exceptions import NotFoundException, ValidationException

logger = logging.getLogger(__name__)

# Map module strings to streak action types
_MODULE_TO_STREAK_ACTION = {
    "food":    StreakActionType.FOOD_ORDER,
    "hotels":  StreakActionType.HOTEL_BOOKING,
    "services": StreakActionType.SERVICE_BOOKING,
    "health":  StreakActionType.HEALTH_BOOKING,
    "tickets": StreakActionType.TICKET_PURCHASE,
    "products": StreakActionType.ANY_ORDER,
}


class PromotionsService:

    # ═══════════════════════════════════════════════════════════════════════
    # ADMIN — CRUD
    # ═══════════════════════════════════════════════════════════════════════

    async def create_promotion(
        self,
        db: AsyncSession,
        *,
        payload: PromotionCreate,
        admin_id: UUID,
    ) -> Promotion:
        data = payload.model_dump(exclude_none=True)
        # Determine initial status
        now = datetime.now(timezone.utc)
        data["status"] = (
            PromotionStatus.ACTIVE
            if payload.start_date <= now <= payload.end_date
            else PromotionStatus.SCHEDULED
        )
        promotion = await promotion_crud.create(db, data=data, admin_id=admin_id)
        await db.commit()
        await db.refresh(promotion)
        return promotion

    async def update_promotion(
        self,
        db: AsyncSession,
        *,
        promotion_id: UUID,
        payload: PromotionUpdate,
    ) -> Promotion:
        promotion = await promotion_crud.get(db, promotion_id)
        if not promotion:
            raise NotFoundException("Promotion not found")
        data = payload.model_dump(exclude_none=True)
        promotion = await promotion_crud.update(db, promotion=promotion, data=data)
        await db.commit()
        await db.refresh(promotion)
        return promotion

    async def delete_promotion(
        self, db: AsyncSession, *, promotion_id: UUID
    ) -> None:
        promotion = await promotion_crud.get(db, promotion_id)
        if not promotion:
            raise NotFoundException("Promotion not found")
        if promotion.status == PromotionStatus.ACTIVE:
            raise ValidationException(
                "Cannot delete an active promotion — pause or end it first"
            )
        await db.delete(promotion)
        await db.commit()

    async def list_promotions(
        self,
        db: AsyncSession,
        *,
        status: Optional[PromotionStatus] = None,
        promotion_type: Optional[PromotionType] = None,
        is_public_only: bool = False,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[list, int]:
        return await promotion_crud.list_all(
            db,
            status=status,
            promotion_type=promotion_type,
            is_public_only=is_public_only,
            skip=skip,
            limit=limit,
        )

    async def get_promotion(
        self, db: AsyncSession, *, promotion_id: UUID
    ) -> Promotion:
        promotion = await promotion_crud.get(db, promotion_id)
        if not promotion:
            raise NotFoundException("Promotion not found")
        return promotion

    async def get_active_promotions(
        self, db: AsyncSession
    ) -> list:
        return await promotion_crud.get_active_promotions(db)

    async def get_analytics(
        self, db: AsyncSession, *, promotion_id: UUID
    ) -> PromotionAnalytics:
        promotion = await self.get_promotion(db, promotion_id=promotion_id)
        stats = await promotion_redemption_crud.get_analytics(
            db, promotion_id=promotion_id
        )
        return PromotionAnalytics(
            promotion_id=promotion.id,
            promotion_title=promotion.title,
            promotion_type=promotion.promotion_type,
            total_redemptions=stats["total_redemptions"],
            total_amount_issued=stats["total_amount_issued"],
            unique_users=stats["unique_users"],
            start_date=promotion.start_date,
            end_date=promotion.end_date,
            status=promotion.status,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # HOOK: FUNDING BONUS
    # ═══════════════════════════════════════════════════════════════════════

    async def on_wallet_funded(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        funded_amount: Decimal,
    ) -> Optional[Decimal]:
        """
        Called by wallet_service after a successful top-up.
        Checks for active FUNDING_BONUS promotions and credits bonus if eligible.
        Returns the bonus amount credited, or None if no bonus applied.

        Per Blueprint: "Fund ₦5,000, get ₦200 bonus" (admin-configured).
        """
        promotions = await promotion_crud.get_active_promotions(
            db, promotion_type=PromotionType.FUNDING_BONUS
        )
        if not promotions:
            return None

        total_bonus = Decimal("0")
        for promo in promotions:
            if not promo.is_currently_active():
                continue
            if not promo.has_capacity():
                continue
            if promo.min_funding_amount and funded_amount < promo.min_funding_amount:
                continue

            # Check user hasn't exceeded max_per_user
            user_count = await promotion_redemption_crud.count_user_redemptions(
                db, promotion_id=promo.id, user_id=user_id
            )
            if user_count >= promo.max_per_user:
                continue

            bonus = promo.bonus_amount or Decimal("0")
            if bonus <= 0:
                continue

            # Credit bonus to wallet
            reference = f"promo_bonus_{promo.id}_{user_id}_{uuid.uuid4().hex[:8]}"
            txn = await self._credit_promotion(
                db,
                user_id=user_id,
                amount=bonus,
                description=f"Promo bonus: {promo.title}",
                reference=reference,
                transaction_type=TransactionType.CASHBACK,
            )

            # Record redemption
            await promotion_redemption_crud.create(
                db,
                promotion_id=promo.id,
                user_id=user_id,
                amount_credited=bonus,
                wallet_transaction_id=txn.id if txn else None,
                trigger_type="wallet_funding",
                meta_data={"funded_amount": float(funded_amount)},
            )

            # Increment promotion counter
            await promotion_crud.increment_redemptions(db, promotion_id=promo.id)
            total_bonus += bonus
            logger.info(
                "Funding bonus ₦%s credited to user %s for promo %s",
                bonus, user_id, promo.id,
            )

        await db.commit()
        return total_bonus if total_bonus > 0 else None

    # ═══════════════════════════════════════════════════════════════════════
    # HOOK: CASHBACK EVENT
    # ═══════════════════════════════════════════════════════════════════════

    async def on_order_completed(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        order_amount: Decimal,
        module: str,
        order_id: Optional[UUID] = None,
    ) -> Optional[Decimal]:
        """
        Called after any order/booking is completed.
        Checks for active CASHBACK_EVENT promotions for the given module.
        Returns total cashback credited, or None if none applied.

        Per Blueprint: "Earn 5% cashback on all food orders this weekend".
        """
        promotions = await promotion_crud.get_active_for_module(db, module=module)
        if not promotions:
            # Also process streak progress even if no cashback
            await self._process_streak(db, user_id=user_id, module=module, order_id=order_id)
            return None

        total_cashback = Decimal("0")
        for promo in promotions:
            if not promo.is_currently_active():
                continue
            if not promo.has_capacity():
                continue

            # Check per-user redemption limit
            user_count = await promotion_redemption_crud.count_user_redemptions(
                db, promotion_id=promo.id, user_id=user_id
            )
            if user_count >= promo.max_per_user:
                continue

            # Check per-user total cashback cap
            if promo.max_cashback_per_user:
                user_total = await promotion_redemption_crud.get_user_total_cashback(
                    db, promotion_id=promo.id, user_id=user_id
                )
                if user_total >= promo.max_cashback_per_user:
                    continue

            # Calculate cashback
            pct = promo.cashback_percentage or Decimal("0")
            cashback = (order_amount * pct / Decimal("100")).quantize(Decimal("0.01"))

            # Apply per-transaction cap
            if promo.max_cashback_amount:
                cashback = min(cashback, promo.max_cashback_amount)

            # Apply remaining per-user cap
            if promo.max_cashback_per_user:
                user_total = await promotion_redemption_crud.get_user_total_cashback(
                    db, promotion_id=promo.id, user_id=user_id
                )
                remaining_cap = promo.max_cashback_per_user - user_total
                cashback = min(cashback, remaining_cap)

            if cashback <= 0:
                continue

            reference = f"promo_cashback_{promo.id}_{user_id}_{uuid.uuid4().hex[:8]}"
            txn = await self._credit_promotion(
                db,
                user_id=user_id,
                amount=cashback,
                description=f"Cashback: {promo.title}",
                reference=reference,
                transaction_type=TransactionType.CASHBACK,
            )

            await promotion_redemption_crud.create(
                db,
                promotion_id=promo.id,
                user_id=user_id,
                amount_credited=cashback,
                wallet_transaction_id=txn.id if txn else None,
                trigger_type=f"{module}_order",
                trigger_id=order_id,
                meta_data={
                    "order_amount": float(order_amount),
                    "cashback_pct": float(pct),
                    "module":       module,
                },
            )
            await promotion_crud.increment_redemptions(db, promotion_id=promo.id)
            total_cashback += cashback
            logger.info(
                "Cashback ₦%s credited to user %s for promo %s module %s",
                cashback, user_id, promo.id, module,
            )

        # Process streak progress regardless of cashback
        await self._process_streak(db, user_id=user_id, module=module, order_id=order_id)

        await db.commit()
        return total_cashback if total_cashback > 0 else None

    # ═══════════════════════════════════════════════════════════════════════
    # HOOK: STREAK REWARD (internal)
    # ═══════════════════════════════════════════════════════════════════════

    async def _process_streak(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        module: str,
        order_id: Optional[UUID] = None,
    ) -> None:
        """
        Internal: Increment streak progress for any active STREAK_REWARD promotions
        that match the action. If target is reached, credit the reward.

        Per Blueprint: "Book 3 services this month and earn ₦1,500".
        """
        action_type = _MODULE_TO_STREAK_ACTION.get(module, StreakActionType.ANY_ORDER)
        promotions = await promotion_crud.get_active_streak_for_action(
            db, action_type=action_type
        )

        for promo in promotions:
            if not promo.is_currently_active():
                continue
            if not promo.streak_target_count or not promo.streak_reward_amount:
                continue

            progress = await streak_progress_crud.get_or_create(
                db,
                promotion_id=promo.id,
                user_id=user_id,
                target_count=promo.streak_target_count,
            )

            if progress.completed:
                continue

            # Increment
            progress = await streak_progress_crud.increment(
                db,
                progress=progress,
                action_id=str(order_id) if order_id else None,
            )

            logger.info(
                "Streak %s/%s for user %s promo %s",
                progress.current_count, progress.target_count, user_id, promo.id,
            )

            # Check completion
            if progress.current_count >= progress.target_count:
                reward = promo.streak_reward_amount

                reference = f"promo_streak_{promo.id}_{user_id}_{uuid.uuid4().hex[:8]}"
                txn = await self._credit_promotion(
                    db,
                    user_id=user_id,
                    amount=reward,
                    description=f"Streak reward: {promo.title}",
                    reference=reference,
                    transaction_type=TransactionType.CASHBACK,
                )

                await promotion_redemption_crud.create(
                    db,
                    promotion_id=promo.id,
                    user_id=user_id,
                    amount_credited=reward,
                    wallet_transaction_id=txn.id if txn else None,
                    trigger_type="streak_completed",
                    trigger_id=order_id,
                    meta_data={
                        "streak_count": progress.current_count,
                        "module":       module,
                    },
                )
                await promotion_crud.increment_redemptions(db, promotion_id=promo.id)
                await streak_progress_crud.mark_completed(db, progress=progress)

                logger.info(
                    "Streak reward ₦%s credited to user %s for promo %s",
                    reward, user_id, promo.id,
                )

    # ═══════════════════════════════════════════════════════════════════════
    # HOOK: DOUBLE REFERRAL
    # ═══════════════════════════════════════════════════════════════════════

    async def get_referral_multiplier(
        self, db: AsyncSession
    ) -> ActiveReferralMultiplier:
        """
        Called by referral_service before crediting a referrer.
        Returns the current multiplier (1.0 if no active double-referral event).

        Per Blueprint: "Refer this weekend and earn ₦2,000 instead of ₦1,000".
        """
        from app.core.constants import REFERRAL_BONUS_AMOUNT

        promo = await promotion_crud.get_active_double_referral(db)
        if not promo or not promo.is_currently_active():
            return ActiveReferralMultiplier(
                multiplier=Decimal("1.0"),
                promotion=None,
                base_amount=REFERRAL_BONUS_AMOUNT,
                final_amount=REFERRAL_BONUS_AMOUNT,
            )

        multiplier = promo.referral_multiplier or Decimal("1.0")
        final_amount = (REFERRAL_BONUS_AMOUNT * multiplier).quantize(Decimal("0.01"))
        return ActiveReferralMultiplier(
            multiplier=multiplier,
            promotion=PromotionOut.model_validate(promo),
            base_amount=REFERRAL_BONUS_AMOUNT,
            final_amount=final_amount,
        )

    async def on_referral_rewarded(
        self,
        db: AsyncSession,
        *,
        referrer_id: UUID,
        referred_id: UUID,
        base_amount: Decimal,
    ) -> Decimal:
        """
        Called by referral_service after confirming a referral order.
        Returns the final amount to credit (multiplied if double-referral is active).
        Records promotion redemption if a multiplier is applied.
        """
        multiplier_info = await self.get_referral_multiplier(db)

        if multiplier_info.multiplier <= Decimal("1.0") or not multiplier_info.promotion:
            return base_amount

        extra_amount = multiplier_info.final_amount - base_amount
        if extra_amount <= 0:
            return base_amount

        promo_id = multiplier_info.promotion.id

        # Check user hasn't already redeemed this promotion
        user_count = await promotion_redemption_crud.count_user_redemptions(
            db, promotion_id=promo_id, user_id=referrer_id
        )
        if user_count >= multiplier_info.promotion.max_per_user:
            return base_amount

        # Credit the bonus (extra over base)
        reference = f"promo_referral_{promo_id}_{referrer_id}_{uuid.uuid4().hex[:8]}"
        txn = await self._credit_promotion(
            db,
            user_id=referrer_id,
            amount=extra_amount,
            description=f"Referral bonus: {multiplier_info.promotion.title}",
            reference=reference,
            transaction_type=TransactionType.REFERRAL_BONUS,
        )

        await promotion_redemption_crud.create(
            db,
            promotion_id=promo_id,
            user_id=referrer_id,
            amount_credited=extra_amount,
            wallet_transaction_id=txn.id if txn else None,
            trigger_type="referral_bonus",
            trigger_id=referred_id,
            meta_data={
                "base_amount":     float(base_amount),
                "multiplier":      float(multiplier_info.multiplier),
                "total_amount":    float(multiplier_info.final_amount),
                "extra_credited":  float(extra_amount),
            },
        )
        await promotion_crud.increment_redemptions(db, promotion_id=promo_id)
        logger.info(
            "Double referral bonus ₦%s credited to referrer %s",
            extra_amount, referrer_id,
        )

        return multiplier_info.final_amount

    # ═══════════════════════════════════════════════════════════════════════
    # CUSTOMER — STREAK PROGRESS
    # ═══════════════════════════════════════════════════════════════════════

    async def get_my_streak_progresses(
        self, db: AsyncSession, *, user_id: UUID
    ) -> list:
        """Return all active, incomplete streak progresses for the user."""
        progresses = await streak_progress_crud.get_user_active_streaks(
            db, user_id=user_id
        )
        result = []
        for p in progresses:
            promo = p.promotion
            result.append(
                StreakProgressOut(
                    id=p.id,
                    promotion_id=p.promotion_id,
                    user_id=p.user_id,
                    current_count=p.current_count,
                    target_count=p.target_count,
                    completed=p.completed,
                    remaining=p.remaining,
                    progress_pct=p.progress_pct,
                    last_action_at=p.last_action_at,
                    promotion_title=promo.title if promo else "",
                    promotion_end_date=promo.end_date if promo else datetime.now(timezone.utc),
                    streak_reward_amount=promo.streak_reward_amount if promo else None,
                )
            )
        return result

    # ═══════════════════════════════════════════════════════════════════════
    # INTERNAL HELPERS
    # ═══════════════════════════════════════════════════════════════════════

    async def _credit_promotion(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        amount: Decimal,
        description: str,
        reference: str,
        transaction_type: TransactionType,
    ):
        """
        Credit promotion bonus to user wallet.
        Uses wallet_crud directly to avoid wallet_service's min-topup validation
        (promotional credits can be below ₦500 minimum).
        """
        try:
            from app.crud.wallet_crud import wallet_crud as wc

            wallet = await wc.get_by_user(db, user_id=user_id)
            if not wallet:
                logger.error("Wallet not found for user %s — skipping promo credit", user_id)
                return None

            # Check idempotency
            from app.crud.wallet_crud import wallet_transaction_crud as wtc
            existing = await wtc.get_by_reference(db, reference_id=reference)
            if existing:
                return existing

            txn = await wc.credit_wallet(
                db,
                wallet_id=wallet.id,
                amount=amount,
                transaction_type=transaction_type,
                description=description,
                reference_id=reference,
                metadata={"source": "promotion"},
            )
            return txn
        except Exception as exc:
            logger.exception(
                "Failed to credit promotion bonus ₦%s to user %s: %s",
                amount, user_id, exc,
            )
            return None


promotions_service = PromotionsService()