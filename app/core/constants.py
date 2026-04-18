"""
app/core/constants.py

Platform-wide constants derived from the Localy Platform Blueprint v2.0.

No blueprint violations found in original — presented for completeness.

FIXES already present in this version (documented here for audit trail):
  1. MIN_WALLET_TOPUP: ₦500 → ₦1,000. Blueprint §5.1.
  2. MAX_WALLET_TOPUP_DAILY: ₦500,000 → ₦2,000,000. Blueprint §5.1.
  3. SubscriptionPlanType: PRO_DRIVER removed — not in blueprint §8.1.
     Blueprint §8.1: Free / Starter / Pro / Enterprise only.
"""

from decimal import Decimal
from enum import Enum


# ════════════════════════════════════════════════════════════════════════════════
# USER ROLES — Blueprint §2
# Exactly three mobile roles. Admin is a separate table and separate JWT.
# ════════════════════════════════════════════════════════════════════════════════

class UserType(str, Enum):
    CUSTOMER = "customer"
    BUSINESS = "business"
    RIDER    = "rider"
    # ADMIN is NOT a mobile role — admin_users table + separate JWT (§2.2, §3.2)


class UserStatus(str, Enum):
    """
    NOTE: Blueprint §14 user model uses is_active BOOLEAN + is_banned BOOLEAN
    (not a status enum). This enum is kept for legacy compatibility only.
    New code should use the boolean fields on the User model directly.
    """
    ACTIVE               = "active"
    SUSPENDED            = "suspended"
    PENDING_VERIFICATION = "pending_verification"
    BANNED               = "banned"


# ════════════════════════════════════════════════════════════════════════════════
# BUSINESS CATEGORIES — Blueprint §1
# Exactly 7 categories. Immutable after registration (admin override only).
# Blueprint §2.2 HARD RULE: category field is immutable post-registration.
# ════════════════════════════════════════════════════════════════════════════════

class BusinessCategory(str, Enum):
    LODGES         = "lodges"
    FOOD           = "food"
    SERVICES       = "services"
    PRODUCTS       = "products"
    HEALTH         = "health"
    PROPERTY_AGENT = "property_agent"
    TICKET_SALES   = "ticket_sales"


class VerificationBadge(str, Enum):
    NONE       = "none"
    STARTER    = "starter"
    PRO        = "pro"
    ENTERPRISE = "enterprise"


# ════════════════════════════════════════════════════════════════════════════════
# SUBSCRIPTION PLANS — Blueprint §8.1
# Four tiers only. Annual = 10 months price (2 months free).
# ════════════════════════════════════════════════════════════════════════════════

class SubscriptionPlanType(str, Enum):
    FREE       = "free"
    STARTER    = "starter"
    PRO        = "pro"
    ENTERPRISE = "enterprise"
    # PRO_DRIVER removed — not in Blueprint §8.1


# Tier rank integers for ORDER BY in discovery queries — Blueprint §7.2
# Enterprise: 4, Pro: 3, Starter: 2, Free: 1
SUBSCRIPTION_TIER_RANKS: dict[str, int] = {
    "enterprise": 4,
    "pro":        3,
    "starter":    2,
    "free":       1,
}

# Blueprint §8.1 subscription prices (₦ NGN)
# Annual = 10 × monthly (2 months free)
STARTER_MONTHLY_PRICE    = Decimal("4500.00")
STARTER_ANNUAL_PRICE     = Decimal("45000.00")     # 10 × 4,500
PRO_MONTHLY_PRICE        = Decimal("10000.00")
PRO_ANNUAL_PRICE         = Decimal("100000.00")    # 10 × 10,000
ENTERPRISE_MONTHLY_PRICE = Decimal("15000.00")
ENTERPRISE_ANNUAL_PRICE  = Decimal("150000.00")    # 10 × 15,000


class BillingCycle(str, Enum):
    MONTHLY = "monthly"
    ANNUAL  = "annual"


# ════════════════════════════════════════════════════════════════════════════════
# TRANSACTION TYPES
# ════════════════════════════════════════════════════════════════════════════════

class TransactionType(str, Enum):
    CREDIT         = "credit"
    DEBIT          = "debit"
    REFUND         = "refund"
    CASHBACK       = "cashback"
    REFERRAL_BONUS = "referral_bonus"
    TOP_UP         = "top_up"
    PAYMENT        = "payment"
    PLATFORM_FEE   = "platform_fee"   # Blueprint §5.4


