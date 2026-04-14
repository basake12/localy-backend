"""
seed_subscription_plans.py

Run once to populate the subscription_plans table.

Usage (from your project root, inside the venv):
    python seed_subscription_plans.py

Pricing source: Localy Blueprint v2, Section 8.1
    Annual billing = 10 × monthly price (2 months free per Blueprint §8)

    Free        ₦0        / ₦0
    Starter     ₦5,500    / ₦55,000
    Pro         ₦16,500   / ₦165,000
    Enterprise  ₦55,000   / ₦550,000
    Pro Driver  ₦2,500    / ₦25,000   ← not in Blueprint; adjust if needed
"""

import sys
import os

# ── Make sure app imports resolve ──────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from decimal import Decimal
from app.core.database import SessionLocal
from app.models.subscription_model import SubscriptionPlan, SubscriptionPlanTypeEnum

# ── Plan definitions ───────────────────────────────────────────────────────────
PLANS = [
    {
        "plan_type": SubscriptionPlanTypeEnum.FREE,
        "name": "Free",
        "monthly_price": Decimal("0.00"),
        "annual_price": Decimal("0.00"),
        "features": [
            "Up to 3 listings",
            "Basic analytics",
            "Bottom of search results",
            "Community support",
        ],
    },
    {
        "plan_type": SubscriptionPlanTypeEnum.STARTER,
        "name": "Starter",
        "monthly_price": Decimal("5500.00"),
        "annual_price": Decimal("55000.00"),   # 10 × monthly (2 months free)
        "features": [
            "Higher search ranking",
            "Featured placement 1-2 days/week",
            "Light blue featured badge",
            "Reels & Stories posting",
            "Product/room tagging in reels",
            "Jobs posting",
            "Standard analytics",
            "Email support",
            "Basic promotions & cashback tools",
        ],
    },
    {
        "plan_type": SubscriptionPlanTypeEnum.PRO,
        "name": "Pro",
        "monthly_price": Decimal("16500.00"),
        "annual_price": Decimal("165000.00"),
        "features": [
            "Top 5-10 search placement",
            "Featured placement 3-5 days/week",
            "Gold Pro badge",
            "Home page carousel inclusion",
            "Unlimited 48h manual listing boosts",
            "Reels & Stories priority feed",
            "Product/room tagging in reels",
            "Jobs posting",
            "Property listings (agents)",
            "Advanced analytics",
            "Priority chat support",
            "Full promotions & cashback tools",
            "Limited API integrations",
        ],
    },
    {
        "plan_type": SubscriptionPlanTypeEnum.ENTERPRISE,
        "name": "Enterprise",
        "monthly_price": Decimal("55000.00"),
        "annual_price": Decimal("550000.00"),
        "features": [
            "Top 1-3 search placement",
            "Near-permanent featured placement",
            "Platinum animated badge",
            "Home page carousel priority slot",
            "Scheduled boost campaigns",
            "Top of feed reels & stories",
            "Product/room tagging in reels",
            "Jobs posting",
            "Property listings (agents)",
            "Full analytics + export",
            "Dedicated account manager",
            "Custom cashback campaigns",
            "Custom category page banner",
            "Editor's Choice badge",
            "Full API integrations",
            "Pinnable stories (up to 7 days)",
        ],
    },
    {
        "plan_type": SubscriptionPlanTypeEnum.PRO_DRIVER,
        "name": "Pro Driver",
        "monthly_price": Decimal("2500.00"),   # ⚠ Not in Blueprint — adjust if needed
        "annual_price": Decimal("25000.00"),
        "features": [
            "Priority delivery job visibility",
            "Verified rider badge",
            "Standard analytics",
            "Email support",
        ],
    },
]


def seed():
    db = SessionLocal()
    try:
        created = 0
        skipped = 0

        for plan_data in PLANS:
            existing = (
                db.query(SubscriptionPlan)
                .filter(SubscriptionPlan.plan_type == plan_data["plan_type"])
                .first()
            )

            if existing:
                print(f"  SKIP  {plan_data['name']} — already exists (id: {existing.id})")
                skipped += 1
                continue

            plan = SubscriptionPlan(**plan_data)
            db.add(plan)
            db.flush()   # get the id before commit
            print(f"  ADD   {plan_data['name']} — id: {plan.id}")
            created += 1

        db.commit()
        print(f"\nDone. {created} plan(s) created, {skipped} skipped.")

    except Exception as exc:
        db.rollback()
        print(f"\nERROR: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()