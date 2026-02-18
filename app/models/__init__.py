"""
SQLAlchemy Models Package
Imports all models for the application
"""

# Base Model
from app.models.base import BaseModel

# User Models
from app.models.user import (
    User,
    UserTypeEnum,
    UserStatusEnum,
    CustomerProfile,
    Admin,
)

# Business Models
from app.models.business import (
    Business,
    BusinessCategoryEnum,
    VerificationBadgeEnum,
    BusinessHours,
)

# Rider Models
from app.models.rider import Rider

# Wallet Models
from app.models.wallet import (
    Wallet,
    WalletTransaction,
    TransactionTypeEnum,
    TransactionStatusEnum,
)

# Subscription Models
from app.models.subscription import (
    SubscriptionPlan,
    Subscription,
    SubscriptionPlanTypeEnum,
    BillingCycleEnum,
)

# Coupon Models
from app.models.coupon import (
    Coupon,
    CouponUsage,
    CouponType,
    CouponStatus,
)

# Favorites Model
from app.models.favorites import Favorite

# Referral Models
from app.models.referrals import (
    ReferralCode,
    Referral,
    ReferralStatus,
)

# Hotel Models
from app.models.hotels import (
    Hotel,
    RoomType,
    Room,
    HotelBooking,
    HotelService,
    RoomStatusEnum,
    BookingStatusEnum as HotelBookingStatusEnum,
    PaymentStatusEnum as HotelPaymentStatusEnum,
    ServiceStatusEnum,
)

# Food/Restaurant Models
from app.models.food import (
    Restaurant,
    MenuItem,
    MenuCategory,
    FoodOrder,
    FoodOrderItem,
    TableReservation,
    CookingService,
    CookingBooking,
    CuisineTypeEnum,
    OrderStatusEnum as FoodOrderStatusEnum,
    TableStatusEnum,
    ReservationStatusEnum,
)

# Product Models
from app.models.products import (
    ProductVendor,
    Product,
    ProductVariant,
    ProductOrder,
    OrderItem,
    CartItem,
    Wishlist,
    ProductTypeEnum,
    OrderStatusEnum as ProductOrderStatusEnum,
    PaymentStatusEnum as ProductPaymentStatusEnum,
)

# Service Models
from app.models.services import (
    ServiceProvider,
    Service,
    ServiceAvailability,
    ServiceBooking,
    ServicePackage,
    ServiceLocationTypeEnum,
    BookingStatusEnum as ServiceBookingStatusEnum,
    PaymentStatusEnum as ServicePaymentStatusEnum,
    PricingTypeEnum,
)

# Health Models
# NOTE: There is no HealthProvider model. Health businesses use Doctor, Pharmacy, and LabCenter directly.
from app.models.health import (
    Doctor,
    DoctorAvailability,
    Consultation,
    Prescription,
    Pharmacy,
    PharmacyOrder,
    PharmacyOrderItem,
    LabCenter,
    LabTest,
    LabBooking,
    LabResult,
    DoctorSpecializationEnum,
    ConsultationTypeEnum,
    ConsultationStatusEnum,
    PrescriptionStatusEnum,
    PharmacyOrderStatusEnum,
    LabBookingStatusEnum,
    LabTestCategoryEnum,
)

# Property Models
from app.models.properties import (
    PropertyAgent,
    Property,
    PropertyViewing,
    PropertyOffer,
    SavedProperty,
    PropertyInquiry,
    PropertyTypeEnum,
    PropertySubtypeEnum,
    ListingTypeEnum,
    PropertyStatusEnum,
    ViewingStatusEnum,
    OfferStatusEnum,
)

# Ticket Models
from app.models.tickets import (
    TicketEvent,
    TicketTier,
    TicketBooking,
    SeatMap,
    EventCategoryEnum,
    TransportTypeEnum,
    TicketStatusEnum,
    BookingStatusEnum as TicketBookingStatusEnum,
    PaymentStatusEnum as TicketPaymentStatusEnum,
)

# Jobs Models
from app.models.jobs import (
    JobPosting,
    JobApplication,
    JobStatus,
    JobType,
    ExperienceLevel,
    ApplicationStatus,
)

# Delivery Models
from app.models.delivery import (
    Delivery,
    DeliveryTracking,
    RiderEarnings,
    DeliveryZone,
    RiderShift,
    DeliveryTypeEnum,
    DeliveryStatusEnum,
    PaymentStatusEnum as DeliveryPaymentStatusEnum,
    VehicleTypeEnum,
)

# Review Models
from app.models.reviews import (
    Review,
    ReviewHelpfulVote,
    ReviewResponse,
    ReviewableTypeEnum,
    ReviewStatusEnum,
    ReviewContextEnum,
)

# Chat Models
from app.models.chat import (
    Conversation,
    Message,
    UserPresence,
    TypingIndicator,
    MessageTypeEnum,
    ConversationTypeEnum,
)

# Social/Content Models
from app.models.stories import (
    Story,
    StoryView,
    StoryTypeEnum,
)