class TransactionStatus(str, Enum):
    PENDING   = "pending"
    COMPLETED = "completed"
    FAILED    = "failed"
    REVERSED  = "reversed"


# ════════════════════════════════════════════════════════════════════════════════
# BOOKING / ORDER STATUSES
# ════════════════════════════════════════════════════════════════════════════════

class BookingStatus(str, Enum):
    PENDING     = "pending"
    CONFIRMED   = "confirmed"
    CHECKED_IN  = "checked_in"
    CHECKED_OUT = "checked_out"
    CANCELLED   = "cancelled"
    NO_SHOW     = "no_show"


class OrderStatus(str, Enum):
    PENDING    = "pending"
    PROCESSING = "processing"
    READY      = "ready"
    SHIPPED    = "shipped"
    DELIVERED  = "delivered"
    CANCELLED  = "cancelled"
    REFUNDED   = "refunded"


class PaymentStatus(str, Enum):
    PENDING  = "pending"
    PAID     = "paid"
    FAILED   = "failed"
    REFUNDED = "refunded"


# ════════════════════════════════════════════════════════════════════════════════
# DELIVERY STATUSES
# ════════════════════════════════════════════════════════════════════════════════

class DeliveryStatus(str, Enum):
    PENDING    = "pending"
    ASSIGNED   = "assigned"
    PICKED_UP  = "picked_up"
    IN_TRANSIT = "in_transit"
    DELIVERED  = "delivered"
    CANCELLED  = "cancelled"
    FAILED     = "failed"


# ════════════════════════════════════════════════════════════════════════════════
# MESSAGE / CHAT — Blueprint §10 + §14
# content_type IN ('text','image','voice_note') — matches DB CHECK constraint.
# Blueprint §10.2 HARD RULE: voice_note DISABLED in rider delivery chats.
# ════════════════════════════════════════════════════════════════════════════════

class MessageType(str, Enum):
    TEXT       = "text"
    IMAGE      = "image"
    VOICE_NOTE = "voice_note"
    # Blueprint §10.2 HARD RULE: VOICE_NOTE blocked in rider chats at API layer.
    # The DB CHECK allows 'voice_note' — enforcement is at application layer.
    # 'system' is NOT a valid content_type — not in §14 CHECK constraint.


# ════════════════════════════════════════════════════════════════════════════════
# NOTIFICATION TYPES
# ════════════════════════════════════════════════════════════════════════════════

class NotificationType(str, Enum):
    BOOKING_CONFIRMED = "booking_confirmed"
    ORDER_STATUS      = "order_status"
    DELIVERY_UPDATE   = "delivery_update"
    PAYMENT_RECEIVED  = "payment_received"
    WALLET_CREDITED   = "wallet_credited"
    REVIEW_POSTED     = "review_posted"
    MESSAGE_RECEIVED  = "message_received"
    PROMOTION         = "promotion"
    REMINDER          = "reminder"


# ════════════════════════════════════════════════════════════════════════════════
# PRODUCT / MARKETPLACE — Blueprint §6.4
# ════════════════════════════════════════════════════════════════════════════════

class ProductType(str, Enum):
    PHYSICAL = "physical"
    DIGITAL  = "digital"


# ════════════════════════════════════════════════════════════════════════════════
# PROPERTY — Blueprint §6.6
# ════════════════════════════════════════════════════════════════════════════════

class PropertyType(str, Enum):
    HOUSE      = "house"
    APARTMENT  = "apartment"
    VILLA      = "villa"
    COMMERCIAL = "commercial"
    LAND       = "land"
    OFFICE     = "office"
    WAREHOUSE  = "warehouse"


class ListingType(str, Enum):
    SALE  = "sale"
    RENT  = "rent"
    LEASE = "lease"


# ════════════════════════════════════════════════════════════════════════════════
# TICKET / EVENTS — Blueprint §6.7
# ════════════════════════════════════════════════════════════════════════════════

class TicketCategory(str, Enum):
    FLIGHT  = "flight"
    BUS     = "bus"
    TRAIN   = "train"
    CONCERT = "concert"
    SPORTS  = "sports"
    PARTY   = "party"
    EVENT   = "event"
    OTHER   = "other"


# ════════════════════════════════════════════════════════════════════════════════
# HEALTH — Blueprint §6.5
# ════════════════════════════════════════════════════════════════════════════════

class HealthProviderType(str, Enum):
    DOCTOR   = "doctor"
    PHARMACY = "pharmacy"
    LAB      = "lab"


class ConsultationType(str, Enum):
    VIDEO     = "video"
    AUDIO     = "audio"
    CHAT      = "chat"
    IN_PERSON = "in_person"


