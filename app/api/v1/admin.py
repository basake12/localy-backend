"""
app/routers/admin.py

Blueprint §11 — Admin Web Application
Blueprint §15 — API Endpoint Reference (admin routes)

Every route requires: Depends(require_admin) from app.core.admin_deps
Admin JWT uses JWT_ADMIN_SECRET_KEY — NEVER mobile JWT_SECRET_KEY.
Blueprint §2.2 HARD RULE: admin exists ONLY as web application.
Blueprint §3.2: admin tokens NEVER accepted by mobile API endpoints.

Mounted at: /api/v1/admin/ by the main router
Admin panel hosted at: admin.localy.ng (Blueprint §13.3)

FIXES vs previous version:
  1. require_admin imported from app.core.admin_deps (NOT app.dependencies).
     Previous import from app.dependencies caused startup ImportError —
     require_admin was deliberately deleted from there (§2.2 HARD RULE).

  2. AdminUser used as the admin identity type (NOT mobile User model).

  3. All §11.1 endpoints implemented:
     PATCH /admin/users/{id}               - edit profile
     POST  /admin/users/{id}/ban           - ban/suspend/reactivate with reason
     POST  /admin/users/{id}/wallet/adjust - manual credit/debit (immutable log)
     GET   /admin/users/{id}/wallet        - wallet history

  4. All §11.2 endpoints implemented:
     POST  /admin/businesses/{id}/subscription   - tier management
     PATCH /admin/businesses/{id}/product-limit  - product limit override
     POST  /admin/businesses/{id}/featured       - featured status

  5. All §11.3 endpoints implemented:
     GET   /admin/withdrawals/             - withdrawal queue
     POST  /admin/withdrawals/{id}/action  - approve/hold/reject
     GET   /admin/config/fees             - fee config
     POST  /admin/config/fees             - update fees

  6. All §11.4 endpoints implemented:
     POST  /admin/content/{type}/{id}/remove  - remove content with reason
     POST  /admin/content/blocklist           - keyword blocklist management

  7. All §11.5 endpoints implemented:
     GET   /admin/analytics/platform      - DAU/MAU/GMV
     GET   /admin/analytics/subscriptions - MRR/churn/tier breakdown

  8. All §11.6 endpoints implemented:
     POST  /admin/config/features         - feature flags
     PATCH /admin/content/terms           - T&C update
     PATCH /admin/content/privacy-policy  - Privacy Policy update
     POST  /admin/push-notifications      - push to segment/user
     GET   /admin/config                  - all config values
     PATCH /admin/config/{key}            - set config value
     POST  /admin/support-agents          - create support agent account
     GET   /admin/support-agents          - list support agents
"""

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.core.database  import get_db
from app.core.admin_deps import require_admin, require_super_admin
from app.dependencies   import get_pagination_params
from app.models.admin_model import AdminUser

from app.schemas.admin_schema import (
    # Dashboard
    DashboardOverview, TrendResponse, RevenueReport,
    # §11.1 Users
    AdminUserListOut, UserEditRequest, UserBanRequest,
    WalletAdjustmentRequest, WalletAdjustmentOut,
    # §11.2 Businesses
    AdminBusinessListOut, BusinessVerifyUpdate,
    SubscriptionUpdateRequest, ProductLimitOverrideRequest, FeaturedStatusRequest,
    # §11.3 Finance
    WithdrawalListOut, WithdrawalActionRequest, PlatformFeeConfig,
    CouponCreateRequest, PromotionCreateRequest, ReferralConfigRequest,
    # §11.4 Content
    ModerationQueueOut, ContentRemoveRequest, KeywordBlocklistRequest,
    # §11.5 Analytics
    PlatformAnalytics, SubscriptionAnalytics,
    # §11.6 Config
    FeatureFlagRequest, TermsUpdateRequest, PrivacyPolicyUpdateRequest,
    PushNotificationRequest, ConfigValueRequest, ConfigValueOut,
    SupportAgentCreateRequest, AdminUserProfileOut,
)

from app.services.admin_service import (
    admin_dashboard_service,
    admin_user_service,
    admin_business_service,
    admin_finance_service,
    admin_moderation_service,
    admin_config_service,
)

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════════
# §11.5 DASHBOARD & ANALYTICS
# Blueprint §15: GET /admin/analytics/platform
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/dashboard", response_model=DashboardOverview)
def get_dashboard(
    db:    Session   = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
):
    """Live KPI cards: users, orders, deliveries, ratings, moderation flags."""
    return admin_dashboard_service.get_overview(db)


