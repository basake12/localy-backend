"""
app/services/admin_service.py

Orchestration layer between admin API routes and CRUD.
All validation, business logic, and Celery task dispatch live here.

Blueprint §11.1 — User Management
Blueprint §11.2 — Business Management
Blueprint §11.3 — Financial Controls
Blueprint §11.4 — Content Moderation
Blueprint §11.5 — Analytics & Reporting
Blueprint §11.6 — Configuration Panel

FIXES vs previous version:
  1. update_status() replaced with apply_ban_action() — uses is_active + is_banned
     booleans (§14), not a status enum.
  2. Mandatory reason validation on every ban/suspend action.
  3. admin_wallet_adjustments written atomically for every wallet adjust.
  4. Subscription management service added (§11.2).
  5. Financial controls service added (§11.3).
  6. Content moderation actions added (§11.4).
  7. Platform config service added (§11.6).
  8. Analytics service expanded (§11.5).
  9. Push notification service added (§11.6).
  10. All datetime.now(timezone.utc) — §16.4 HARD RULE.
"""

import logging
from decimal import Decimal
from datetime import date, datetime, timezone
from typing import Optional, List, Dict, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.crud.admin_crud import (
    admin_dashboard_crud,
    admin_user_crud,
    admin_business_crud,
    admin_finance_crud,
    admin_moderation_crud,
    platform_config_crud,
    admin_mgmt_crud,
)
from app.core.exceptions import NotFoundException, ValidationException

logger = logging.getLogger(__name__)


# ── Dashboard ─────────────────────────────────────────────────────────────────

class AdminDashboardService:

    _VALID_METRICS = {
        "new_users", "total_orders", "new_revenue",
        "completed_deliveries", "new_reviews", "total_messages",
    }

    def get_overview(self, db: Session) -> dict:
        users      = admin_dashboard_crud.user_summary(db)
        orders     = admin_dashboard_crud.order_summary(db)
        deliveries = admin_dashboard_crud.delivery_summary(db)
        pending    = admin_dashboard_crud.pending_moderation_count(db)
        avg_rating = admin_dashboard_crud.platform_avg_rating(db)
        return {
            "users":               users,
            "orders":              orders,
            "deliveries":          deliveries,
            "avg_platform_rating": avg_rating,
            "pending_reviews":     pending,
            "unread_flags":        pending,
        }

    def get_trend(
        self, db: Session, *,
        metric: str,
        from_date: date,
        to_date: date,
    ) -> dict:
        if metric not in self._VALID_METRICS:
            raise ValidationException(
                f"Invalid metric. Choose from: {', '.join(sorted(self._VALID_METRICS))}"
            )
        if from_date > to_date:
            raise ValidationException("from_date must be <= to_date")
        if (to_date - from_date).days > 365:
            raise ValidationException("Date range cannot exceed 365 days")
        points = admin_dashboard_crud.get_trend(
            db, metric=metric, from_date=from_date, to_date=to_date
        )
        return {"metric": metric, "points": points}

    def get_revenue_report(
        self, db: Session, *,
        from_date: date,
        to_date: date,
    ) -> dict:
        if from_date > to_date:
            raise ValidationException("from_date must be <= to_date")
        if (to_date - from_date).days > 365:
            raise ValidationException("Date range cannot exceed 365 days")
        return admin_dashboard_crud.revenue_report(
            db, from_date=from_date, to_date=to_date
        )

    def get_platform_analytics(
        self, db: Session, *,
        from_date: date,
        to_date: date,
    ) -> dict:
        """Blueprint §11.5: DAU/MAU/GMV/wallet adoption."""
        if from_date > to_date:
            raise ValidationException("from_date must be <= to_date")
        return admin_dashboard_crud.get_platform_analytics(
            db, from_date=from_date, to_date=to_date
        )

    def get_subscription_analytics(self, db: Session) -> dict:
        """Blueprint §11.5: subscription analytics — MRR, churn, tier breakdown."""
        return admin_dashboard_crud.get_subscription_analytics(db)


# ── User Management ───────────────────────────────────────────────────────────

