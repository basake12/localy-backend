"""
app/models/__init__.py

Centralized SQLAlchemy model exports.

CHANGES:
  - AdminUser replaces Admin (admin_users table is now separate from users)
  - UserAgreement added (Blueprint §14 / §3 step 8)
  - UserRoleEnum replaces UserRoleEnum (no 'admin' in mobile role enum)
  - CouponRedemption replaces CouponUsage (renamed per Blueprint §9.2)
  - ReelView / ReelLike / ReelComment kept
  - StoryView.viewer_user_id field (renamed from viewer_id per Blueprint §14)
  - CryptoTopUp REMOVED — not in blueprint
  - HotelService → HotelInStayRequest (BUG-13 FIX: naming collision with service layer)
  - DriverSubscriptionPlan REMOVED — riders have no subscription plan (Blueprint §8.1)
"""

from app.models.analytics_model import DailyAnalyticsSnapshot
from app.models.base_model import BaseModel
from app.models.business_model import (
    Business,
    BusinessCategoryEnum,
    BusinessHours,
)
from app.models.address_model import CustomerAddress
from app.models.chat_model import (
    Conversation,
    ConversationTypeEnum,
    Message,
    TypingIndicator,
    UserPresence,
)
from app.models.coupon_model import (
    Coupon,
    CouponRedemption,
    CouponStatus,
    CouponType,
    CouponUsage,   # alias for backward compat
)
from app.models.delivery_model import (
    Delivery,
    DeliveryStatusEnum,
    DeliveryTracking,
    DeliveryTypeEnum,
    DeliveryZone,
    PaymentStatusEnum as DeliveryPaymentStatusEnum,
    RiderEarnings,
    RiderShift,
    VehicleTypeEnum,
)
from app.models.favorites_model import Favorite
from app.models.food_model import (
    CookingBooking,
    CookingService,
    CookingServiceStatusEnum,
    CuisineTypeEnum,
    FoodOrder,
    FoodOrderItem,
    MenuCategory,
    MenuItem,
    OrderStatusEnum as FoodOrderStatusEnum,
    ReservationStatusEnum,
    Restaurant,
    TableReservation,
    TableStatusEnum,
)
from app.models.health_model import (
    Consultation,
    ConsultationStatusEnum,
    ConsultationTypeEnum,
    Doctor,
    DoctorAvailability,
    DoctorSpecializationEnum,
    LabBooking,
    LabBookingStatusEnum,
    LabCenter,
    LabResult,
    LabTest,
    LabTestCategoryEnum,
    Pharmacy,
    PharmacyOrder,
    PharmacyOrderItem,
    PharmacyOrderStatusEnum,
    PharmacyProduct,
    Prescription,
    PrescriptionStatusEnum,
)
from app.models.hotels_model import (
    BookingStatusEnum as HotelBookingStatusEnum,
    Hotel,
    HotelBooking,
    HotelInStayRequest,          # was HotelService — BUG-13 FIX
    PaymentStatusEnum as HotelPaymentStatusEnum,
    Room,
    RoomStatusEnum,
    RoomType,
    ServiceStatusEnum,
)
from app.models.jobs_model import (
    ApplicationStatus,
    ExperienceLevel,
    JobApplication,
    JobPosting,
    JobStatus,
    JobType,
)
from app.models.notifications_model import (
    DeviceToken,
    Notification,
    NotificationCategoryEnum,
    NotificationChannelEnum,
    NotificationPreference,
    NotificationStatusEnum,
)
from app.models.products_model import (
    CartItem,
    OrderItem,
    OrderStatusEnum as ProductOrderStatusEnum,
    PaymentStatusEnum as ProductPaymentStatusEnum,
    Product,
    ProductOrder,
    ProductTypeEnum,
    ProductVariant,
    ProductVendor,
    Wishlist,
)
from app.models.promotions_model import (
    Promotion,
    PromotionRedemption,
    PromotionStatus,
    PromotionType,
    StreakActionType,
    StreakProgress,
)
from app.models.properties_model import (
    ListingTypeEnum,
    OfferStatusEnum,
    Property,
    PropertyAgent,
    PropertyInquiry,
    PropertyOffer,
    PropertyStatusEnum,
    PropertySubtypeEnum,
    PropertyTypeEnum,
    PropertyViewing,
    SavedProperty,
    ViewingStatusEnum,
)
from app.models.referrals_model import Referral, ReferralCode
from app.models.reels_model import Reel, ReelComment, ReelLike, ReelView
from app.models.reviews_model import (
    Review,
    ReviewContextEnum,
    ReviewHelpfulVote,
    ReviewResponse,
    ReviewableTypeEnum,
    ReviewStatusEnum,
)
from app.models.rider_model import Rider  # DriverSubscriptionPlan removed — Blueprint §8.1
from app.models.search_model import SearchQuery
from app.models.services_model import (
    BookingStatusEnum as ServiceBookingStatusEnum,
    PaymentStatusEnum as ServicePaymentStatusEnum,
    PricingTypeEnum,
    Service,
    ServiceAvailability,
    ServiceBooking,
    ServiceLocationTypeEnum,
    ServicePackage,
    ServiceProvider,
)
from app.models.stories_model import Story, StoryTypeEnum, StoryView
from app.models.subscription_model import (
    BillingCycleEnum,
    Subscription,
    SubscriptionPlan,
    SubscriptionPlanTypeEnum,
    SubscriptionStatusEnum,
)
from app.models.tickets_model import (
    BookingStatusEnum as TicketBookingStatusEnum,
    EventCategoryEnum,
    EventTypeEnum,
    PaymentStatusEnum as TicketPaymentStatusEnum,
    SeatMap,
    TicketBooking,
    TicketEvent,
    TicketStatusEnum,
    TicketTier,
    TransportTypeEnum,
)
from app.models.user_model import (
    AdminUser,
    CustomerProfile,
    User,
    UserAgreement,
    UserRoleEnum,
)
from app.models.wallet_model import (
    PlatformRevenue,
    TransactionStatus,
    TransactionStatusEnum,
    TransactionType,
    TransactionTypeEnum,
    Wallet,
    WalletTransaction,
)

