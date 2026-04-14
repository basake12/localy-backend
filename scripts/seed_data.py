"""
scripts/seed_data.py

Seeds the database with reference / demo data for development.
Safe to re-run — skips rows that already exist.

    python scripts/seed_data.py

DO NOT run in production.
"""
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.core.constants import (
    UserType, UserStatus,
    SubscriptionPlanType, BusinessCategory,
)
from app.models.user import User
from app.models.subscription import SubscriptionPlan
from app.core.security import hash_password
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# SEED DATA
# ─────────────────────────────────────────────

SUBSCRIPTION_PLANS = [
    {
        "name":         SubscriptionPlanType.FREE,
        "display_name": "Free",
        "monthly_price": 0.0,
        "annual_price":  0.0,
        "features":      ["Standard search visibility", "Basic profile"],
    },
    {
        "name":         SubscriptionPlanType.STARTER,
        "display_name": "Starter",
        "monthly_price": 5500.0,
        "annual_price":  55000.0,
        "features":      ["Featured tag", "Light blue highlight", "1–2 featured days/week"],
    },
    {
        "name":         SubscriptionPlanType.PRO,
        "display_name": "Pro",
        "monthly_price": 16500.0,
        "annual_price":  165000.0,
        "features":      ["Gold badge", "3–5 featured days/week", "Home page carousel"],
    },
    {
        "name":         SubscriptionPlanType.ENTERPRISE,
        "display_name": "Enterprise",
        "monthly_price": 55000.0,
        "annual_price":  550000.0,
        "features":      ["Platinum badge", "Top 1–3 positions", "Dedicated account manager"],
    },
    {
        "name":         SubscriptionPlanType.PRO_DRIVER,
        "display_name": "Pro Driver",
        "monthly_price": 8500.0,
        "annual_price":  85000.0,
        "features":      ["Priority job notifications", "Purple badge", "Zone preference"],
    },
]

DEMO_USERS = [
    {
        "full_name":    "Test Customer",
        "email":        "customer@localy.test",
        "phone":        "+2348000000001",
        "password":     "Test@1234",
        "user_type":    UserType.CUSTOMER,
    },
    {
        "full_name":    "Test Business",
        "email":        "business@localy.test",
        "phone":        "+2348000000002",
        "password":     "Test@1234",
        "user_type":    UserType.BUSINESS,
    },
    {
        "full_name":    "Test Rider",
        "email":        "rider@localy.test",
        "phone":        "+2348000000003",
        "password":     "Test@1234",
        "user_type":    UserType.RIDER,
    },
]


def seed_subscription_plans(db) -> None:
    logger.info("Seeding subscription plans…")
    for plan_data in SUBSCRIPTION_PLANS:
        existing = db.query(SubscriptionPlan).filter_by(name=plan_data["name"]).first()
        if existing:
            logger.info(f"  SKIP  {plan_data['name']} (already exists)")
            continue
        plan = SubscriptionPlan(**plan_data)
        db.add(plan)
        logger.info(f"  ADD   {plan_data['name']}")
    db.commit()


def seed_demo_users(db) -> None:
    logger.info("Seeding demo users…")
    for u in DEMO_USERS:
        existing = db.query(User).filter_by(email=u["email"]).first()
        if existing:
            logger.info(f"  SKIP  {u['email']} (already exists)")
            continue
        user = User(
            id=uuid.uuid4(),
            full_name=u["full_name"],
            email=u["email"],
            phone=u["phone"],
            hashed_password=hash_password(u["password"]),
            user_type=u["user_type"],
            status=UserStatus.ACTIVE,
            is_email_verified=True,
            is_phone_verified=True,
        )
        db.add(user)
        logger.info(f"  ADD   {u['email']} ({u['user_type'].value})")
    db.commit()


def main() -> None:
    from app.config import settings
    if settings.APP_ENV == "production":
        print("❌  Refusing to seed production database.")
        sys.exit(1)

    db = SessionLocal()
    try:
        seed_subscription_plans(db)
        seed_demo_users(db)
        logger.info("✅  Seed completed.")
    except Exception as exc:
        db.rollback()
        logger.error(f"Seed failed: {exc}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()