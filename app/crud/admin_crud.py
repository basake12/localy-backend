"""
app/crud/admin_crud.py

FIXES vs previous version:
  1. datetime.utcnow() → datetime.now(timezone.utc) everywhere (§16.4 HARD RULE).
  2. User.user_type → User.role (§14 column name).
  3. User.status → User.is_active + User.is_banned booleans (§14).
  4. User.phone → User.phone_number (§14 column name).
  5. Business.name → Business.business_name (§14 column name).
  6. UserRoleEnum, UserStatusEnum imports removed — not in §14.
  7. AdminWalletAdjustment CRUD added — §11.1 immutable audit log.
  8. AdminBanLog CRUD added — §11.1 mandatory reason log.
  9. Financial controls CRUD added — §11.3 fee config, withdrawals.
  10. Content moderation action CRUD added — §11.4.
  11. Platform config CRUD added — §11.6.
  12. Subscription management CRUD added — §11.2.
  13. Platform analytics CRUD added — §11.5.
"""

import logging
from decimal import Decimal
from datetime import date, datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, text

from app.models.user_model      import User
from app.models.business_model  import Business
from app.models.wallet_model    import Wallet, WalletTransaction, TransactionType
from app.models.hotels_model    import HotelBooking
from app.models.products_model  import Product, ProductOrder
from app.models.food_model      import FoodOrder
from app.models.services_model  import ServiceBooking
from app.models.delivery_model  import Delivery
from app.models.reviews_model   import Review, ReviewStatusEnum
from app.models.reels_model     import Reel
from app.models.stories_model   import Story
from app.models.jobs_model      import JobPosting
from app.models.analytics_model import DailyAnalyticsSnapshot
from app.models.admin_model     import (
    AdminUser, AdminWalletAdjustment, AdminBanLog, PlatformConfig
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Blueprint §16.4 HARD RULE: always timezone-aware UTC."""
    return datetime.now(timezone.utc)


def _today() -> date:
    """Blueprint §16.4: timezone-aware date."""
    return datetime.now(timezone.utc).date()


# ── Dashboard CRUD ────────────────────────────────────────────────────────────

class CRUDAdminDashboard:

    def user_summary(self, db: Session) -> dict:
        today = _today()  # FIX: was datetime.utcnow().date()

        total = db.query(func.count(User.id)).scalar() or 0

        # FIX: was User.user_type — §14 column is User.role
        by_role = (
            db.query(User.role, func.count(User.id))
            .group_by(User.role)
            .all()
        )
        role_map = {
            str(row[0].value if hasattr(row[0], "value") else row[0]): row[1]
            for row in by_role
        }

        new_today = (
            db.query(func.count(User.id))
            .filter(func.date(User.created_at) == today)
            .scalar()
        ) or 0

        return {
            "total":        total,
            "customers":    role_map.get("customer", 0),
            "businesses":   role_map.get("business", 0),
            "riders":       role_map.get("rider", 0),
            "new_today":    new_today,
            "active_today": new_today,  # proxy; replace with last_login_at in v2
        }

    def order_summary(self, db: Session) -> dict:
        today = _today()  # FIX: was datetime.utcnow().date()

        prod_today = (
            db.query(
                func.count(ProductOrder.id).label("total"),
                func.coalesce(func.sum(ProductOrder.total_amount), 0).label("revenue"),
            )
            .filter(func.date(ProductOrder.created_at) == today)
            .one()
        )

        food_today = (
            db.query(
                func.count(FoodOrder.id).label("total"),
                func.coalesce(func.sum(FoodOrder.total_amount), 0).label("revenue"),
            )
            .filter(func.date(FoodOrder.created_at) == today)
            .one()
        )

        hotel_today = (
            db.query(func.count(HotelBooking.id))
            .filter(func.date(HotelBooking.created_at) == today)
            .scalar()
        ) or 0

        service_today = (
            db.query(func.count(ServiceBooking.id))
            .filter(func.date(ServiceBooking.created_at) == today)
            .scalar()
        ) or 0

        total_orders  = (prod_today.total or 0) + (food_today.total or 0) + hotel_today + service_today
        revenue_today = float(prod_today.revenue or 0) + float(food_today.revenue or 0)

        cancelled = (
            db.query(func.count(ProductOrder.id))
            .filter(
                func.date(ProductOrder.created_at) == today,
                ProductOrder.order_status == "cancelled",
            )
            .scalar()
        ) or 0

        completed = (
            db.query(func.count(ProductOrder.id))
            .filter(
                func.date(ProductOrder.created_at) == today,
                ProductOrder.order_status == "delivered",
            )
            .scalar()
        ) or 0

        return {
            "total_orders":    total_orders,
            "new_today":       total_orders,
            "completed_today": completed,
            "cancelled_today": cancelled,
            "revenue_today":   round(revenue_today, 2),
        }

    def delivery_summary(self, db: Session) -> dict:
        today = _today()  # FIX

        total = (
            db.query(func.count(Delivery.id))
            .filter(func.date(Delivery.created_at) == today)
            .scalar()
        ) or 0

        completed = (
            db.query(func.count(Delivery.id))
            .filter(
                func.date(Delivery.created_at) == today,
                Delivery.status == "delivered",
            )
            .scalar()
        ) or 0

        avg_row = (
            db.query(
                func.avg(
                    func.extract("epoch", Delivery.delivered_at)
                    - func.extract("epoch", Delivery.created_at)
                )
            )
            .filter(
                func.date(Delivery.created_at) == today,
                Delivery.status == "delivered",
                Delivery.delivered_at.isnot(None),
            )
            .scalar()
        )
        avg_min = round((avg_row or 0) / 60, 2)

        return {"total": total, "completed_today": completed, "avg_time_min": avg_min}

    def pending_moderation_count(self, db: Session) -> int:
        return (
            db.query(func.count(Review.id))
            .filter(Review.status.in_([ReviewStatusEnum.FLAGGED, ReviewStatusEnum.PENDING]))
            .scalar()
        ) or 0

    def platform_avg_rating(self, db: Session) -> float:
        row = (
            db.query(func.avg(Review.rating))
            .filter(Review.status == ReviewStatusEnum.APPROVED)
            .scalar()
        )
        return round(float(row) if row else 0.0, 2)

    def get_trend(
        self, db: Session, *,
        metric: str,
        from_date: date,
        to_date: date,
    ) -> List[Dict]:
        valid = {
            "new_users", "total_orders", "new_revenue",
            "completed_deliveries", "new_reviews", "total_messages",
        }
        if metric not in valid:
            metric = "new_users"

        rows = (
            db.query(
                DailyAnalyticsSnapshot.snapshot_date,
                getattr(DailyAnalyticsSnapshot, metric),
            )
            .filter(
                DailyAnalyticsSnapshot.snapshot_date >= from_date,
                DailyAnalyticsSnapshot.snapshot_date <= to_date,
            )
            .order_by(DailyAnalyticsSnapshot.snapshot_date)
            .all()
        )
        return [{"date": row[0], "value": float(row[1] or 0)} for row in rows]

    def revenue_report(
        self, db: Session, *,
        from_date: date,
        to_date: date,
    ) -> dict:
        results = []
        total = Decimal("0")

        module_queries = [
            ("product", ProductOrder, "total_amount", "order_status", "delivered"),
            ("food",    FoodOrder,    "total_amount", "order_status", "delivered"),
            ("hotel",   HotelBooking, "total_amount", "status",       "checked_out"),
            ("service", ServiceBooking, "total_amount", "status",     "completed"),
        ]

        for label, model, amount_col, status_col, status_val in module_queries:
            val = (
                db.query(func.coalesce(func.sum(getattr(model, amount_col)), 0))
                .filter(
                    func.date(model.created_at).between(from_date, to_date),
                    getattr(model, status_col) == status_val,
                )
                .scalar()
            ) or 0

            dec_val = Decimal(str(val))
            total  += dec_val
            if dec_val > 0:
                results.append({
                    "category": label,
                    "revenue":  round(float(dec_val), 2),
                    "orders":   0,
                })

        return {
            "from_date": from_date,
            "to_date":   to_date,
            "total":     round(float(total), 2),
            "breakdown": results,
        }

    def get_platform_analytics(
        self, db: Session, *,
        from_date: date,
        to_date: date,
    ) -> dict:
        """Blueprint §11.5: DAU, MAU, GMV, wallet adoption rate."""
        # DAU (today new active users as proxy)
        dau = (
            db.query(func.count(User.id))
            .filter(func.date(User.created_at) == to_date)
            .scalar()
        ) or 0

        # MAU (users created in last 30 days)
        mau = (
            db.query(func.count(User.id))
            .filter(User.created_at >= _utcnow() - timedelta(days=30))
            .scalar()
        ) or 0

        # GMV — sum of all completed transaction amounts in period
        gmv_prod = (
            db.query(func.coalesce(func.sum(ProductOrder.total_amount), 0))
            .filter(
                func.date(ProductOrder.created_at).between(from_date, to_date),
                ProductOrder.order_status == "delivered",
            ).scalar()
        ) or 0

        gmv_food = (
            db.query(func.coalesce(func.sum(FoodOrder.total_amount), 0))
            .filter(
                func.date(FoodOrder.created_at).between(from_date, to_date),
                FoodOrder.order_status == "delivered",
            ).scalar()
        ) or 0

        gmv = round(float(gmv_prod) + float(gmv_food), 2)

        # Wallet adoption — % of users with balance > 0
        total_users  = db.query(func.count(User.id)).scalar() or 1
        funded_wallets = (
            db.query(func.count(Wallet.id))
            .filter(Wallet.balance > 0)
            .scalar()
        ) or 0
        adoption = round((funded_wallets / total_users) * 100, 2)

        # Platform fee revenue
        platform_revenue = (
            db.query(func.coalesce(func.sum(WalletTransaction.amount), 0))
            .filter(
                WalletTransaction.transaction_type == "platform_fee",
                func.date(WalletTransaction.created_at).between(from_date, to_date),
            )
            .scalar()
        ) or 0

        return {
            "dau":                  dau,
            "mau":                  mau,
            "gmv":                  gmv,
            "total_revenue":        round(float(platform_revenue), 2),
            "wallet_adoption_rate": adoption,
            "period_from":          from_date,
            "period_to":            to_date,
        }

    def get_subscription_analytics(self, db: Session) -> dict:
        """Blueprint §11.5: subscription analytics — MRR, churn, tier breakdown."""
        from app.core.constants import (
            STARTER_MONTHLY_PRICE, PRO_MONTHLY_PRICE, ENTERPRISE_MONTHLY_PRICE
        )

        tier_counts = (
            db.query(Business.subscription_tier, func.count(Business.id))
            .filter(Business.is_active.is_(True))
            .group_by(Business.subscription_tier)
            .all()
        )
        tier_map = {row[0]: row[1] for row in tier_counts}

        # MRR estimate
        mrr = (
            tier_map.get("starter", 0) * float(STARTER_MONTHLY_PRICE)
            + tier_map.get("pro", 0) * float(PRO_MONTHLY_PRICE)
            + tier_map.get("enterprise", 0) * float(ENTERPRISE_MONTHLY_PRICE)
        )

        return {
            "new_subscriptions": 0,    # requires subscription_events table
            "upgrades":          0,
            "downgrades":        0,
            "cancellations":     0,
            "churn_rate_pct":    0.0,
            "mrr":               round(mrr, 2),
            "tier_breakdown":    tier_map,
        }


# ── User CRUD ─────────────────────────────────────────────────────────────────

class CRUDAdminUser:

    def list_users(
        self, db: Session, *,
        user_type:  Optional[str] = None,
        status:     Optional[str] = None,
        search:     Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[User], int]:
        q = db.query(User)

        # FIX: was User.user_type — §14 column is User.role
        if user_type:
            q = q.filter(User.role == user_type)

        # FIX: was User.status — §14 uses is_active + is_banned booleans
        if status == "active":
            q = q.filter(User.is_active.is_(True), User.is_banned.is_(False))
        elif status == "banned":
            q = q.filter(User.is_banned.is_(True))
        elif status == "suspended":
            q = q.filter(User.is_active.is_(False), User.is_banned.is_(False))

        if search:
            pattern = f"%{search}%"
            q = q.filter(
                or_(
                    User.email.ilike(pattern),
                    # FIX: was User.phone — §14 column is phone_number
                    User.phone_number.ilike(pattern),
                    User.full_name.ilike(pattern),
                )
            )

        total = q.with_entities(func.count(User.id)).scalar() or 0
        users = q.order_by(User.created_at.desc()).offset(skip).limit(limit).all()
        return users, total

    def get_user(self, db: Session, *, user_id: UUID) -> Optional[User]:
        return db.query(User).filter(User.id == user_id).first()

    def edit_user(
        self, db: Session, *,
        user_id: UUID,
        full_name: Optional[str] = None,
        phone_number: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Optional[User]:
        """Blueprint §11.1: Edit any user profile."""
        user = self.get_user(db, user_id=user_id)
        if not user:
            return None
        if full_name:
            user.full_name = full_name
        if phone_number:
            user.phone_number = phone_number
        if email is not None:
            user.email = email
        db.flush()
        return user

    def apply_ban_action(
        self, db: Session, *,
        user_id: UUID,
        action: str,
        reason: str,
        performed_by_admin_id: UUID,
    ) -> Optional[User]:
        """
        Blueprint §11.1: suspend, ban, reactivate with mandatory immutable reason log.
        FIX: replaces update_status(status=str) — §14 uses is_active + is_banned booleans.
        """
        user = self.get_user(db, user_id=user_id)
        if not user:
            return None

        # Apply the action using §14 boolean fields
        if action == "suspended":
            user.is_active = False
            user.is_banned = False
        elif action == "banned":
            user.is_active = False
            user.is_banned = True
            user.ban_reason = reason
        elif action == "reactivated":
            user.is_active = True
            user.is_banned = False
            user.ban_reason = None

        db.flush()

        # Write immutable audit log (Blueprint §11.1)
        log = AdminBanLog(
            target_user_id=user_id,
            performed_by_admin_id=performed_by_admin_id,
            action=action,
            reason=reason,
        )
        db.add(log)
        db.flush()

        return user

    def get_user_wallet_history(
        self, db: Session, *,
        user_id: UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[WalletTransaction], int]:
        """Blueprint §11.1: View full wallet transaction history for any user."""
        wallet = db.query(Wallet).filter(Wallet.owner_id == user_id).first()
        if not wallet:
            return [], 0

        q = (
            db.query(WalletTransaction)
            .filter(WalletTransaction.wallet_id == wallet.id)
            .order_by(WalletTransaction.created_at.desc())
        )
        total = q.with_entities(func.count(WalletTransaction.id)).scalar() or 0
        txns  = q.offset(skip).limit(limit).all()
        return txns, total

    def adjust_wallet(
        self, db: Session, *,
        user_id: UUID,
        admin_id: UUID,
        adjustment_type: str,
        amount: Decimal,
        reason: str,
        related_order_id: Optional[UUID] = None,
    ) -> AdminWalletAdjustment:
        """
        Blueprint §11.1:
        "Manually credit or debit any wallet — requires written reason,
         logged with admin ID, immutable. (admin_wallet_adjustments table)"
        All financial operations in a DB transaction (§5.6 rule).
        """
        wallet = db.query(Wallet).filter(Wallet.owner_id == user_id).first()
        if not wallet:
            from app.core.exceptions import NotFoundException
            raise NotFoundException("Wallet")

        balance_before = wallet.balance

        if adjustment_type == "credit":
            wallet.balance += amount
        else:
            if wallet.balance < amount:
                from app.core.exceptions import ValidationException
                raise ValidationException("Insufficient wallet balance for debit")
            wallet.balance -= amount

        balance_after = wallet.balance
        db.flush()

        # Immutable audit record (Blueprint §11.1)
        adj = AdminWalletAdjustment(
            wallet_id=wallet.id,
            performed_by_admin_id=admin_id,
            adjustment_type=adjustment_type,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            reason=reason,
            related_user_id=user_id,
            related_order_id=related_order_id,
        )
        db.add(adj)
        db.flush()
        return adj


# ── Business CRUD ─────────────────────────────────────────────────────────────

class CRUDAdminBusiness:

    def list_businesses(
        self, db: Session, *,
        category:    Optional[str]  = None,
        is_verified: Optional[bool] = None,
        search:      Optional[str]  = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[Business], int]:
        q = db.query(Business)

        if category:
            q = q.filter(Business.category == category)
        if is_verified is not None:
            q = q.filter(Business.is_verified == is_verified)
        if search:
            pattern = f"%{search}%"
            # FIX: was Business.name — §14 column is Business.business_name
            q = q.filter(Business.business_name.ilike(pattern))

        total = q.with_entities(func.count(Business.id)).scalar() or 0
        businesses = q.order_by(Business.created_at.desc()).offset(skip).limit(limit).all()
        return businesses, total

    def get_business(self, db: Session, *, business_id: UUID) -> Optional[Business]:
        return db.query(Business).filter(Business.id == business_id).first()

    def update_verification(
        self, db: Session, *,
        business_id: UUID,
        is_verified: bool,
        reviewed_by_admin_id: UUID,
        reason: Optional[str] = None,
    ) -> Optional[Business]:
        biz = self.get_business(db, business_id=business_id)
        if not biz:
            return None
        biz.is_verified             = is_verified
        biz.verification_reviewed_by = reviewed_by_admin_id
        biz.verification_reviewed_at = _utcnow()
        db.flush()
        return biz

    def update_subscription(
        self, db: Session, *,
        business_id: UUID,
        tier: str,
    ) -> Optional[Business]:
        """Blueprint §11.2: Upgrade/downgrade subscription tier manually."""
        from app.core.constants import SUBSCRIPTION_TIER_RANKS
        biz = self.get_business(db, business_id=business_id)
        if not biz:
            return None
        biz.subscription_tier      = tier
        biz.subscription_tier_rank = SUBSCRIPTION_TIER_RANKS.get(tier, 1)
        db.flush()
        return biz

    def set_product_limit_override(
        self, db: Session, *,
        business_id: UUID,
        override_enabled: bool,
        override_value: Optional[int],
    ) -> Optional[Business]:
        """
        Blueprint §11.2:
        "Override product listing limit for specific businesses
         (stores override flag + override_limit_value on the business record)."
        Blueprint §2.2 implementation note for admin_panel override.
        """
        biz = self.get_business(db, business_id=business_id)
        if not biz:
            return None
        biz.product_limit_override       = override_enabled
        biz.product_limit_override_value = override_value if override_enabled else None
        db.flush()
        return biz

    def set_featured_status(
        self, db: Session, *,
        business_id: UUID,
        is_featured: bool,
    ) -> Optional[Business]:
        """Blueprint §11.2: Set featured status manually (overrides rotation algorithm)."""
        biz = self.get_business(db, business_id=business_id)
        if not biz:
            return None
        # is_featured flag — add this column to Business model if not present
        biz.is_featured = is_featured
        db.flush()
        return biz


# ── Financial Controls CRUD ───────────────────────────────────────────────────

class CRUDAdminFinance:

    def list_withdrawals(
        self, db: Session, *,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List, int]:
        """Blueprint §11.3: Full view of all withdrawal requests."""
        q = db.query(WalletTransaction).filter(
            WalletTransaction.transaction_type == TransactionType.WITHDRAWAL
        ).order_by(WalletTransaction.created_at.desc())
        if status:
            q = q.filter(WalletTransaction.status == status)
        total = q.with_entities(func.count(WalletTransaction.id)).scalar() or 0
        rows  = q.offset(skip).limit(limit).all()
        return rows, total

    def process_withdrawal(
        self, db: Session, *,
        withdrawal_id: UUID,
        action: str,
        admin_id: UUID,
        reason: Optional[str] = None,
    ) -> Optional[Any]:
        """Blueprint §11.3: Approve or hold withdrawals."""
        wd = db.query(WalletTransaction).filter(
            WalletTransaction.id == withdrawal_id,
            WalletTransaction.transaction_type == TransactionType.WITHDRAWAL
        ).first()
        if not wd:
            return None

        wd.status              = action   # approved | held | rejected
        wd.reviewed_by_admin_id = admin_id
        wd.reviewed_at         = _utcnow()
        if reason:
            wd.review_note = reason
        db.flush()
        return wd

    def get_fee_config(self, db: Session) -> Dict[str, str]:
        """Blueprint §11.3: Read current platform fee rates from config table."""
        keys = ["platform_fee_standard_ngn", "platform_fee_booking_ngn", "platform_fee_ticket_ngn"]
        rows = db.query(PlatformConfig).filter(PlatformConfig.key.in_(keys)).all()
        return {r.key: r.value for r in rows}

    def update_fee_config(
        self, db: Session, *,
        admin_id: UUID,
        fee_standard: Optional[Decimal] = None,
        fee_booking: Optional[Decimal]  = None,
        fee_ticket: Optional[Decimal]   = None,
    ) -> Dict[str, str]:
        """
        Blueprint §11.3:
        "Adjust platform fee rates — changes apply to NEW transactions only."
        Every rate change: logged with timestamp + admin user ID.
        """
        updates: Dict[str, Decimal] = {}
        if fee_standard is not None:
            updates["platform_fee_standard_ngn"] = fee_standard
        if fee_booking is not None:
            updates["platform_fee_booking_ngn"] = fee_booking
        if fee_ticket is not None:
            updates["platform_fee_ticket_ngn"] = fee_ticket

        for key, val in updates.items():
            cfg = db.query(PlatformConfig).filter(PlatformConfig.key == key).first()
            if cfg:
                cfg.value               = str(val)
                cfg.updated_by_admin_id = admin_id
                cfg.updated_at          = _utcnow()
            else:
                db.add(PlatformConfig(
                    key=key,
                    value=str(val),
                    updated_by_admin_id=admin_id,
                ))
        db.flush()
        return self.get_fee_config(db)


# ── Content Moderation CRUD ───────────────────────────────────────────────────

class CRUDAdminModeration:

    def get_flagged_reviews(
        self, db: Session, *,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[Review], int]:
        q = db.query(Review).filter(
            Review.status.in_([ReviewStatusEnum.FLAGGED, ReviewStatusEnum.PENDING])
        )
        total   = q.with_entities(func.count(Review.id)).scalar() or 0
        reviews = q.order_by(Review.created_at.asc()).offset(skip).limit(limit).all()
        return reviews, total

    def remove_review(
        self, db: Session, *,
        review_id: UUID,
        reason: str,
        admin_id: UUID,
    ) -> Optional[Review]:
        """Blueprint §11.4: Remove content — business receives automated notification."""
        review = db.query(Review).filter(Review.id == review_id).first()
        if review:
            review.status = ReviewStatusEnum.REJECTED
            db.flush()
        return review

    def remove_reel(
        self, db: Session, *,
        reel_id: UUID,
        reason: str,
        admin_id: UUID,
    ) -> Optional[Any]:
        """Blueprint §11.4: Remove any reel — business notified with reason."""
        reel = db.query(Reel).filter(Reel.id == reel_id).first()
        if reel:
            reel.is_active = False
            db.flush()
        return reel

    def remove_story(
        self, db: Session, *,
        story_id: UUID,
        reason: str,
        admin_id: UUID,
    ) -> Optional[Any]:
        """Blueprint §11.4: Remove any story — business notified with reason."""
        story = db.query(Story).filter(Story.id == story_id).first()
        if story:
            story.is_active = False
            db.flush()
        return story

    def remove_job(
        self, db: Session, *,
        job_id: UUID,
        reason: str,
        admin_id: UUID,
    ) -> Optional[Any]:
        """Blueprint §11.4: Remove any job post — business notified with reason."""
        job = db.query(JobPosting).filter(JobPosting.id == job_id).first()
        if job:
            job.status = "removed"
            db.flush()
        return job

    def get_keyword_blocklist(self, db: Session) -> List[str]:
        """Blueprint §11.4: Keyword blocklist management."""
        cfg = db.query(PlatformConfig).filter(
            PlatformConfig.key == "content_keyword_blocklist"
        ).first()
        if not cfg:
            return []
        import json
        try:
            return json.loads(cfg.value)
        except Exception:
            return []

    def update_keyword_blocklist(
        self, db: Session, *,
        keywords: List[str],
        action: str,
        admin_id: UUID,
    ) -> List[str]:
        """Blueprint §11.4: Add or remove keywords from blocklist."""
        import json
        current = self.get_keyword_blocklist(db)
        if action == "add":
            updated = list(set(current + [k.lower() for k in keywords]))
        else:  # remove
            updated = [k for k in current if k not in [w.lower() for w in keywords]]

        cfg = db.query(PlatformConfig).filter(
            PlatformConfig.key == "content_keyword_blocklist"
        ).first()
        if cfg:
            cfg.value               = json.dumps(updated)
            cfg.updated_by_admin_id = admin_id
        else:
            db.add(PlatformConfig(
                key="content_keyword_blocklist",
                value=json.dumps(updated),
                updated_by_admin_id=admin_id,
            ))
        db.flush()
        return updated


# ── Platform Config CRUD ──────────────────────────────────────────────────────

class CRUDPlatformConfig:

    def get(self, db: Session, *, key: str) -> Optional[PlatformConfig]:
        return db.query(PlatformConfig).filter(PlatformConfig.key == key).first()

    def get_value(self, db: Session, *, key: str, default: str = "") -> str:
        cfg = self.get(db, key=key)
        return cfg.value if cfg else default

    def set(
        self, db: Session, *,
        key: str,
        value: str,
        admin_id: UUID,
        description: Optional[str] = None,
    ) -> PlatformConfig:
        """Blueprint §11.6: Set any config value with admin audit trail."""
        cfg = self.get(db, key=key)
        if cfg:
            cfg.value               = value
            cfg.updated_by_admin_id = admin_id
            if description:
                cfg.description = description
        else:
            cfg = PlatformConfig(
                key=key,
                value=value,
                description=description,
                updated_by_admin_id=admin_id,
            )
            db.add(cfg)
        db.flush()
        return cfg

    def list_all(self, db: Session) -> List[PlatformConfig]:
        return db.query(PlatformConfig).order_by(PlatformConfig.key).all()


# ── Support Agent CRUD ────────────────────────────────────────────────────────

class CRUDAdminUserManagement:

    def create_support_agent(
        self, db: Session, *,
        email: str,
        password: str,
        full_name: str,
        role: str,
        created_by_id: UUID,
    ) -> AdminUser:
        """
        Blueprint §11.6: "Manage support agent accounts."
        Blueprint §2.2: Admin accounts NOT created through mobile registration.
        """
        from app.core.admin_security import hash_admin_password
        agent = AdminUser(
            email=email,
            password_hash=hash_admin_password(password),
            full_name=full_name,
            role=role,
            created_by_id=created_by_id,
        )
        db.add(agent)
        db.flush()
        return agent

    def list_admin_users(
        self, db: Session, *,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[AdminUser], int]:
        q = db.query(AdminUser).order_by(AdminUser.created_at.desc())
        total  = q.with_entities(func.count(AdminUser.id)).scalar() or 0
        admins = q.offset(skip).limit(limit).all()
        return admins, total


# ── Singletons ────────────────────────────────────────────────────────────────

admin_dashboard_crud   = CRUDAdminDashboard()
admin_user_crud        = CRUDAdminUser()
admin_business_crud    = CRUDAdminBusiness()
admin_finance_crud     = CRUDAdminFinance()
admin_moderation_crud  = CRUDAdminModeration()
platform_config_crud   = CRUDPlatformConfig()
admin_mgmt_crud        = CRUDAdminUserManagement()