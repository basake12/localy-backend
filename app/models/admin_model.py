"""
app/models/admin_model.py

Blueprint §14 admin_users table.
Blueprint §11.1: admin_wallet_adjustments table — immutable audit log for
manual wallet credits/debits performed by admins.

Blueprint §2.2 HARD RULE: Admin exists ONLY as a web application.
  No admin panel inside the mobile app.
  Admin accounts are NOT created through the mobile registration flow.
  Admin tokens carry { role: "admin", admin_id: uuid } — NEVER accepted by
  mobile API endpoints.

Blueprint §3.2:
  "Admin tokens are issued by a separate endpoint and carry an 'admin' role
   claim — they are never accepted by mobile API endpoints."
  JWT_ADMIN_SECRET_KEY is separate from JWT_SECRET_KEY.

Blueprint §11.1:
  "Manually credit or debit any wallet — requires written reason, logged with
   admin ID, immutable. (admin_wallet_adjustments table)"
  "Suspend, ban, or delete account — mandatory reason log (immutable)"

Blueprint §13.3 admin stack:
  React · shadcn/ui · TanStack Table · Same FastAPI backend (admin JWT)
  Hosted at: admin.localy.ng
"""

from sqlalchemy import (
    Column, String, Boolean, Text, Numeric,
    DateTime, ForeignKey, Index, CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.models.base_model import BaseModel


# ── AdminUser ─────────────────────────────────────────────────────────────────

class AdminUser(BaseModel):
    """
    Blueprint §14 admin_users table.
    Admin accounts are provisioned ONLY by a senior admin or a migration script.
    Self-registration is blocked — there is no public endpoint to create admins.

    Blueprint §3.2: admin token payload:
      { role: "admin", admin_id: <this table's id>, iat, exp }

    Roles:
      super_admin   — full access including fee config, ban, delete
      admin         — standard admin access
      support_agent — read-only user/order view + support ticket management
                      (Blueprint §10.3: support agents handle chat tickets)
    """
    __tablename__ = "admin_users"

    email         = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    full_name     = Column(String(255), nullable=False)

    # Blueprint §13.3: role within admin — controls access tiers
    role = Column(
        String(30),
        nullable=False,
        default="support_agent",
    )  # super_admin | admin | support_agent

    is_active = Column(Boolean, nullable=False, default=True)

    # Audit trail
    last_login_at     = Column(DateTime(timezone=True), nullable=True)
    created_by_id     = Column(
        UUID(as_uuid=True),
        ForeignKey("admin_users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    created_by = relationship(
        "AdminUser",
        remote_side="AdminUser.id",
        foreign_keys=[created_by_id],
        uselist=False,
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('super_admin','admin','support_agent')",
            name="valid_admin_role",
        ),
        Index("ix_admin_users_email", "email"),
        {"extend_existing": True},
    )

    def __repr__(self) -> str:
        return f"<AdminUser {self.email} role={self.role}>"


# ── AdminWalletAdjustment ─────────────────────────────────────────────────────

class AdminWalletAdjustment(BaseModel):
    """
    Blueprint §11.1: immutable audit log for manual wallet credits/debits.

    "Manually credit or debit any wallet — requires written reason,
     logged with admin ID, immutable. (admin_wallet_adjustments table)"

    IMMUTABILITY: rows are INSERT-only. No UPDATE or DELETE is permitted.
    The application layer never calls db.delete() or UPDATE on this table.
    The admin panel UI must not expose edit/delete actions on this table.

    adjustment_type: 'credit' | 'debit'
    amount: always positive — direction is encoded in adjustment_type.
    """
    __tablename__ = "admin_wallet_adjustments"

    # Which wallet was adjusted
    wallet_id = Column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Which admin performed the adjustment
    performed_by_admin_id = Column(
        UUID(as_uuid=True),
        ForeignKey("admin_users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Direction
    adjustment_type = Column(String(10), nullable=False)  # credit | debit

    # Amount in Naira — NUMERIC(12,2), never float (Blueprint §5.6)
    amount = Column(Numeric(12, 2), nullable=False)

    # Balances at time of adjustment (for audit trail integrity)
    balance_before = Column(Numeric(12, 2), nullable=False)
    balance_after  = Column(Numeric(12, 2), nullable=False)

    # Mandatory written reason (Blueprint §11.1)
    reason = Column(Text, nullable=False)

    # Related entity for context
    related_user_id     = Column(UUID(as_uuid=True), nullable=True)
    related_order_id    = Column(UUID(as_uuid=True), nullable=True)

    # Relationships (read-only references)
    performed_by = relationship("AdminUser", foreign_keys=[performed_by_admin_id])

    __table_args__ = (
        CheckConstraint(
            "adjustment_type IN ('credit','debit')",
            name="valid_adjustment_type",
        ),
        CheckConstraint(
            "amount > 0",
            name="positive_adjustment_amount",
        ),
        Index("ix_admin_wallet_adj_wallet", "wallet_id"),
        Index("ix_admin_wallet_adj_admin",  "performed_by_admin_id"),
        {"extend_existing": True},
    )

    def __repr__(self) -> str:
        return (
            f"<AdminWalletAdjustment {self.adjustment_type} "
            f"₦{self.amount} wallet={self.wallet_id}>"
        )


# ── AdminBanLog ───────────────────────────────────────────────────────────────

class AdminBanLog(BaseModel):
    """
    Blueprint §11.1: "Suspend, ban, or delete account — mandatory reason log (immutable)"

    INSERT-only table. Tracks every suspension, ban, or reactivation action
    with the performing admin's ID and a mandatory written reason.
    Visible in admin panel under user profile history.
    """
    __tablename__ = "admin_ban_logs"

    target_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    performed_by_admin_id = Column(
        UUID(as_uuid=True),
        ForeignKey("admin_users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    action = Column(String(20), nullable=False)   # suspended | banned | reactivated | deleted
    reason = Column(Text, nullable=False)          # mandatory — cannot be empty

    # Relationships
    performed_by = relationship("AdminUser", foreign_keys=[performed_by_admin_id])

    __table_args__ = (
        CheckConstraint(
            "action IN ('suspended','banned','reactivated','deleted')",
            name="valid_ban_action",
        ),
        CheckConstraint(
            "length(trim(reason)) > 0",
            name="non_empty_ban_reason",
        ),
        Index("ix_admin_ban_logs_user", "target_user_id"),
        {"extend_existing": True},
    )

    def __repr__(self) -> str:
        return f"<AdminBanLog {self.action} user={self.target_user_id}>"


# ── PlatformConfig ────────────────────────────────────────────────────────────

class PlatformConfig(BaseModel):
    """
    Blueprint §11.6 Configuration Panel.
    Stores all admin-configurable platform settings as key-value pairs.
    Admin can create, pause, edit, end promotions without code deployment.

    key examples:
      platform_fee_standard_ngn       → "50.00"
      platform_fee_booking_ngn        → "100.00"
      platform_fee_ticket_ngn         → "50.00"
      referral_bonus_amount_ngn       → "1000.00"
      referral_discount_amount_ngn    → "1000.00"
      referral_min_order_ngn          → "2000.00"
      default_discovery_radius_m      → "5000"
      min_discovery_radius_m          → "1000"
      max_discovery_radius_m          → "50000"
      feature_flag_hotels_enabled     → "true"
      feature_flag_events_enabled     → "true"
      terms_and_conditions_text       → "<rich text HTML>"
      privacy_policy_text             → "<rich text HTML>"
      terms_version                   → "v2.1"
    """
    __tablename__ = "platform_config"

    # Unique config key
    key = Column(String(100), unique=True, nullable=False, index=True)

    # Value stored as text — cast at application layer
    value = Column(Text, nullable=False)

    description = Column(Text, nullable=True)

    # Which admin last updated this value
    updated_by_admin_id = Column(
        UUID(as_uuid=True),
        ForeignKey("admin_users.id", ondelete="SET NULL"),
        nullable=True,
    )

    updated_by = relationship("AdminUser", foreign_keys=[updated_by_admin_id])

    __table_args__ = ({"extend_existing": True},)

    def __repr__(self) -> str:
        return f"<PlatformConfig {self.key}={self.value[:30]}>"