from app.models.reels import (
    Reel,
    ReelLike,
    ReelComment,
    ReelView,
)

# Notification Models
from app.models.notifications import (
    Notification,
    NotificationPreference,
    DeviceToken,
    NotificationChannelEnum,
    NotificationStatusEnum,
    NotificationCategoryEnum,
)

# Search Models
from app.models.search import SearchQuery

# Analytics Models
from app.models.analytics import DailyAnalyticsSnapshot

# Explicit __all__ for clean imports
__all__ = [
    # Base
    "BaseModel",

    # Users
    "User",
    "UserTypeEnum",
    "UserStatusEnum",
    "CustomerProfile",
    "Admin",

    # Business
    "Business",
    "BusinessCategoryEnum",
    "VerificationBadgeEnum",
    "BusinessHours",

    # Riders
    "Rider",

    # Wallet
    "Wallet",
    "WalletTransaction",
    "TransactionTypeEnum",
    "TransactionStatusEnum",

    # Subscriptions
    "SubscriptionPlan",
    "Subscription",
    "SubscriptionPlanTypeEnum",
    "BillingCycleEnum",

    # Coupons
    "Coupon",
    "CouponUsage",
    "CouponType",
    "CouponStatus",

    # Favorites
    "Favorite",

    # Referrals
    "ReferralCode",
    "Referral",
    "ReferralStatus",

    # Hotels
    "Hotel",
    "RoomType",
    "Room",
    "HotelBooking",
    "HotelService",
    "RoomStatusEnum",
    "HotelBookingStatusEnum",
    "HotelPaymentStatusEnum",
    "ServiceStatusEnum",

    # Food/Restaurant
    "Restaurant",
    "MenuItem",
    "MenuCategory",
    "FoodOrder",
    "FoodOrderItem",
    "TableReservation",
    "CookingService",
    "CookingBooking",
    "CuisineTypeEnum",
    "FoodOrderStatusEnum",
    "TableStatusEnum",
    "ReservationStatusEnum",

    # Products
    "ProductVendor",
    "Product",
    "ProductVariant",
    "ProductOrder",
    "OrderItem",
    "CartItem",
    "Wishlist",
    "ProductTypeEnum",
    "ProductOrderStatusEnum",
    "ProductPaymentStatusEnum",

    # Services
    "ServiceProvider",
    "Service",
    "ServiceAvailability",
    "ServiceBooking",
    "ServicePackage",
    "ServiceLocationTypeEnum",
    "ServiceBookingStatusEnum",
    "ServicePaymentStatusEnum",
    "PricingTypeEnum",

    # Health
    "Doctor",
    "DoctorAvailability",
    "Consultation",
    "Prescription",
    "Pharmacy",
    "PharmacyOrder",
    "PharmacyOrderItem",
    "LabCenter",
    "LabTest",
    "LabBooking",
    "LabResult",
    "DoctorSpecializationEnum",
    "ConsultationTypeEnum",
    "ConsultationStatusEnum",
    "PrescriptionStatusEnum",
    "PharmacyOrderStatusEnum",
    "LabBookingStatusEnum",
    "LabTestCategoryEnum",

    # Properties
    "PropertyAgent",
    "Property",
    "PropertyViewing",
    "PropertyOffer",
    "SavedProperty",
    "PropertyInquiry",
    "PropertyTypeEnum",
    "PropertySubtypeEnum",
    "ListingTypeEnum",
    "PropertyStatusEnum",
    "ViewingStatusEnum",
    "OfferStatusEnum",

    # Tickets
    "TicketEvent",
    "TicketTier",
    "TicketBooking",
    "SeatMap",
    "EventCategoryEnum",
    "TransportTypeEnum",
    "TicketStatusEnum",
    "TicketBookingStatusEnum",
    "TicketPaymentStatusEnum",

    # Jobs
    "JobPosting",
    "JobApplication",
    "JobStatus",
    "JobType",
    "ExperienceLevel",
    "ApplicationStatus",

    # Delivery
    "Delivery",
    "DeliveryTracking",
    "RiderEarnings",
    "DeliveryZone",
    "RiderShift",
    "DeliveryTypeEnum",
    "DeliveryStatusEnum",
    "DeliveryPaymentStatusEnum",
    "VehicleTypeEnum",

    # Reviews
    "Review",
    "ReviewHelpfulVote",
    "ReviewResponse",
    "ReviewableTypeEnum",
    "ReviewStatusEnum",
    "ReviewContextEnum",

    # Chat
    "Conversation",
    "Message",
    "UserPresence",
    "TypingIndicator",
    "MessageTypeEnum",
    "ConversationTypeEnum",

    # Social/Content
    "Story",
    "StoryView",
    "StoryTypeEnum",
    "Reel",
    "ReelLike",
    "ReelComment",
    "ReelView",

    # Notifications
    "Notification",
    "NotificationPreference",
    "DeviceToken",
    "NotificationChannelEnum",
    "NotificationStatusEnum",
    "NotificationCategoryEnum",

    # Search
    "SearchQuery",

    # Analytics
    "DailyAnalyticsSnapshot",
]