__all__ = [
    # Admin
    "AdminUser",
    # Analytics
    "DailyAnalyticsSnapshot",
    # Base
    "BaseModel",
    # Business
    "Business",
    "BusinessCategoryEnum",
    "BusinessHours",
    # Address
    "CustomerAddress",
    # Chat
    "Conversation",
    "ConversationTypeEnum",
    "Message",
    "TypingIndicator",
    "UserPresence",
    # Coupon
    "Coupon",
    "CouponRedemption",
    "CouponStatus",
    "CouponType",
    "CouponUsage",
    # Delivery
    "Delivery",
    "DeliveryPaymentStatusEnum",
    "DeliveryStatusEnum",
    "DeliveryTracking",
    "DeliveryTypeEnum",
    "DeliveryZone",
    "RiderEarnings",
    "RiderShift",
    "VehicleTypeEnum",
    # Favorites
    "Favorite",
    # Food
    "CookingBooking",
    "CookingService",
    "CookingServiceStatusEnum",
    "CuisineTypeEnum",
    "FoodOrder",
    "FoodOrderItem",
    "FoodOrderStatusEnum",
    "MenuCategory",
    "MenuItem",
    "ReservationStatusEnum",
    "Restaurant",
    "TableReservation",
    "TableStatusEnum",
    # Health
    "Consultation",
    "ConsultationStatusEnum",
    "ConsultationTypeEnum",
    "Doctor",
    "DoctorAvailability",
    "DoctorSpecializationEnum",
    "LabBooking",
    "LabBookingStatusEnum",
    "LabCenter",
    "LabResult",
    "LabTest",
    "LabTestCategoryEnum",
    "Pharmacy",
    "PharmacyOrder",
    "PharmacyOrderItem",
    "PharmacyOrderStatusEnum",
    "PharmacyProduct",
    "Prescription",
    "PrescriptionStatusEnum",
    # Hotels
    "Hotel",
    "HotelBooking",
    "HotelBookingStatusEnum",
    "HotelInStayRequest",        # was HotelService — BUG-13 FIX
    "HotelPaymentStatusEnum",
    "Room",
    "RoomStatusEnum",
    "RoomType",
    "ServiceStatusEnum",
    # Jobs
    "ApplicationStatus",
    "ExperienceLevel",
    "JobApplication",
    "JobPosting",
    "JobStatus",
    "JobType",
    # Notifications
    "DeviceToken",
    "Notification",
    "NotificationCategoryEnum",
    "NotificationChannelEnum",
    "NotificationPreference",
    "NotificationStatusEnum",
    # Products
    "CartItem",
    "OrderItem",
    "Product",
    "ProductOrder",
    "ProductOrderStatusEnum",
    "ProductPaymentStatusEnum",
    "ProductTypeEnum",
    "ProductVariant",
    "ProductVendor",
    "Wishlist",
    # Promotions
    "Promotion",
    "PromotionRedemption",
    "PromotionStatus",
    "PromotionType",
    "StreakActionType",
    "StreakProgress",
    # Properties
    "ListingTypeEnum",
    "OfferStatusEnum",
    "Property",
    "PropertyAgent",
    "PropertyInquiry",
    "PropertyOffer",
    "PropertyStatusEnum",
    "PropertySubtypeEnum",
    "PropertyTypeEnum",
    "PropertyViewing",
    "SavedProperty",
    "ViewingStatusEnum",
    # Referrals
    "Referral",
    "ReferralCode",
    # Reels
    "Reel",
    "ReelComment",
    "ReelLike",
    "ReelView",
    # Reviews
    "Review",
    "ReviewContextEnum",
    "ReviewHelpfulVote",
    "ReviewResponse",
    "ReviewStatusEnum",
    "ReviewableTypeEnum",
    # Rider
    "Rider",
    # DriverSubscriptionPlan removed — Blueprint §8.1: riders have no subscription plan
    # Search
    "SearchQuery",
    # Services
    "PricingTypeEnum",
    "Service",
    "ServiceAvailability",
    "ServiceBooking",
    "ServiceBookingStatusEnum",
    "ServiceLocationTypeEnum",
    "ServicePackage",
    "ServicePaymentStatusEnum",
    "ServiceProvider",
    # Stories
    "Story",
    "StoryTypeEnum",
    "StoryView",
    # Subscriptions
    "BillingCycleEnum",
    "Subscription",
    "SubscriptionPlan",
    "SubscriptionPlanTypeEnum",
    "SubscriptionStatusEnum",
    # Tickets
    "EventCategoryEnum",
    "EventTypeEnum",
    "SeatMap",
    "TicketBooking",
    "TicketBookingStatusEnum",
    "TicketEvent",
    "TicketPaymentStatusEnum",
    "TicketStatusEnum",
    "TicketTier",
    "TransportTypeEnum",
    # Users
    "CustomerProfile",
    "User",
    "UserAgreement",
    "UserRoleEnum",
    # Wallet
    "PlatformRevenue",
    "TransactionStatus",
    "TransactionStatusEnum",
    "TransactionType",
    "TransactionTypeEnum",
    "Wallet",
    "WalletTransaction",
]