class AdminUserService:

    def list_users(
        self, db: Session, *,
        user_type:  Optional[str] = None,
        status:     Optional[str] = None,
        search:     Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> dict:
        users, total = admin_user_crud.list_users(
            db,
            user_type=user_type,
            status=status,
            search=search,
            skip=skip,
            limit=limit,
        )
        return {"users": users, "total": total, "skip": skip, "limit": limit}

    def edit_user(
        self, db: Session, *,
        user_id: UUID,
        full_name: Optional[str] = None,
        phone_number: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Any:
        """Blueprint §11.1: Edit any user profile."""
        user = admin_user_crud.edit_user(
            db,
            user_id=user_id,
            full_name=full_name,
            phone_number=phone_number,
            email=email,
        )
        if not user:
            raise NotFoundException("User")
        db.commit()
        db.refresh(user)
        return user

    def apply_ban_action(
        self, db: Session, *,
        user_id: UUID,
        action: str,
        reason: str,
        performed_by_admin_id: UUID,
    ) -> Any:
        """
        Blueprint §11.1:
        "Suspend, ban, or delete account — mandatory reason log (immutable)"
        FIX: was update_status(status=str) which used a status enum not in §14.
        """
        valid_actions = {"suspended", "banned", "reactivated"}
        if action not in valid_actions:
            raise ValidationException(
                f"Invalid action. Choose from: {', '.join(sorted(valid_actions))}"
            )
        if not reason or not reason.strip():
            raise ValidationException("Reason is mandatory and cannot be empty.")

        user = admin_user_crud.apply_ban_action(
            db,
            user_id=user_id,
            action=action,
            reason=reason.strip(),
            performed_by_admin_id=performed_by_admin_id,
        )
        if not user:
            raise NotFoundException("User")

        db.commit()
        db.refresh(user)
        return user

    def get_wallet_history(
        self, db: Session, *,
        user_id: UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> dict:
        """Blueprint §11.1: View full wallet transaction history for any user."""
        txns, total = admin_user_crud.get_user_wallet_history(
            db, user_id=user_id, skip=skip, limit=limit
        )
        return {"transactions": txns, "total": total, "skip": skip, "limit": limit}

    def adjust_wallet(
        self, db: Session, *,
        user_id: UUID,
        admin_id: UUID,
        adjustment_type: str,
        amount: Decimal,
        reason: str,
        related_order_id: Optional[UUID] = None,
    ) -> Any:
        """
        Blueprint §11.1:
        "Manually credit or debit any wallet — requires written reason,
         logged with admin ID, immutable. (admin_wallet_adjustments table)"
        §5.6: all financial operations in DB transactions.
        """
        valid_types = {"credit", "debit"}
        if adjustment_type not in valid_types:
            raise ValidationException("adjustment_type must be 'credit' or 'debit'")
        if amount <= 0:
            raise ValidationException("Amount must be greater than 0")
        if not reason or not reason.strip():
            raise ValidationException("Reason is mandatory for wallet adjustments.")

        adj = admin_user_crud.adjust_wallet(
            db,
            user_id=user_id,
            admin_id=admin_id,
            adjustment_type=adjustment_type,
            amount=amount,
            reason=reason.strip(),
            related_order_id=related_order_id,
        )
        db.commit()
        db.refresh(adj)
        return adj


# ── Business Management ───────────────────────────────────────────────────────

class AdminBusinessService:

    def list_businesses(
        self, db: Session, *,
        category:    Optional[str]  = None,
        is_verified: Optional[bool] = None,
        search:      Optional[str]  = None,
        skip: int = 0,
        limit: int = 50,
    ) -> dict:
        businesses, total = admin_business_crud.list_businesses(
            db,
            category=category,
            is_verified=is_verified,
            search=search,
            skip=skip,
            limit=limit,
        )
        return {"businesses": businesses, "total": total, "skip": skip, "limit": limit}

    def verify_business(
        self, db: Session, *,
        business_id: UUID,
        is_verified: bool,
        admin_id: UUID,
        reason: Optional[str] = None,
    ) -> Any:
        """Blueprint §11.2: Verify or reject business registration."""
        if not is_verified and not reason:
            raise ValidationException("A reason is required when rejecting a business.")

        biz = admin_business_crud.update_verification(
            db,
            business_id=business_id,
            is_verified=is_verified,
            reviewed_by_admin_id=admin_id,
            reason=reason,
        )
        if not biz:
            raise NotFoundException("Business")
        db.commit()
        db.refresh(biz)
        return biz

    def update_subscription(
        self, db: Session, *,
        business_id: UUID,
        tier: str,
        reason: str,
    ) -> Any:
        """Blueprint §11.2: Upgrade/downgrade subscription tier manually."""
        valid_tiers = {"free", "starter", "pro", "enterprise"}
        if tier not in valid_tiers:
            raise ValidationException(
                f"Invalid tier. Choose from: {', '.join(sorted(valid_tiers))}"
            )
        biz = admin_business_crud.update_subscription(db, business_id=business_id, tier=tier)
        if not biz:
            raise NotFoundException("Business")
        db.commit()
        db.refresh(biz)
        return biz

    def set_product_limit_override(
        self, db: Session, *,
        business_id: UUID,
        override_enabled: bool,
        override_value: Optional[int],
    ) -> Any:
        """
        Blueprint §11.2:
        "Override product listing limit for specific businesses
         (set product_limit_override=TRUE, product_limit_override_value=N)"
        """
        if override_enabled and not override_value:
            raise ValidationException("override_value is required when override is enabled.")
        biz = admin_business_crud.set_product_limit_override(
            db,
            business_id=business_id,
            override_enabled=override_enabled,
            override_value=override_value,
        )
        if not biz:
            raise NotFoundException("Business")
        db.commit()
        db.refresh(biz)
        return biz

    def set_featured_status(
        self, db: Session, *,
        business_id: UUID,
        is_featured: bool,
    ) -> Any:
        """Blueprint §11.2: Set featured status manually."""
        biz = admin_business_crud.set_featured_status(
            db, business_id=business_id, is_featured=is_featured
        )
        if not biz:
            raise NotFoundException("Business")
        db.commit()
        db.refresh(biz)
        return biz


# ── Financial Controls ────────────────────────────────────────────────────────

class AdminFinanceService:

    def list_withdrawals(
        self, db: Session, *,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> dict:
        """Blueprint §11.3: Full view of all withdrawal requests."""
        wds, total = admin_finance_crud.list_withdrawals(
            db, status=status, skip=skip, limit=limit
        )
        return {"withdrawals": wds, "total": total, "skip": skip, "limit": limit}

    def process_withdrawal(
        self, db: Session, *,
        withdrawal_id: UUID,
        action: str,
        admin_id: UUID,
        reason: Optional[str] = None,
    ) -> Any:
        """Blueprint §11.3: Approve or hold withdrawals."""
        valid_actions = {"approve", "hold", "reject"}
        if action not in valid_actions:
            raise ValidationException(f"Action must be one of: {', '.join(valid_actions)}")
        if action in {"hold", "reject"} and not reason:
            raise ValidationException(f"Reason is required for '{action}' action.")

        wd = admin_finance_crud.process_withdrawal(
            db,
            withdrawal_id=withdrawal_id,
            action=action,
            admin_id=admin_id,
            reason=reason,
        )
        if not wd:
            raise NotFoundException("Withdrawal")
        db.commit()
        return wd

    def get_fee_config(self, db: Session) -> dict:
        """Blueprint §11.3: Read current platform fee rates."""
        return admin_finance_crud.get_fee_config(db)

    def update_fee_config(
        self, db: Session, *,
        admin_id: UUID,
        fee_standard: Optional[Decimal] = None,
        fee_booking: Optional[Decimal]  = None,
        fee_ticket: Optional[Decimal]   = None,
        reason: str = "",
    ) -> dict:
        """
        Blueprint §11.3:
        "Adjust platform fee rates — changes apply to NEW transactions only.
         Every rate change: logged with timestamp + admin user ID."
        Blueprint §15: POST /admin/config/fees
        """
        if not reason:
            raise ValidationException("Reason is required for fee rate changes.")

        config = admin_finance_crud.update_fee_config(
            db,
            admin_id=admin_id,
            fee_standard=fee_standard,
            fee_booking=fee_booking,
            fee_ticket=fee_ticket,
        )
        db.commit()
        logger.info(
            "Fee config updated by admin=%s: standard=%s booking=%s ticket=%s reason=%s",
            admin_id, fee_standard, fee_booking, fee_ticket, reason,
        )
        return config


# ── Content Moderation ────────────────────────────────────────────────────────

class AdminModerationService:

    def get_queue(
        self, db: Session, *,
        skip: int = 0,
        limit: int = 50,
    ) -> dict:
        reviews, total = admin_moderation_crud.get_flagged_reviews(
            db, skip=skip, limit=limit
        )
        items = [
            {
                "review_id":       r.id,
                "reviewer_id":     r.reviewer_id,
                "reviewable_type": r.reviewable_type.value if hasattr(r.reviewable_type, "value") else r.reviewable_type,
                "reviewable_id":   r.reviewable_id,
                "rating":          r.rating,
                "title":           r.title,
                "body":            r.body,
                "flag_reason":     r.flag_reason,
                "status":          r.status.value if hasattr(r.status, "value") else r.status,
                "created_at":      r.created_at,
            }
            for r in reviews
        ]
        return {"items": items, "total": total, "skip": skip, "limit": limit}

    def remove_content(
        self, db: Session, *,
        content_type: str,
        content_id: UUID,
        reason: str,
        admin_id: UUID,
        notify_business: bool = True,
    ) -> dict:
        """
        Blueprint §11.4:
        "Remove any content — business receives automated notification with reason."
        content_type: review | reel | story | job
        """
        if not reason or len(reason.strip()) < 10:
            raise ValidationException("Reason must be at least 10 characters.")

        handlers = {
            "review": admin_moderation_crud.remove_review,
            "reel":   admin_moderation_crud.remove_reel,
            "story":  admin_moderation_crud.remove_story,
            "job":    admin_moderation_crud.remove_job,
        }

        handler = handlers.get(content_type)
        if not handler:
            raise ValidationException(
                f"Invalid content_type. Choose from: {', '.join(handlers.keys())}"
            )

        result = handler(db, **{
            f"{content_type}_id": content_id,
            "reason": reason.strip(),
            "admin_id": admin_id,
        })
        if not result:
            raise NotFoundException(content_type.capitalize())

        db.commit()

        # TODO: dispatch Celery task to notify business via push + in-app message
        if notify_business:
            logger.info(
                "Content removed: type=%s id=%s admin=%s reason=%s",
                content_type, content_id, admin_id, reason[:50],
            )

        return {"removed": True, "content_type": content_type, "content_id": str(content_id)}

    def update_keyword_blocklist(
        self, db: Session, *,
        keywords: List[str],
        action: str,
        admin_id: UUID,
    ) -> List[str]:
        """Blueprint §11.4: Keyword blocklist management."""
        result = admin_moderation_crud.update_keyword_blocklist(
            db, keywords=keywords, action=action, admin_id=admin_id
        )
        db.commit()
        return result


# ── Platform Configuration ────────────────────────────────────────────────────

class AdminConfigService:

    def set_feature_flag(
        self, db: Session, *,
        key: str,
        enabled: bool,
        admin_id: UUID,
    ) -> dict:
        """
        Blueprint §11.6:
        "Feature flag toggles: enable/disable any module or feature (no code deploy)"
        """
        # Prefix guard — feature flags must start with feature_flag_
        if not key.startswith("feature_flag_"):
            key = f"feature_flag_{key}"

        cfg = platform_config_crud.set(
            db,
            key=key,
            value="true" if enabled else "false",
            admin_id=admin_id,
            description=f"Feature flag: {key}",
        )
        db.commit()
        return {"key": cfg.key, "value": cfg.value, "updated_at": cfg.updated_at}

    def update_terms(
        self, db: Session, *,
        content: str,
        version: str,
        admin_id: UUID,
    ) -> dict:
        """
        Blueprint §11.6 + §15: PATCH /admin/content/terms
        "T&C and Privacy Policy: admin edits via rich text editor;
         mobile always fetches latest version."
        Blueprint §3.1 step 8: "Never cached locally beyond TTL."
        """
        platform_config_crud.set(db, key="terms_and_conditions_text", value=content, admin_id=admin_id)
        platform_config_crud.set(db, key="terms_version", value=version, admin_id=admin_id)
        db.commit()
        return {"version": version, "updated": True}

    def update_privacy_policy(
        self, db: Session, *,
        content: str,
        version: str,
        admin_id: UUID,
    ) -> dict:
        platform_config_crud.set(db, key="privacy_policy_text", value=content, admin_id=admin_id)
        platform_config_crud.set(db, key="privacy_policy_version", value=version, admin_id=admin_id)
        db.commit()
        return {"version": version, "updated": True}

    def get_terms(self, db: Session) -> dict:
        """Blueprint §3.1 step 8: Mobile fetches this on every load."""
        return {
            "content": platform_config_crud.get_value(db, key="terms_and_conditions_text"),
            "version": platform_config_crud.get_value(db, key="terms_version", default="v1.0"),
        }

    def get_privacy_policy(self, db: Session) -> dict:
        return {
            "content": platform_config_crud.get_value(db, key="privacy_policy_text"),
            "version": platform_config_crud.get_value(db, key="privacy_policy_version", default="v1.0"),
        }

    def send_push_notification(
        self, db: Session, *,
        title: str,
        body: str,
        segment: Optional[str] = None,
        user_id: Optional[UUID] = None,
        admin_id: UUID,
    ) -> dict:
        """
        Blueprint §11.6 + §15: POST /admin/push-notifications
        "Push notifications: to all users, a segment (e.g. all Pro businesses in Lagos),
         or a specific user."
        Dispatches a Celery task for the actual FCM delivery.
        """
        from app.tasks.celery_tasks import send_push_notification  # Celery task

        task_payload = {
            "title":    title,
            "body":     body,
            "segment":  segment,
            "user_id":  str(user_id) if user_id else None,
            "admin_id": str(admin_id),
        }

        # Celery task handles FCM delivery + targeting logic
        send_push_notification.delay(**task_payload)

        logger.info(
            "Push notification queued: admin=%s segment=%s user_id=%s",
            admin_id, segment, user_id,
        )
        return {"queued": True, "target": segment or str(user_id)}

    def get_all_config(self, db: Session) -> List[dict]:
        """Blueprint §11.6: List all platform config values."""
        configs = platform_config_crud.list_all(db)
        return [
            {
                "key": c.key,
                "value": c.value,
                "description": c.description,
                "updated_at": c.updated_at,
            }
            for c in configs
        ]

    def set_config_value(
        self, db: Session, *,
        key: str,
        value: str,
        admin_id: UUID,
        description: Optional[str] = None,
    ) -> dict:
        """Generic config key-value setter for Blueprint §11.6 configuration panel."""
        cfg = platform_config_crud.set(
            db, key=key, value=value, admin_id=admin_id, description=description
        )
        db.commit()
        return {"key": cfg.key, "value": cfg.value}

    def create_support_agent(
        self, db: Session, *,
        email: str,
        password: str,
        full_name: str,
        role: str,
        created_by_id: UUID,
    ) -> Any:
        """Blueprint §11.6: Manage support agent accounts."""
        valid_roles = {"support_agent", "admin"}
        if role not in valid_roles:
            raise ValidationException(f"Role must be one of: {', '.join(valid_roles)}")

        existing = db.query(__import__("app.models.admin_model", fromlist=["AdminUser"]).AdminUser).filter(
            __import__("app.models.admin_model", fromlist=["AdminUser"]).AdminUser.email == email
        ).first()
        if existing:
            raise ValidationException("An admin with this email already exists.")

        agent = admin_mgmt_crud.create_support_agent(
            db,
            email=email,
            password=password,
            full_name=full_name,
            role=role,
            created_by_id=created_by_id,
        )
        db.commit()
        db.refresh(agent)
        return agent


# ── Singletons ────────────────────────────────────────────────────────────────

admin_dashboard_service  = AdminDashboardService()
admin_user_service       = AdminUserService()
admin_business_service   = AdminBusinessService()
admin_finance_service    = AdminFinanceService()
admin_moderation_service = AdminModerationService()
admin_config_service     = AdminConfigService()