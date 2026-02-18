from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Import your Base and all models
from app.core.database import Base

# User Models
from app.models.user import User, CustomerProfile, Admin

# Business Models
from app.models.business import Business, BusinessHours

# Rider Models
from app.models.rider import Rider

# Wallet Models
from app.models.wallet import Wallet, WalletTransaction

# Subscription Models
from app.models.subscription import SubscriptionPlan, Subscription

# Coupon Models
from app.models.coupon import Coupon, CouponUsage

# Favorites Model
from app.models.favorites import Favorite

# Referral Models
from app.models.referrals import ReferralCode, Referral

# Hotel Models
from app.models.hotels import Hotel, RoomType, Room, HotelBooking, HotelService

# Product Models
from app.models.products import (
    ProductVendor, Product, ProductVariant, ProductOrder,
    OrderItem, CartItem, Wishlist
)

# Service Models
from app.models.services import (
    ServiceProvider, Service, ServiceAvailability, ServiceBooking, ServicePackage
)

# Delivery Models
from app.models.delivery import (
    Delivery, DeliveryTracking, RiderEarnings, DeliveryZone, RiderShift
)

# Food/Restaurant Models
from app.models.food import (
    Restaurant, MenuCategory, MenuItem, TableReservation,
    FoodOrder, FoodOrderItem, CookingService, CookingBooking
)

# Health Models
from app.models.health import (
    Doctor, DoctorAvailability, Consultation, Prescription,
    Pharmacy, PharmacyOrder, PharmacyOrderItem,
    LabCenter, LabTest, LabBooking, LabResult
)

# Property Models
from app.models.properties import (
    PropertyAgent, Property, PropertyViewing, PropertyOffer,
    SavedProperty, PropertyInquiry
)

# Ticket Models
from app.models.tickets import (
    TicketEvent, TicketTier, TicketBooking, SeatMap
)

# Jobs Models
from app.models.jobs import JobPosting, JobApplication

# Review Models
from app.models.reviews import Review, ReviewHelpfulVote, ReviewResponse

# Chat Models
from app.models.chat import Conversation, Message, UserPresence, TypingIndicator

# Story Models
from app.models.stories import Story, StoryView

# Reel Models
from app.models.reels import Reel, ReelLike, ReelComment, ReelView

# Notification Models
from app.models.notifications import Notification, NotificationPreference, DeviceToken

# Search Models
from app.models.search import SearchQuery

# Analytics Models
from app.models.analytics import DailyAnalyticsSnapshot

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()