@router.get("/trends", response_model=TrendResponse)
def get_trend(
    metric:    str  = Query(...),
    from_date: date = Query(...),
    to_date:   date = Query(...),
    db:        Session   = Depends(get_db),
    admin:     AdminUser = Depends(require_admin),
):
    """Time-series chart data from analytics snapshot table."""
    return admin_dashboard_service.get_trend(
        db, metric=metric, from_date=from_date, to_date=to_date
    )


@router.get("/analytics/revenue", response_model=RevenueReport)
def get_revenue_report(
    from_date: date = Query(...),
    to_date:   date = Query(...),
    db:        Session   = Depends(get_db),
    admin:     AdminUser = Depends(require_admin),
):
    """Revenue breakdown by module and date range."""
    return admin_dashboard_service.get_revenue_report(
        db, from_date=from_date, to_date=to_date
    )


@router.get("/analytics/platform", response_model=PlatformAnalytics)
def get_platform_analytics(
    from_date: date = Query(...),
    to_date:   date = Query(...),
    db:        Session   = Depends(get_db),
    admin:     AdminUser = Depends(require_admin),
):
    """
    Blueprint §11.5: DAU, MAU, GMV, total revenue, wallet adoption rate.
    Blueprint §15: GET /admin/analytics/platform
    """
    return admin_dashboard_service.get_platform_analytics(
        db, from_date=from_date, to_date=to_date
    )


@router.get("/analytics/subscriptions", response_model=SubscriptionAnalytics)
def get_subscription_analytics(
    db:    Session   = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
):
    """Blueprint §11.5: Subscription analytics — MRR, churn rate, tier breakdown."""
    return admin_dashboard_service.get_subscription_analytics(db)


# ═══════════════════════════════════════════════════════════════════════════════
# §11.1 USER MANAGEMENT
# Blueprint §15: GET /admin/users/, PATCH /admin/users/{id}, POST /admin/users/{id}/ban
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/users", response_model=AdminUserListOut)
def list_users(
    role:       Optional[str] = Query(None, description="customer | business | rider"),
    status:     Optional[str] = Query(None, description="active | suspended | banned"),
    search:     Optional[str] = Query(None),
    pagination: dict          = Depends(get_pagination_params),
    db:         Session       = Depends(get_db),
    admin:      AdminUser     = Depends(require_admin),
):
    """
    Blueprint §11.1: View, search, filter all users.
    FIX: was user_type param — §14 field is role.
    """
    return admin_user_service.list_users(
        db,
        user_type=role,
        status=status,
        search=search,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )


@router.patch("/users/{user_id}", summary="Edit user profile")
def edit_user(
    user_id: UUID,
    body:    UserEditRequest,
    db:      Session   = Depends(get_db),
    admin:   AdminUser = Depends(require_admin),
):
    """Blueprint §11.1: Edit any user profile — name, phone, email."""
    user = admin_user_service.edit_user(
        db,
        user_id=user_id,
        full_name=body.full_name,
        phone_number=body.phone_number,
        email=body.email,
    )
    return {"success": True, "data": {"id": str(user.id), "message": "User profile updated"}}


@router.post("/users/{user_id}/ban", summary="Ban / suspend / reactivate user")
def ban_user(
    user_id: UUID,
    body:    UserBanRequest,
    db:      Session   = Depends(get_db),
    admin:   AdminUser = Depends(require_super_admin),  # destructive — super_admin only
):
    """
    Blueprint §11.1:
    "Suspend, ban, or delete account — mandatory reason log (immutable)"
    Blueprint §15: POST /admin/users/{id}/ban
    FIX: replaces PATCH /users/{id}/status which used a status enum not in §14.
    reason is MANDATORY — stored in immutable admin_ban_logs table.
    """
    user = admin_user_service.apply_ban_action(
        db,
        user_id=user_id,
        action=body.action,
        reason=body.reason,
        performed_by_admin_id=admin.id,
    )
    return {
        "success": True,
        "data": {
            "id":        str(user.id),
            "action":    body.action,
            "is_active": user.is_active,
            "is_banned": user.is_banned,
            "message":   f"User {body.action} successfully. Reason logged immutably.",
        },
    }


@router.get("/users/{user_id}/wallet", summary="View user wallet history")
def get_user_wallet_history(
    user_id:    UUID,
    pagination: dict      = Depends(get_pagination_params),
    db:         Session   = Depends(get_db),
    admin:      AdminUser = Depends(require_admin),
):
    """Blueprint §11.1: View full wallet transaction history for any user."""
    return admin_user_service.get_wallet_history(
        db,
        user_id=user_id,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )


@router.post("/users/{user_id}/wallet/adjust", summary="Manual wallet credit/debit")
def adjust_user_wallet(
    user_id: UUID,
    body:    WalletAdjustmentRequest,
    db:      Session   = Depends(get_db),
    admin:   AdminUser = Depends(require_super_admin),  # financial — super_admin only
):
    """
    Blueprint §11.1:
    "Manually credit or debit any wallet — requires written reason,
     logged with admin ID, immutable. (admin_wallet_adjustments table)"
    §5.6: atomic DB transaction.
    """
    adj = admin_user_service.adjust_wallet(
        db,
        user_id=user_id,
        admin_id=admin.id,
        adjustment_type=body.adjustment_type,
        amount=body.amount,
        reason=body.reason,
        related_order_id=body.related_order_id,
    )
    return {
        "success": True,
        "data": {
            "adjustment_id": str(adj.id),
            "type":          adj.adjustment_type,
            "amount":        str(adj.amount),
            "balance_after": str(adj.balance_after),
            "message":       "Wallet adjustment applied and logged immutably.",
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# §11.2 BUSINESS MANAGEMENT
# Blueprint §15: POST /admin/businesses/{id}/verify, /subscription
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/businesses", response_model=AdminBusinessListOut)
def list_businesses(
    category:    Optional[str]  = Query(None),
    is_verified: Optional[bool] = Query(None),
    search:      Optional[str]  = Query(None),
    pagination:  dict           = Depends(get_pagination_params),
    db:          Session        = Depends(get_db),
    admin:       AdminUser      = Depends(require_admin),
):
    return admin_business_service.list_businesses(
        db,
        category=category,
        is_verified=is_verified,
        search=search,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )


@router.post("/businesses/{business_id}/verify")
def verify_business(
    business_id: UUID,
    body:        BusinessVerifyUpdate,
    db:          Session   = Depends(get_db),
    admin:       AdminUser = Depends(require_admin),
):
    """
    Blueprint §11.2: Verify or reject business registration submissions.
    Blueprint §15: POST /admin/businesses/{id}/verify
    Rejection requires a reason (returned to business via notification).
    """
    biz = admin_business_service.verify_business(
        db,
        business_id=business_id,
        is_verified=body.is_verified,
        admin_id=admin.id,
        reason=body.reason,
    )
    action = "verified" if body.is_verified else "rejected"
    return {
        "success": True,
        "data": {
            "id":          str(biz.id),
            "is_verified": biz.is_verified,
            "message":     f"Business {action} successfully.",
        },
    }


@router.post("/businesses/{business_id}/subscription")
def update_business_subscription(
    business_id: UUID,
    body:        SubscriptionUpdateRequest,
    db:          Session   = Depends(get_db),
    admin:       AdminUser = Depends(require_super_admin),
):
    """
    Blueprint §11.2: Upgrade/downgrade subscription tier manually.
    Blueprint §15: POST /admin/businesses/{id}/subscription
    """
    biz = admin_business_service.update_subscription(
        db, business_id=business_id, tier=body.tier, reason=body.reason
    )
    return {
        "success": True,
        "data": {
            "id":   str(biz.id),
            "tier": biz.subscription_tier,
            "message": f"Subscription updated to {body.tier}.",
        },
    }


@router.patch("/businesses/{business_id}/product-limit")
def override_product_limit(
    business_id: UUID,
    body:        ProductLimitOverrideRequest,
    db:          Session   = Depends(get_db),
    admin:       AdminUser = Depends(require_admin),
):
    """
    Blueprint §11.2:
    "Override product listing limit for specific businesses."
    Blueprint §2.2 implementation note: product_limit_override fields.
    """
    biz = admin_business_service.set_product_limit_override(
        db,
        business_id=business_id,
        override_enabled=body.override_enabled,
        override_value=body.override_value,
    )
    return {
        "success": True,
        "data": {
            "id":                         str(biz.id),
            "product_limit_override":     biz.product_limit_override,
            "product_limit_override_value": biz.product_limit_override_value,
        },
    }


@router.post("/businesses/{business_id}/featured")
def set_featured_status(
    business_id: UUID,
    body:        FeaturedStatusRequest,
    db:          Session   = Depends(get_db),
    admin:       AdminUser = Depends(require_admin),
):
    """Blueprint §11.2: Set featured status manually (overrides rotation algorithm)."""
    biz = admin_business_service.set_featured_status(
        db, business_id=business_id, is_featured=body.is_featured
    )
    return {
        "success": True,
        "data": {
            "id":          str(biz.id),
            "is_featured": body.is_featured,
            "message":     "Featured status updated.",
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# §11.3 FINANCIAL CONTROLS
# Blueprint §15: GET/POST /admin/withdrawals/, POST /admin/config/fees
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/withdrawals", response_model=WithdrawalListOut)
def list_withdrawals(
    status:     Optional[str] = Query(None, description="pending | approved | held | rejected"),
    pagination: dict          = Depends(get_pagination_params),
    db:         Session       = Depends(get_db),
    admin:      AdminUser     = Depends(require_admin),
):
    """
    Blueprint §11.3: Full view of all wallet funding events, withdrawal requests.
    Blueprint §15: GET /admin/withdrawals/
    """
    return admin_finance_service.list_withdrawals(
        db,
        status=status,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )


@router.post("/withdrawals/{withdrawal_id}/action")
def process_withdrawal(
    withdrawal_id: UUID,
    body:          WithdrawalActionRequest,
    db:            Session   = Depends(get_db),
    admin:         AdminUser = Depends(require_super_admin),
):
    """
    Blueprint §11.3: Approve or hold withdrawals above configurable threshold.
    Blueprint §15: POST /admin/withdrawals/{id}/approve
    """
    result = admin_finance_service.process_withdrawal(
        db,
        withdrawal_id=withdrawal_id,
        action=body.action,
        admin_id=admin.id,
        reason=body.reason,
    )
    return {
        "success": True,
        "data": {
            "withdrawal_id": str(withdrawal_id),
            "action":        body.action,
            "message":       f"Withdrawal {body.action}d.",
        },
    }


@router.get("/config/fees")
def get_fee_config(
    db:    Session   = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
):
    """Blueprint §11.3: Real-time platform fee revenue configuration."""
    return {"success": True, "data": admin_finance_service.get_fee_config(db)}


@router.post("/config/fees")
def update_fee_config(
    body:  PlatformFeeConfig,
    db:    Session   = Depends(get_db),
    admin: AdminUser = Depends(require_super_admin),
):
    """
    Blueprint §11.3: Adjust platform fee rates — changes apply to NEW transactions only.
    Blueprint §15: POST /admin/config/fees
    Every change logged with timestamp + admin ID.
    """
    result = admin_finance_service.update_fee_config(
        db,
        admin_id=admin.id,
        fee_standard=body.fee_standard_ngn,
        fee_booking=body.fee_booking_ngn,
        fee_ticket=body.fee_ticket_ngn,
        reason=body.reason,
    )
    return {"success": True, "data": result}


# ═══════════════════════════════════════════════════════════════════════════════
# §11.4 CONTENT MODERATION
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/moderation/queue", response_model=ModerationQueueOut)
def get_moderation_queue(
    pagination: dict    = Depends(get_pagination_params),
    db:         Session = Depends(get_db),
    admin:      AdminUser = Depends(require_admin),
):
    """Blueprint §11.4: Review flagged content sorted by report count."""
    return admin_moderation_service.get_queue(
        db, skip=pagination["skip"], limit=pagination["limit"]
    )


@router.post("/content/{content_type}/{content_id}/remove")
def remove_content(
    content_type: str,
    content_id:   UUID,
    body:         ContentRemoveRequest,
    db:           Session   = Depends(get_db),
    admin:        AdminUser = Depends(require_admin),
):
    """
    Blueprint §11.4:
    "Remove any content — business receives automated notification with reason."
    content_type: review | reel | story | job
    """
    result = admin_moderation_service.remove_content(
        db,
        content_type=content_type,
        content_id=content_id,
        reason=body.reason,
        admin_id=admin.id,
        notify_business=body.notify_business,
    )
    return {"success": True, "data": result}


@router.post("/content/blocklist")
def manage_keyword_blocklist(
    body:  KeywordBlocklistRequest,
    db:    Session   = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
):
    """Blueprint §11.4: Keyword blocklist management (auto-review triggers)."""
    updated = admin_moderation_service.update_keyword_blocklist(
        db, keywords=body.keywords, action=body.action, admin_id=admin.id
    )
    return {
        "success": True,
        "data": {
            "action":           body.action,
            "blocklist_count":  len(updated),
            "updated_keywords": updated[:20],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# §11.6 CONFIGURATION PANEL
# Blueprint §15: PATCH /admin/content/terms, POST /admin/push-notifications
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/config/features")
def set_feature_flag(
    body:  FeatureFlagRequest,
    db:    Session   = Depends(get_db),
    admin: AdminUser = Depends(require_super_admin),
):
    """
    Blueprint §11.6: Feature flag toggles — enable/disable any module without code deploy.
    """
    result = admin_config_service.set_feature_flag(
        db, key=body.key, enabled=body.enabled, admin_id=admin.id
    )
    return {"success": True, "data": result}


@router.patch("/content/terms")
def update_terms(
    body:  TermsUpdateRequest,
    db:    Session   = Depends(get_db),
    admin: AdminUser = Depends(require_super_admin),
):
    """
    Blueprint §11.6 + §15: PATCH /admin/content/terms
    "T&C: admin edits via rich text editor; mobile always fetches latest version."
    Blueprint §3.1 step 8: "Cannot proceed without accepting. Never cached locally."
    """
    result = admin_config_service.update_terms(
        db, content=body.content, version=body.version, admin_id=admin.id
    )
    return {"success": True, "data": result}


@router.patch("/content/privacy-policy")
def update_privacy_policy(
    body:  PrivacyPolicyUpdateRequest,
    db:    Session   = Depends(get_db),
    admin: AdminUser = Depends(require_super_admin),
):
    """Blueprint §11.6: Update Privacy Policy via rich text editor."""
    result = admin_config_service.update_privacy_policy(
        db, content=body.content, version=body.version, admin_id=admin.id
    )
    return {"success": True, "data": result}


@router.get("/content/terms")
def get_terms(db: Session = Depends(get_db)):
    """
    Blueprint §3.1 step 8: Mobile fetches T&C on every load of relevant screen.
    No auth required — publicly readable.
    """
    return {"success": True, "data": admin_config_service.get_terms(db)}


@router.get("/content/privacy-policy")
def get_privacy_policy(db: Session = Depends(get_db)):
    """Blueprint §3.1 step 8: Mobile fetches Privacy Policy on every load."""
    return {"success": True, "data": admin_config_service.get_privacy_policy(db)}


@router.post("/push-notifications")
def send_push_notification(
    body:  PushNotificationRequest,
    db:    Session   = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
):
    """
    Blueprint §11.6 + §15: POST /admin/push-notifications
    "Push notifications: to all users, a segment, or a specific user."
    """
    if not body.segment and not body.user_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "validation_error", "message": "Provide either segment or user_id."},
        )
    if body.segment and body.user_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "validation_error", "message": "Provide either segment or user_id — not both."},
        )

    result = admin_config_service.send_push_notification(
        db,
        title=body.title,
        body=body.body,
        segment=body.segment,
        user_id=body.user_id,
        admin_id=admin.id,
    )
    return {"success": True, "data": result}


@router.get("/config")
def list_all_config(
    db:    Session   = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
):
    """Blueprint §11.6: Configuration panel — list all platform config values."""
    return {"success": True, "data": admin_config_service.get_all_config(db)}


@router.patch("/config/{key}")
def set_config_value(
    key:   str,
    body:  ConfigValueRequest,
    db:    Session   = Depends(get_db),
    admin: AdminUser = Depends(require_super_admin),
):
    """Blueprint §11.6: Set any platform config key-value."""
    result = admin_config_service.set_config_value(
        db, key=key, value=body.value, admin_id=admin.id, description=body.description
    )
    return {"success": True, "data": result}


# ═══════════════════════════════════════════════════════════════════════════════
# §11.6 SUPPORT AGENT MANAGEMENT
# Blueprint §11.6: "Manage support agent accounts and assign incoming tickets."
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/support-agents")
def create_support_agent(
    body:  SupportAgentCreateRequest,
    db:    Session   = Depends(get_db),
    admin: AdminUser = Depends(require_super_admin),
):
    """
    Blueprint §11.6: Create support agent or admin account.
    Blueprint §2.2 HARD RULE: Admin accounts CANNOT be created through mobile registration.
    """
    agent = admin_config_service.create_support_agent(
        db,
        email=str(body.email),
        password=body.password,
        full_name=body.full_name,
        role=body.role,
        created_by_id=admin.id,
    )
    return {
        "success": True,
        "data": {
            "id":       str(agent.id),
            "email":    agent.email,
            "role":     agent.role,
            "message":  "Support agent account created.",
        },
    }