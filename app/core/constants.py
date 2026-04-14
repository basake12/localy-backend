from enum import Enum


# ============================================
# USER TYPES
# ============================================

class UserType(str, Enum):
    CUSTOMER = "customer"
    BUSINESS = "business"
    RIDER = "rider"
    ADMIN = "admin"


class UserStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    PENDING_VERIFICATION = "pending_verification"
    BANNED = "banned"


# ============================================
# BUSINESS CATEGORIES
# ============================================

class BusinessCategory(str, Enum):
    LODGES = "lodges"
    FOOD = "food"
    SERVICES = "services"
    PRODUCTS = "products"
    HEALTH = "health"
    PROPERTY_AGENT = "property_agent"
    TICKET_SALES = "ticket_sales"


class VerificationBadge(str, Enum):
    NONE = "none"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


# ============================================
# SUBSCRIPTION PLANS
# ============================================

class SubscriptionPlanType(str, Enum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"
    PRO_DRIVER = "pro_driver"


class BillingCycle(str, Enum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


# ============================================
# TRANSACTION TYPES
# ============================================

class TransactionType(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"
    REFUND = "refund"
    CASHBACK = "cashback"
    REFERRAL_BONUS = "referral_bonus"
    TOP_UP = "top_up"
    PAYMENT = "payment"


class TransactionStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERSED = "reversed"


# ============================================
# BOOKING/ORDER STATUSES
# ============================================

class BookingStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class OrderStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


# ============================================
# DELIVERY STATUSES
# ============================================

class DeliveryStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    PICKED_UP = "picked_up"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


# ============================================
# MESSAGE TYPES
# ============================================

class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    LOCATION = "location"


# ============================================
# NOTIFICATION TYPES
# ============================================

class NotificationType(str, Enum):
    BOOKING_CONFIRMED = "booking_confirmed"
    ORDER_STATUS = "order_status"
    DELIVERY_UPDATE = "delivery_update"
    PAYMENT_RECEIVED = "payment_received"
    REVIEW_POSTED = "review_posted"
    MESSAGE_RECEIVED = "message_received"
    PROMOTION = "promotion"
    REMINDER = "reminder"


# ============================================
# FILE TYPES
# ============================================

class FileType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    AUDIO = "audio"


# ============================================
# PRODUCT TYPES
# ============================================

class ProductType(str, Enum):
    PHYSICAL = "physical"
    DIGITAL = "digital"


# ============================================
# PROPERTY TYPES
# ============================================

class PropertyType(str, Enum):
    HOUSE = "house"
    APARTMENT = "apartment"
    VILLA = "villa"
    COMMERCIAL = "commercial"
    LAND = "land"
    OFFICE = "office"
    WAREHOUSE = "warehouse"


class ListingType(str, Enum):
    SALE = "sale"
    RENT = "rent"
    LEASE = "lease"


# ============================================
# TICKET CATEGORIES
# ============================================

class TicketCategory(str, Enum):
    FLIGHT = "flight"
    BUS = "bus"
    TRAIN = "train"
    CONCERT = "concert"
    SPORTS = "sports"
    PARTY = "party"
    EVENT = "event"
    OTHER = "other"


# ============================================
# HEALTH PROVIDER TYPES
# ============================================

class HealthProviderType(str, Enum):
    DOCTOR = "doctor"
    PHARMACY = "pharmacy"
    LAB = "lab"


class ConsultationType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    CHAT = "chat"
    IN_PERSON = "in_person"


# ============================================
# ROOM STATUSES
# ============================================

class RoomStatus(str, Enum):
    VACANT = "vacant"
    OCCUPIED = "occupied"
    DIRTY = "dirty"
    BLOCKED = "blocked"
    OUT_OF_ORDER = "out_of_order"


# ============================================
# REEL TYPES
# ============================================

class ReelType(str, Enum):
    REEL = "reel"
    STORY = "story"


# ============================================
# LOCATION CONSTANTS (BLUEPRINT v2.0)
# ============================================
# Per Blueprint: "Location model — Radius-based (default 5 km) — no LGA dependency"

DEFAULT_RADIUS_KM = 5.0      # Default discovery radius in kilometers
MIN_RADIUS_KM = 1.0          # Minimum user-adjustable radius
MAX_RADIUS_KM = 50.0         # Maximum user-adjustable radius

# PostGIS uses meters for ST_DWithin — these constants convert km to meters
DEFAULT_RADIUS_METERS = int(DEFAULT_RADIUS_KM * 1000)  # 5000 meters
MIN_RADIUS_METERS = int(MIN_RADIUS_KM * 1000)          # 1000 meters
MAX_RADIUS_METERS = int(MAX_RADIUS_KM * 1000)          # 50000 meters


# ============================================
# NIGERIA STATES (for display purposes only)
# ============================================
# NOTE: States/LGAs are stored in business addresses for display but 
# NEVER used for filtering queries (Blueprint: no LGA dependency)

NIGERIA_STATES = [
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa",
    "Benue", "Borno", "Cross River", "Delta", "Ebonyi", "Edo",
    "Ekiti", "Enugu", "FCT", "Gombe", "Imo", "Jigawa", "Kaduna",
    "Kano", "Katsina", "Kebbi", "Kogi", "Kwara", "Lagos", "Nasarawa",
    "Niger", "Ogun", "Ondo", "Osun", "Oyo", "Plateau", "Rivers",
    "Sokoto", "Taraba", "Yobe", "Zamfara"
]


# ============================================
# FINANCIAL RATES & LIMITS
# ============================================
from decimal import Decimal

# Platform fees per Blueprint Section 4.4
PLATFORM_FEE_STANDARD = Decimal("50.00")     # ₦50 on products, food, tickets
PLATFORM_FEE_BOOKING = Decimal("100.00")     # ₦100 on hotels, services, health
PLATFORM_FEE_TICKET = Decimal("50.00")       # ₦50 per ticket

# Referral rewards per Blueprint Section 6.1
REFERRAL_BONUS_AMOUNT = Decimal("1000.00")         # ₦1,000 to referrer
REFERRAL_DISCOUNT_AMOUNT = Decimal("1000.00")      # ₦1,000 off for new user
REFERRAL_MINIMUM_ORDER = Decimal("2000.00")        # New user must spend >₦2,000

# Wallet limits
MIN_WALLET_TOPUP = Decimal("500.00")               # Minimum top-up amount
MAX_WALLET_TOPUP_DAILY = Decimal("500000.00")      # Daily funding limit
MIN_WITHDRAWAL_AMOUNT = Decimal("1000.00")         # Minimum withdrawal
MAX_WITHDRAWAL_AMOUNT = Decimal("1000000.00")      # Maximum withdrawal per day
# Ticket service charge rate (0.00 = no percentage charge; Blueprint §4.4 uses flat ₦50 fee only)
TICKET_SERVICE_CHARGE_RATE = Decimal("0.00")