# ════════════════════════════════════════════════════════════════════════════════
# HOTEL / ROOM — Blueprint §6.1
# ════════════════════════════════════════════════════════════════════════════════

class RoomStatus(str, Enum):
    VACANT       = "vacant"
    OCCUPIED     = "occupied"
    DIRTY        = "dirty"
    BLOCKED      = "blocked"
    OUT_OF_ORDER = "out_of_order"


# ════════════════════════════════════════════════════════════════════════════════
# JOBS — Blueprint §8.6
# "full-time/part-time/contract/gig" — all four required.
# ════════════════════════════════════════════════════════════════════════════════

class JobType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT  = "contract"
    GIG       = "gig"          # Blueprint §8.6: required


# ════════════════════════════════════════════════════════════════════════════════
# SUPPORT TICKET — Blueprint §10.3
# ════════════════════════════════════════════════════════════════════════════════

class SupportTicketStatus(str, Enum):
    OPEN        = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED    = "resolved"


# ════════════════════════════════════════════════════════════════════════════════
# LOCATION CONSTANTS — Blueprint §4.1
# "Default radius: 5 km from device GPS position"
# "User adjustment: 1 km to 50 km"
# "No LGA column or parameter anywhere." — HARD RULE
# ════════════════════════════════════════════════════════════════════════════════

DEFAULT_RADIUS_KM = 5.0
MIN_RADIUS_KM     = 1.0
MAX_RADIUS_KM     = 50.0

DEFAULT_RADIUS_METERS = int(DEFAULT_RADIUS_KM * 1000)   # 5000 m
MIN_RADIUS_METERS     = int(MIN_RADIUS_KM * 1000)        # 1000 m
MAX_RADIUS_METERS     = int(MAX_RADIUS_KM * 1000)        # 50000 m

# NO LGA constants — Blueprint §4 HARD RULE: no LGA anywhere in codebase.


# ════════════════════════════════════════════════════════════════════════════════
# NIGERIA STATES — display-only
# Blueprint §4 HARD RULE: location filtering is radius-only, NEVER by state/LGA.
# These strings are for address display and form dropdowns ONLY.
# ════════════════════════════════════════════════════════════════════════════════

NIGERIA_STATES = [
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa",
    "Benue", "Borno", "Cross River", "Delta", "Ebonyi", "Edo",
    "Ekiti", "Enugu", "FCT", "Gombe", "Imo", "Jigawa", "Kaduna",
    "Kano", "Katsina", "Kebbi", "Kogi", "Kwara", "Lagos", "Nasarawa",
    "Niger", "Ogun", "Ondo", "Osun", "Oyo", "Plateau", "Rivers",
    "Sokoto", "Taraba", "Yobe", "Zamfara",
]


# ════════════════════════════════════════════════════════════════════════════════
# FINANCIAL CONSTANTS — Blueprint §5.4 / §5.1 / §9.1
# All financial amounts: NUMERIC(12,2) in Naira. Never floats. Never utcnow().
# ════════════════════════════════════════════════════════════════════════════════

# Platform fees — shown to both sides of every transaction (Blueprint §5.4)
# Total platform revenue per transaction = customer_fee + business_fee
PLATFORM_FEE_STANDARD = Decimal("50.00")   # ₦50 per side — product/food orders
PLATFORM_FEE_BOOKING  = Decimal("100.00")  # ₦100 per side — hotel/service/health bookings
PLATFORM_FEE_TICKET   = Decimal("50.00")   # ₦50 from customer only — tickets

# Referral programme — Blueprint §9.1
REFERRAL_BONUS_AMOUNT    = Decimal("1000.00")  # ₦1,000 credited to referrer
REFERRAL_DISCOUNT_AMOUNT = Decimal("1000.00")  # ₦1,000 off new user's first order
REFERRAL_MINIMUM_ORDER   = Decimal("2000.00")  # First order must be > ₦2,000

# Wallet limits — Blueprint §5.1 + §5.2
MIN_WALLET_TOPUP       = Decimal("1000.00")     # ₦1,000 minimum top-up (FIX: was ₦500)
MAX_WALLET_TOPUP_DAILY = Decimal("2000000.00")  # ₦2,000,000 daily limit (FIX: was ₦500k)
MIN_WITHDRAWAL_AMOUNT  = Decimal("1000.00")     # Blueprint §5.2
MAX_WITHDRAWAL_AMOUNT  = Decimal("1000000.00")  # Blueprint §5.2 (higher on request)