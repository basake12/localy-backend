"""
app/api/v1/router.py

FIXES:
  [AUDIT BUG-8 FIX] webhooks router added at prefix "/webhooks".

  Root cause:
    Monnify and Paystack webhook endpoints were inside wallet.py, which is
    mounted at prefix "/wallet". Actual paths were:
      /api/v1/wallet/webhooks/monnify/funding   ← WRONG (404 from Monnify)
      /api/v1/wallet/webhooks/paystack/payment  ← WRONG (404 from Paystack)

    Blueprint §15 requires:
      POST /api/v1/webhooks/monnify/funding     ← CORRECT
      POST /api/v1/webhooks/paystack/payment    ← CORRECT

  Fix:
    - New webhooks.py router imported and mounted at "/webhooks".
    - wallet.py no longer contains any webhook routes.
"""
from fastapi import APIRouter

from app.api.v1 import auth
from app.api.v1 import users
from app.api.v1 import hotels
from app.api.v1 import products
from app.api.v1 import services
from app.api.v1 import deliveries
from app.api.v1 import food
from app.api.v1 import tickets
from app.api.v1 import properties
from app.api.v1 import health
from app.api.v1 import chat
from app.api.v1 import reviews
from app.api.v1 import notifications
from app.api.v1 import admin
from app.api.v1 import jobs
from app.api.v1 import wallet
from app.api.v1 import businesses
from app.api.v1 import riders
from app.api.v1 import subscriptions
from app.api.v1 import analytics
from app.api.v1 import stories
from app.api.v1 import reels
from app.api.v1 import search
from app.api.v1 import coupons
from app.api.v1 import favorites
from app.api.v1 import referrals
from app.api.v1 import promotions
# [BUG-8 FIX] — Dedicated webhooks router at /webhooks prefix.
# Blueprint §15: POST /webhooks/monnify/funding + POST /webhooks/paystack/payment
from app.api.v1 import webhooks

api_router = APIRouter()

# ── Core ──────────────────────────────────────────────────────────────────────
api_router.include_router(auth.router,           prefix="/auth",          tags=["Authentication"])
api_router.include_router(users.router,          prefix="/users",         tags=["Users"])
api_router.include_router(wallet.router,         prefix="/wallet",        tags=["Wallet & Payments"])

# [BUG-8 FIX] Webhooks at /webhooks — NOT nested under /wallet.
# Blueprint §15: these endpoints accept no JWT — HMAC signature verification only.
# include_in_schema=False is set per-endpoint in webhooks.py (not needed here).
api_router.include_router(webhooks.router,       prefix="/webhooks",      tags=["Webhooks"])

# ── User Management ───────────────────────────────────────────────────────────
api_router.include_router(businesses.router,     prefix="/businesses",    tags=["Businesses"])
api_router.include_router(riders.router,         prefix="/riders",        tags=["Riders"])
api_router.include_router(subscriptions.router,  prefix="/subscriptions", tags=["Subscriptions"])

# ── Commerce modules ──────────────────────────────────────────────────────────
api_router.include_router(hotels.router,         prefix="/hotels",        tags=["Hotels"])
api_router.include_router(products.router,       prefix="/products",      tags=["Products"])
api_router.include_router(services.router,       prefix="/services",      tags=["Services"])
api_router.include_router(food.router,           prefix="/food",          tags=["Food & Restaurants"])
api_router.include_router(tickets.router,        prefix="/tickets",       tags=["Tickets & Events"])
api_router.include_router(properties.router,     prefix="/properties",    tags=["Properties"])
api_router.include_router(health.router,         prefix="/health",        tags=["Health"])
api_router.include_router(deliveries.router,     prefix="/deliveries",    tags=["Deliveries"])
api_router.include_router(jobs.router,           prefix="/jobs",          tags=["Jobs & Careers"])

# ── Promotions & Engagement ───────────────────────────────────────────────────
api_router.include_router(promotions.router,     prefix="/promotions",    tags=["Promotions"])
api_router.include_router(coupons.router,        prefix="/coupons",       tags=["Coupons"])
api_router.include_router(favorites.router,      prefix="/favorites",     tags=["Favorites"])
api_router.include_router(referrals.router,      prefix="/referrals",     tags=["Referrals"])

# ── Supporting features ───────────────────────────────────────────────────────
api_router.include_router(chat.router,           prefix="/chat",          tags=["Chat & Messaging"])
api_router.include_router(reviews.router,        prefix="/reviews",       tags=["Reviews & Ratings"])
api_router.include_router(notifications.router,  prefix="/notifications", tags=["Notifications"])
api_router.include_router(stories.router,        prefix="/stories",       tags=["Stories"])
api_router.include_router(reels.router,          prefix="/reels",         tags=["Reels"])
api_router.include_router(search.router,         prefix="/search",        tags=["Search"])

# ── Admin ─────────────────────────────────────────────────────────────────────
api_router.include_router(admin.router,          prefix="/admin",         tags=["Admin Dashboard"])
api_router.include_router(analytics.router,      prefix="/analytics",     tags=["Analytics"])


# ── Health check ──────────────────────────────────────────────────────────────
@api_router.get("/health", tags=["Health"])
async def api_health():
    return {"success": True, "data": {"status": "healthy", "message": "API is running"}}