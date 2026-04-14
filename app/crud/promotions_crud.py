"""
app/crud/promotions_crud.py

Database access layer for Promotions, PromotionRedemptions, and StreakProgress.
All business logic lives in promotions_service.py.
"""
from typing import List, Optional, Tuple
from uuid import UUID
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, func, and_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.promotions_model import (
    Promotion,
    PromotionRedemption,
    StreakProgress,
    PromotionType,
    PromotionStatus,
    StreakActionType,
)


# ─── Promotion CRUD ───────────────────────────────────────────────────────────

class PromotionCRUD:

    # ── Create ────────────────────────────────────────────────────────────────

    async def create(
        self,
        db: AsyncSession,
        *,
        data: dict,
        admin_id: Optional[UUID] = None,
    ) -> Promotion:
        promotion = Promotion(**data, created_by_admin_id=admin_id)
        db.add(promotion)
        await db.flush()
        await db.refresh(promotion)
        return promotion

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get(self, db: AsyncSession, promotion_id: UUID) -> Optional[Promotion]:
        result = await db.execute(
            select(Promotion).where(Promotion.id == promotion_id)
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        db: AsyncSession,
        *,
        status: Optional[PromotionStatus] = None,
        promotion_type: Optional[PromotionType] = None,
        is_public_only: bool = False,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[Promotion], int]:
        q = select(Promotion)
        conditions = []

        if status:
            conditions.append(Promotion.status == status)
        if promotion_type:
            conditions.append(Promotion.promotion_type == promotion_type)
        if is_public_only:
            conditions.append(Promotion.is_public.is_(True))

        if conditions:
            q = q.where(and_(*conditions))

        total_result = await db.execute(
            select(func.count()).select_from(q.subquery())
        )
        total = total_result.scalar_one()

        q = q.order_by(Promotion.start_date.desc()).offset(skip).limit(limit)
        result = await db.execute(q)
        return result.scalars().all(), total

    async def get_active_promotions(
        self,
        db: AsyncSession,
        *,
        promotion_type: Optional[PromotionType] = None,
    ) -> List[Promotion]:
        """Return all promotions that are currently active."""
        now = datetime.now(timezone.utc)
        conditions = [
            Promotion.status == PromotionStatus.ACTIVE,
            Promotion.start_date <= now,
            Promotion.end_date >= now,
        ]
        if promotion_type:
            conditions.append(Promotion.promotion_type == promotion_type)

        result = await db.execute(
            select(Promotion)
            .where(and_(*conditions))
            .order_by(Promotion.start_date.asc())
        )
        return result.scalars().all()

    async def get_active_for_module(
        self,
        db: AsyncSession,
        *,
        module: str,
    ) -> List[Promotion]:
        """Return active CASHBACK_EVENT promotions applicable to a specific module."""
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(Promotion).where(
                and_(
                    Promotion.status == PromotionStatus.ACTIVE,
                    Promotion.promotion_type == PromotionType.CASHBACK_EVENT,
                    Promotion.start_date <= now,
                    Promotion.end_date >= now,
                    Promotion.applicable_modules.contains([module]),
                )
            )
        )
        return result.scalars().all()

    async def get_active_double_referral(
        self, db: AsyncSession
    ) -> Optional[Promotion]:
        """Return the active DOUBLE_REFERRAL promotion if one exists."""
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(Promotion).where(
                and_(
                    Promotion.status == PromotionStatus.ACTIVE,
                    Promotion.promotion_type == PromotionType.DOUBLE_REFERRAL,
                    Promotion.start_date <= now,
                    Promotion.end_date >= now,
                )
            ).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_active_streak_for_action(
        self,
        db: AsyncSession,
        *,
        action_type: StreakActionType,
    ) -> List[Promotion]:
        """Return active STREAK_REWARD promotions that match the given action type."""
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(Promotion).where(
                and_(
                    Promotion.status == PromotionStatus.ACTIVE,
                    Promotion.promotion_type == PromotionType.STREAK_REWARD,
                    Promotion.start_date <= now,
                    Promotion.end_date >= now,
                    # Match specific action OR any_order/any_booking catch-alls
                    Promotion.streak_action_type.in_([
                        action_type,
                        StreakActionType.ANY_ORDER,
                        StreakActionType.ANY_BOOKING,
                    ]),
                )
            )
        )
        return result.scalars().all()

    # ── Update ────────────────────────────────────────────────────────────────

    async def update(
        self,
        db: AsyncSession,
        *,
        promotion: Promotion,
        data: dict,
    ) -> Promotion:
        for field, value in data.items():
            if value is not None:
                setattr(promotion, field, value)
        await db.flush()
        await db.refresh(promotion)
        return promotion

    async def increment_redemptions(
        self, db: AsyncSession, *, promotion_id: UUID
    ) -> None:
        await db.execute(
            update(Promotion)
            .where(Promotion.id == promotion_id)
            .values(current_redemptions=Promotion.current_redemptions + 1)
        )

    async def sync_statuses(self, db: AsyncSession) -> int:
        """
        Sync promotion statuses based on current time.
        Called by Celery task every hour.
        Returns number of records updated.
        """
        now = datetime.now(timezone.utc)
        count = 0

        # SCHEDULED → ACTIVE
        result = await db.execute(
            update(Promotion)
            .where(
                and_(
                    Promotion.status == PromotionStatus.SCHEDULED,
                    Promotion.start_date <= now,
                    Promotion.end_date >= now,
                )
            )
            .values(status=PromotionStatus.ACTIVE)
        )
        count += result.rowcount

        # ACTIVE → ENDED (past end date)
        result = await db.execute(
            update(Promotion)
            .where(
                and_(
                    Promotion.status == PromotionStatus.ACTIVE,
                    Promotion.end_date < now,
                )
            )
            .values(status=PromotionStatus.ENDED)
        )
        count += result.rowcount

        return count


