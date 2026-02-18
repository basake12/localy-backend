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
# NIGERIA LOCAL GOVERNMENTS (Sample - Abuja)
# ============================================

ABUJA_LOCAL_GOVERNMENTS = [
    "Abaji",
    "Abuja Municipal",
    "Bwari",
    "Gwagwalada",
    "Kuje",
    "Kwali"
]

# Add more states and LGAs as needed
NIGERIA_STATES = [
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa",
    "Benue", "Borno", "Cross River", "Delta", "Ebonyi", "Edo",
    "Ekiti", "Enugu", "FCT", "Gombe", "Imo", "Jigawa", "Kaduna",
    "Kano", "Katsina", "Kebbi", "Kogi", "Kwara", "Lagos", "Nasarawa",
    "Niger", "Ogun", "Ondo", "Osun", "Oyo", "Plateau", "Rivers",
    "Sokoto", "Taraba", "Yobe", "Zamfara"
]