# ─── Redemption CRUD ──────────────────────────────────────────────────────────

class PromotionRedemptionCRUD:

    async def create(
        self,
        db: AsyncSession,
        *,
        promotion_id: UUID,
        user_id: UUID,
        amount_credited: Decimal,
        wallet_transaction_id: Optional[UUID] = None,
        trigger_type: Optional[str] = None,
        trigger_id: Optional[UUID] = None,
        meta_data: Optional[dict] = None,
    ) -> PromotionRedemption:
        record = PromotionRedemption(
            promotion_id=promotion_id,
            user_id=user_id,
            amount_credited=amount_credited,
            wallet_transaction_id=wallet_transaction_id,
            trigger_type=trigger_type,
            trigger_id=trigger_id,
            meta_data=meta_data,
        )
        db.add(record)
        await db.flush()
        await db.refresh(record)
        return record

    async def count_user_redemptions(
        self,
        db: AsyncSession,
        *,
        promotion_id: UUID,
        user_id: UUID,
    ) -> int:
        result = await db.execute(
            select(func.count(PromotionRedemption.id)).where(
                and_(
                    PromotionRedemption.promotion_id == promotion_id,
                    PromotionRedemption.user_id == user_id,
                )
            )
        )
        return result.scalar_one()

    async def get_user_total_cashback(
        self,
        db: AsyncSession,
        *,
        promotion_id: UUID,
        user_id: UUID,
    ) -> Decimal:
        """Total cashback received by user for this promotion (for cap enforcement)."""
        result = await db.execute(
            select(func.coalesce(func.sum(PromotionRedemption.amount_credited), 0)).where(
                and_(
                    PromotionRedemption.promotion_id == promotion_id,
                    PromotionRedemption.user_id == user_id,
                )
            )
        )
        return Decimal(str(result.scalar_one()))

    async def list_for_user(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[PromotionRedemption], int]:
        total_result = await db.execute(
            select(func.count(PromotionRedemption.id)).where(
                PromotionRedemption.user_id == user_id
            )
        )
        total = total_result.scalar_one()

        result = await db.execute(
            select(PromotionRedemption)
            .where(PromotionRedemption.user_id == user_id)
            .order_by(PromotionRedemption.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all(), total

    async def get_analytics(
        self,
        db: AsyncSession,
        *,
        promotion_id: UUID,
    ) -> dict:
        total_result = await db.execute(
            select(
                func.count(PromotionRedemption.id),
                func.coalesce(func.sum(PromotionRedemption.amount_credited), 0),
                func.count(func.distinct(PromotionRedemption.user_id)),
            ).where(PromotionRedemption.promotion_id == promotion_id)
        )
        row = total_result.one()
        return {
            "total_redemptions":   row[0],
            "total_amount_issued": Decimal(str(row[1])),
            "unique_users":        row[2],
        }


# ─── Streak Progress CRUD ─────────────────────────────────────────────────────

class StreakProgressCRUD:

    async def get_or_create(
        self,
        db: AsyncSession,
        *,
        promotion_id: UUID,
        user_id: UUID,
        target_count: int,
    ) -> StreakProgress:
        result = await db.execute(
            select(StreakProgress).where(
                and_(
                    StreakProgress.promotion_id == promotion_id,
                    StreakProgress.user_id == user_id,
                )
            )
        )
        progress = result.scalar_one_or_none()
        if not progress:
            progress = StreakProgress(
                promotion_id=promotion_id,
                user_id=user_id,
                current_count=0,
                target_count=target_count,
                completed=False,
                qualifying_action_ids=[],
            )
            db.add(progress)
            await db.flush()
            await db.refresh(progress)
        return progress

    async def increment(
        self,
        db: AsyncSession,
        *,
        progress: StreakProgress,
        action_id: Optional[str] = None,
    ) -> StreakProgress:
        progress.current_count += 1
        progress.last_action_at = datetime.now(timezone.utc)
        if action_id:
            ids = list(progress.qualifying_action_ids or [])
            ids.append(action_id)
            progress.qualifying_action_ids = ids
        await db.flush()
        await db.refresh(progress)
        return progress

    async def mark_completed(
        self, db: AsyncSession, *, progress: StreakProgress
    ) -> StreakProgress:
        progress.completed = True
        await db.flush()
        await db.refresh(progress)
        return progress

    async def get_user_active_streaks(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
    ) -> List[StreakProgress]:
        """Return all incomplete streak progresses for a user in active promotions."""
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(StreakProgress)
            .join(Promotion, StreakProgress.promotion_id == Promotion.id)
            .where(
                and_(
                    StreakProgress.user_id == user_id,
                    StreakProgress.completed.is_(False),
                    Promotion.status == PromotionStatus.ACTIVE,
                    Promotion.end_date >= now,
                )
            )
            .options(selectinload(StreakProgress.promotion))
        )
        return result.scalars().all()


# ─── Singletons ───────────────────────────────────────────────────────────────

promotion_crud            = PromotionCRUD()
promotion_redemption_crud = PromotionRedemptionCRUD()
streak_progress_crud      = StreakProgressCRUD()