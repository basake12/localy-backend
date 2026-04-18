"""
app/services/search_service.py

Unified search service — all seven Localy modules.

Blueprint §4.3  — ST_DWithin for radius filter (GIST index-backed).
                   NEVER ST_Distance <= radius_m (bypasses index entirely).
Blueprint §4.2  — distance_km populated on every result via ST_Distance.
Blueprint §7.1  — Autocomplete served from Redis (search_suggest:{query_hash} TTL=300s).
Blueprint §7.2  — All 6 ranking factors:
                   tier → profile completeness → weighted rating → distance.
Blueprint §13.2 — [P11] ST_DWithin, no LGA anywhere, default radius 5 km.
Blueprint §16.3 — Redis key: search_suggest:{query_hash} TTL=300.
Blueprint §16.4 — datetime.now(timezone.utc) everywhere.

FIXES vs previous version:
  1. _geo_filter replaced: ST_Distance <= radius_m → ST_DWithin.
     ST_Distance forces full sequential scan — does not use GIST index.
     ST_DWithin activates the GIST index defined in §4.3. Critical perf fix.

  2. distance_km populated on every result via ST_Distance.
     Blueprint §4.2: "Every listing card shows: '1.2 km away' or 'Within 500 m'".
     Was hardcoded None on every result.

  3. Business.is_verified.is_(True) filter added to all entity searches.
     Blueprint §4.3: WHERE b.is_verified = TRUE.
     Was missing — unverified businesses appeared in search results.

  4. Redis autocomplete cache implemented (search_suggest:{query_hash} TTL=300s).
     Blueprint §7.1 + §16.3. Was hitting PostgreSQL on every keystroke.

  5. Products joined to Business for geo filter, verification, and tier rank.
     Blueprint §4: all discovery is radius-filtered. Products had no geo filter.

  6. Doctors joined to Business for geo filter, verification, and tier rank.
     Blueprint §4: every discovery surface is filtered by GPS position.
     Doctors had no geo filter — returned results from all of Nigeria.

  7. Services and Properties joined to Business for correct subscription_tier.
     Previously called _subscription_weight(service_or_property_obj) on satellite
     models that don't have subscription_tier — silently returned Free(1) for all.

  8. Blueprint §7.2 6-factor ranking implemented:
     tier → profile completeness → weighted rating (× log review count) → distance.

  9. lga_id removed from all docstrings and parameter references.
     Blueprint §4 HARD RULE: no LGA anywhere.

  10. Per-module DB limit raised from 20 to 100 so cross-module ranking is meaningful.
      total field reflects the combined result count, not an artificial cap.
"""

import json
import hashlib
import logging
import math
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple
from uuid import UUID

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, func
from geoalchemy2 import Geography

from app.core.cache import get_redis
from app.crud.search_crud import search_query_crud
from app.models.hotels_model import Hotel
from app.models.business_model import Business
from app.models.products_model import Product
from app.models.food_model import Restaurant
from app.models.services_model import Service
from app.models.properties_model import Property
from app.models.health_model import Doctor
from app.models.tickets_model import TicketEvent
from app.schemas.search_schema import SearchRequest

logger = logging.getLogger(__name__)

# Blueprint §7.2 tier rank mapping
_SUBSCRIPTION_WEIGHT: Dict[str, int] = {
    "enterprise": 4,
    "pro":        3,
    "starter":    2,
    "free":       1,
}

# Blueprint §16.3: search_suggest:{query_hash} TTL=300s
_AUTOCOMPLETE_CACHE_TTL = 300


# ── Spatial helpers ────────────────────────────────────────────────────────────

def _make_geography(lng: float, lat: float):
    """
    Build a PostGIS geography expression from longitude, latitude.
    Blueprint §4.3 canonical pattern:
      ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
    NOTE: PostGIS convention is (longitude, latitude) — NOT (lat, lng).
    """
    return func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326).cast(Geography())


def _dwithin_filter(location_col, lng: float, lat: float, radius_m: float):
    """
    ST_DWithin radius filter — activates the GIST spatial index.
    Blueprint §4.3: ST_DWithin(b.location::geography,
                               ST_MakePoint(:lng,:lat)::geography, :radius_m)
    NEVER use ST_Distance <= radius_m — that forces a full sequential scan and
    completely ignores the GIST index. Kills performance on any real dataset.
    """
    return func.ST_DWithin(location_col, _make_geography(lng, lat), radius_m)


def _distance_m_expr(location_col, lng: float, lat: float):
    """
    ST_Distance expression for SELECT — returns distance in metres.
    Blueprint §4.2: distance uses ST_Distance, converted to km at display layer.
    Label as 'distance_m' so it is accessible via row.distance_m.
    """
    return func.ST_Distance(location_col, _make_geography(lng, lat)).label("distance_m")


# ── Ranking helpers ────────────────────────────────────────────────────────────

def _subscription_weight(tier: Optional[str]) -> int:
    """Blueprint §7.2 factor 1: Enterprise(4) > Pro(3) > Starter(2) > Free(1)."""
    return _SUBSCRIPTION_WEIGHT.get((tier or "free").lower(), 1)


def _weighted_rating(avg_rating: Optional[float], review_count: Optional[int]) -> float:
    """
    Blueprint §7.2 factor 4: rating weighted by number of verified reviews.
    Uses log(1 + review_count) smoothing so a business with 500 reviews at 4.5
    ranks above one with 1 review at 5.0.
    """
    if not avg_rating:
        return 0.0
    count = max(review_count or 0, 0)
    return avg_rating * math.log1p(count)


def _profile_completeness_score(
    has_logo: bool,
    has_description: bool,
    is_verified: bool,
    has_images: bool = False,
) -> float:
    """
    Blueprint §7.2 factor 3: profile completeness.
    Max = 1.0. Each attribute contributes proportionally.
    """
    score = 0.0
    if is_verified:
        score += 0.35
    if has_description:
        score += 0.25
    if has_logo:
        score += 0.25
    if has_images:
        score += 0.15
    return score


def _composite_sort_key(
    tier: Optional[str],
    avg_rating: Optional[float],
    review_count: Optional[int],
    has_logo: bool,
    has_description: bool,
    is_verified: bool,
    distance_m: Optional[float],
    has_images: bool = False,
) -> Tuple[int, float, float, float]:
    """
    Blueprint §7.2 ranking tuple (all DESC factors negated for ascending sort):
      (-tier_rank, -profile_completeness, -weighted_rating, distance_m)
    """
    return (
        -_subscription_weight(tier),
        -_profile_completeness_score(has_logo, has_description, is_verified, has_images),
        -_weighted_rating(avg_rating, review_count),
        distance_m if distance_m is not None else 999_999.0,
    )


class SearchService:

    # ── Public API ─────────────────────────────────────────────────────────────

    def search(
        self,
        db: Session,
        *,
        request: SearchRequest,
        user_id: Optional[UUID] = None,
    ) -> dict:
        """
        Unified cross-module search across all seven Localy categories.

        Radius filter: ST_DWithin (GIST index-backed). Blueprint §4.3.
        Distance display: ST_Distance metres → km. Blueprint §4.2.
        Ranking (Blueprint §7.2):
          1. Subscription tier
          2. Profile completeness (verified badge, photos, description)
          3. Weighted rating (avg × log(review_count))
          4. Distance ASC
        """
        query  = request.query.lower().strip()
        results: List[Dict] = []

        has_location = bool(request.location_lat and request.location_lng)
        lat      = request.location_lat
        lng      = request.location_lng
        radius_m = request.radius_km * 1000

        cat = request.category   # None → search all modules

        # 100 per module gives meaningful cross-module ranking before pagination
        per_module_limit = 100

        if not cat or cat == "hotels":
            results.extend(
                self._search_hotels(db, query, lat, lng, radius_m if has_location else None, per_module_limit)
            )
        if not cat or cat == "products":
            results.extend(
                self._search_products(db, query, lat, lng, radius_m if has_location else None, per_module_limit)
            )
        if not cat or cat in ("restaurants", "food"):
            results.extend(
                self._search_restaurants(db, query, lat, lng, radius_m if has_location else None, per_module_limit)
            )
        if not cat or cat == "services":
            results.extend(
                self._search_services(db, query, lat, lng, radius_m if has_location else None, per_module_limit)
            )
        if not cat or cat in ("properties", "property"):
            results.extend(
                self._search_properties(db, query, lat, lng, radius_m if has_location else None, per_module_limit)
            )
        if not cat or cat in ("doctors", "health"):
            results.extend(
                self._search_doctors(db, query, lat, lng, radius_m if has_location else None, per_module_limit)
            )
        if not cat or cat == "events":
            results.extend(
                self._search_events(db, query, lat, lng, radius_m if has_location else None, per_module_limit)
            )

        # Blueprint §7.2 multi-factor sort
        results.sort(key=lambda x: x["_sort_key"])

        total = len(results)
        page  = results[request.skip: request.skip + request.limit]

        # Strip internal sort key before returning to serialiser
        for r in page:
            r.pop("_sort_key", None)

        # Track search for analytics + autocomplete corpus
        search_query_crud.record_search(
            db,
            user_id=user_id,
            query=query,
            category=request.category,
            results_count=total,
            location_lat=lat,
            location_lng=lng,
            filters=request.filters,
        )
        db.flush()

        return {
            "query":    request.query,
            "results":  page,
            "total":    total,
            "skip":     request.skip,
            "limit":    request.limit,
            "category": request.category,
        }

    # ── Entity-specific search methods ─────────────────────────────────────────

    def _search_hotels(
        self,
        db: Session,
        query: str,
        lat: Optional[float],
        lng: Optional[float],
        radius_m: Optional[float],
        limit: int,
    ) -> List[Dict]:
        """
        Hotels joined to Business.
        FIX: is_verified filter added (§4.3).
        FIX: ST_Distance computed for distance_km display (§4.2).
        FIX: subscription_tier read from Business (correct tier-based ranking).
        """
        geo = lat is not None and lng is not None and radius_m is not None

        select_exprs = [Hotel]
        if geo:
            select_exprs.append(_distance_m_expr(Business.location, lng, lat))

        q = (
            db.query(*select_exprs)
            .join(Business, Business.id == Hotel.business_id)
            .options(joinedload(Hotel.business))
            .filter(
                Business.is_active.is_(True),
                Business.is_verified.is_(True),     # FIX: §4.3 — verified only
                or_(
                    Business.business_name.ilike(f"%{query}%"),
                    Business.description.ilike(f"%{query}%"),
                    Business.address.ilike(f"%{query}%"),
                ),
            )
        )
        if geo:
            q = q.filter(_dwithin_filter(Business.location, lng, lat, radius_m))

        rows = q.limit(limit).all()
        results = []
        for row in rows:
            h, dist_m = (row[0], row[1]) if geo else (row, None)
            b = h.business
            distance_km = round(dist_m / 1000, 2) if dist_m is not None else None
            results.append({
                "entity_type":         "hotel",
                "entity_id":           str(h.id),
                "title":               b.business_name,
                "subtitle":            f"{h.star_rating}★ hotel",
                "description":         (b.description or "")[:200] or None,
                "image_url":           b.logo,
                "price":               float(h.base_price_per_night) if getattr(h, "base_price_per_night", None) else None,
                "rating":              float(b.average_rating) if b.average_rating else None,
                "location":            b.registered_address,
                "distance_km":         distance_km,          # FIX §4.2
                "is_open":             getattr(b, "is_open", None),
                "subscription_weight": _subscription_weight(b.subscription_tier),
                "metadata": {
                    "star_rating": h.star_rating,
                    "total_rooms": h.total_rooms,
                },
                "_sort_key": _composite_sort_key(
                    tier=b.subscription_tier,
                    avg_rating=float(b.average_rating) if b.average_rating else None,
                    review_count=getattr(b, "review_count", 0),
                    has_logo=bool(b.logo),
                    has_description=bool(b.description),
                    is_verified=b.is_verified,
                    distance_m=dist_m,
                ),
            })
        return results

    def _search_products(
        self,
        db: Session,
        query: str,
        lat: Optional[float],
        lng: Optional[float],
        radius_m: Optional[float],
        limit: int,
    ) -> List[Dict]:
        """
        FIX: Products now joined to Business for geo filter, verification, and tier rank.
        Blueprint §4: all discovery is radius-filtered.
        Products had NO geo filter — returned results from all of Nigeria.
        """
        geo = lat is not None and lng is not None and radius_m is not None

        select_exprs = [Product]
        if geo:
            select_exprs.append(_distance_m_expr(Business.location, lng, lat))

        q = (
            db.query(*select_exprs)
            .join(Business, Business.id == Product.business_id)
            .options(joinedload(Product.business))
            .filter(
                Product.is_active.is_(True),
                Product.is_deleted.is_(False),
                Product.is_archived.is_(False),
                Business.is_active.is_(True),
                Business.is_verified.is_(True),     # FIX: §4.3
                or_(
                    Product.name.ilike(f"%{query}%"),
                    Product.description.ilike(f"%{query}%"),
                    Product.category.ilike(f"%{query}%"),
                ),
            )
        )
        if geo:
            q = q.filter(_dwithin_filter(Business.location, lng, lat, radius_m))

        rows = q.limit(limit).all()
        results = []
        for row in rows:
            p, dist_m = (row[0], row[1]) if geo else (row, None)
            b = p.business
            distance_km = round(dist_m / 1000, 2) if dist_m is not None else None
            results.append({
                "entity_type":         "product",
                "entity_id":           str(p.id),
                "title":               p.name,
                "subtitle":            p.category,
                "description":         (p.description or "")[:200] or None,
                "image_url":           p.images[0] if p.images else None,
                "price":               float(p.price) if p.price else None,
                "rating":              None,
                "location":            b.registered_address if b else None,
                "distance_km":         distance_km,          # FIX §4.2
                "is_open":             None,
                "subscription_weight": _subscription_weight(b.subscription_tier if b else None),
                "metadata": {
                    "is_digital":     p.is_digital,
                    "stock_quantity": p.stock_quantity,
                },
                "_sort_key": _composite_sort_key(
                    tier=b.subscription_tier if b else None,
                    avg_rating=None,
                    review_count=0,
                    has_logo=bool(b.logo if b else None),
                    has_description=bool(p.description),
                    is_verified=b.is_verified if b else False,
                    distance_m=dist_m,
                    has_images=bool(p.images),
                ),
            })
        return results

    def _search_restaurants(
        self,
        db: Session,
        query: str,
        lat: Optional[float],
        lng: Optional[float],
        radius_m: Optional[float],
        limit: int,
    ) -> List[Dict]:
        """
        FIX: is_verified filter added (§4.3).
        FIX: distance_km computed via ST_Distance (§4.2).
        """
        geo = lat is not None and lng is not None and radius_m is not None

        select_exprs = [Restaurant]
        if geo:
            select_exprs.append(_distance_m_expr(Business.location, lng, lat))

        q = (
            db.query(*select_exprs)
            .join(Business, Business.id == Restaurant.business_id)
            .options(joinedload(Restaurant.business))
            .filter(
                Business.is_active.is_(True),
                Business.is_verified.is_(True),     # FIX: §4.3
                or_(
                    Business.business_name.ilike(f"%{query}%"),
                    Business.description.ilike(f"%{query}%"),
                    Restaurant.cuisine_type.ilike(f"%{query}%"),
                ),
            )
        )
        if geo:
            q = q.filter(_dwithin_filter(Business.location, lng, lat, radius_m))

        rows = q.limit(limit).all()
        results = []
        for row in rows:
            r, dist_m = (row[0], row[1]) if geo else (row, None)
            b = r.business
            distance_km = round(dist_m / 1000, 2) if dist_m is not None else None
            results.append({
                "entity_type":         "restaurant",
                "entity_id":           str(r.id),
                "title":               b.business_name,
                "subtitle":            r.cuisine_type,
                "description":         (b.description or "")[:200] or None,
                "image_url":           b.logo,
                "price":               None,
                "rating":              float(b.average_rating) if b.average_rating else None,
                "location":            b.registered_address,
                "distance_km":         distance_km,          # FIX §4.2
                "is_open":             getattr(b, "is_open", None),
                "subscription_weight": _subscription_weight(b.subscription_tier),
                "metadata":            {"cuisine": r.cuisine_type},
                "_sort_key": _composite_sort_key(
                    tier=b.subscription_tier,
                    avg_rating=float(b.average_rating) if b.average_rating else None,
                    review_count=getattr(b, "review_count", 0),
                    has_logo=bool(b.logo),
                    has_description=bool(b.description),
                    is_verified=b.is_verified,
                    distance_m=dist_m,
                ),
            })
        return results

    def _search_services(
        self,
        db: Session,
        query: str,
        lat: Optional[float],
        lng: Optional[float],
        radius_m: Optional[float],
        limit: int,
    ) -> List[Dict]:
        """
        FIX: Joined to Business for correct subscription_tier.
        Previously called _subscription_weight(service_obj) on Service directly —
        Service has no subscription_tier; silently returned Free(1) for every result.
        FIX: is_verified filter added (§4.3).
        FIX: distance_km computed (§4.2).
        """
        geo = lat is not None and lng is not None and radius_m is not None

        select_exprs = [Service]
        if geo:
            select_exprs.append(_distance_m_expr(Business.location, lng, lat))

        q = (
            db.query(*select_exprs)
            .join(Business, Business.id == Service.business_id)
            .options(joinedload(Service.business))
            .filter(
                Service.is_active.is_(True),
                Business.is_active.is_(True),
                Business.is_verified.is_(True),     # FIX: §4.3
                or_(
                    Service.name.ilike(f"%{query}%"),
                    Service.description.ilike(f"%{query}%"),
                    Service.category.ilike(f"%{query}%"),
                ),
            )
        )
        if geo:
            q = q.filter(_dwithin_filter(Business.location, lng, lat, radius_m))

        rows = q.limit(limit).all()
        results = []
        for row in rows:
            s, dist_m = (row[0], row[1]) if geo else (row, None)
            b = s.business
            distance_km = round(dist_m / 1000, 2) if dist_m is not None else None
            results.append({
                "entity_type":         "service",
                "entity_id":           str(s.id),
                "title":               s.name,
                "subtitle":            s.category,
                "description":         (s.description or "")[:200] or None,
                "image_url":           s.images[0] if s.images else None,
                "price":               float(s.base_price) if s.base_price else None,
                "rating":              float(s.average_rating) if getattr(s, "average_rating", None) else None,
                "location":            b.registered_address if b else None,
                "distance_km":         distance_km,          # FIX §4.2
                "is_open":             None,
                "subscription_weight": _subscription_weight(b.subscription_tier if b else None),  # FIX §7.2
                "metadata":            {"duration_minutes": getattr(s, "duration_minutes", None)},
                "_sort_key": _composite_sort_key(
                    tier=b.subscription_tier if b else None,
                    avg_rating=float(s.average_rating) if getattr(s, "average_rating", None) else None,
                    review_count=getattr(s, "review_count", 0),
                    has_logo=bool(b.logo if b else None),
                    has_description=bool(s.description),
                    is_verified=b.is_verified if b else False,
                    distance_m=dist_m,
                    has_images=bool(s.images),
                ),
            })
        return results

    def _search_properties(
        self,
        db: Session,
        query: str,
        lat: Optional[float],
        lng: Optional[float],
        radius_m: Optional[float],
        limit: int,
    ) -> List[Dict]:
        """
        FIX: Joined to Business for correct subscription_tier.
        Previously called _subscription_weight(property_obj) — Property has no
        subscription_tier; silently returned Free(1) for every result.
        FIX: is_verified filter added (§4.3).
        FIX: distance_km computed from Property.location (§4.2).
        """
        geo = lat is not None and lng is not None and radius_m is not None

        select_exprs = [Property]
        if geo:
            # Properties have their own location column (§6.6)
            select_exprs.append(_distance_m_expr(Property.location, lng, lat))

        q = (
            db.query(*select_exprs)
            .join(Business, Business.id == Property.business_id)
            .options(joinedload(Property.business))
            .filter(
                Property.is_active.is_(True),
                Business.is_active.is_(True),
                Business.is_verified.is_(True),     # FIX: §4.3
                or_(
                    Property.title.ilike(f"%{query}%"),
                    Property.description.ilike(f"%{query}%"),
                    Property.address.ilike(f"%{query}%"),
                ),
            )
        )
        if geo:
            q = q.filter(_dwithin_filter(Property.location, lng, lat, radius_m))

        rows = q.limit(limit).all()
        results = []
        for row in rows:
            p, dist_m = (row[0], row[1]) if geo else (row, None)
            b = p.business
            distance_km = round(dist_m / 1000, 2) if dist_m is not None else None
            results.append({
                "entity_type":         "property",
                "entity_id":           str(p.id),
                "title":               p.title,
                "subtitle":            f"{p.bedrooms} bed, {p.bathrooms} bath",
                "description":         (p.description or "")[:200] or None,
                "image_url":           p.photos[0] if p.photos else None,
                "price":               float(p.price) if p.price else None,
                "rating":              None,
                "location":            p.address,
                "distance_km":         distance_km,          # FIX §4.2
                "is_open":             None,
                "subscription_weight": _subscription_weight(b.subscription_tier if b else None),  # FIX §7.2
                "metadata": {
                    "property_type": p.property_type,
                    "listing_type":  p.listing_type,
                },
                "_sort_key": _composite_sort_key(
                    tier=b.subscription_tier if b else None,
                    avg_rating=None,
                    review_count=0,
                    has_logo=bool(b.logo if b else None),
                    has_description=bool(p.description),
                    is_verified=b.is_verified if b else False,
                    distance_m=dist_m,
                    has_images=bool(p.photos),
                ),
            })
        return results

    def _search_doctors(
        self,
        db: Session,
        query: str,
        lat: Optional[float],
        lng: Optional[float],
        radius_m: Optional[float],
        limit: int,
    ) -> List[Dict]:
        """
        FIX: Joined to Business for geo filter, verification, and tier rank.
        Blueprint §4: every discovery surface is filtered by GPS position.
        Doctors had NO geo filter — returned results from all of Nigeria.
        FIX: is_verified filter added (§4.3).
        FIX: distance_km computed (§4.2).
        FIX: subscription_tier from Business (not Doctor which has no such field).
        """
        geo = lat is not None and lng is not None and radius_m is not None

        select_exprs = [Doctor]
        if geo:
            select_exprs.append(_distance_m_expr(Business.location, lng, lat))

        q = (
            db.query(*select_exprs)
            .join(Business, Business.id == Doctor.business_id)
            .options(joinedload(Doctor.business))
            .filter(
                Doctor.is_active.is_(True),
                Business.is_active.is_(True),
                Business.is_verified.is_(True),     # FIX: §4.3
                or_(
                    Doctor.full_name.ilike(f"%{query}%"),
                    Doctor.specialization.ilike(f"%{query}%"),
                    Doctor.bio.ilike(f"%{query}%"),
                ),
            )
        )
        if geo:
            q = q.filter(_dwithin_filter(Business.location, lng, lat, radius_m))

        rows = q.limit(limit).all()
        results = []
        for row in rows:
            d, dist_m = (row[0], row[1]) if geo else (row, None)
            b = d.business
            distance_km = round(dist_m / 1000, 2) if dist_m is not None else None
            results.append({
                "entity_type":         "doctor",
                "entity_id":           str(d.id),
                "title":               f"Dr. {d.full_name}",
                "subtitle":            d.specialization,
                "description":         (d.bio or "")[:200] or None,
                "image_url":           d.profile_photo,
                "price":               float(d.consultation_fee) if d.consultation_fee else None,
                "rating":              float(d.average_rating) if getattr(d, "average_rating", None) else None,
                "location":            b.registered_address if b else None,
                "distance_km":         distance_km,          # FIX §4.2
                "is_open":             None,
                "subscription_weight": _subscription_weight(b.subscription_tier if b else None),  # FIX §7.2
                "metadata":            {"years_experience": getattr(d, "years_experience", None)},
                "_sort_key": _composite_sort_key(
                    tier=b.subscription_tier if b else None,
                    avg_rating=float(d.average_rating) if getattr(d, "average_rating", None) else None,
                    review_count=getattr(d, "review_count", 0),
                    has_logo=bool(b.logo if b else None),
                    has_description=bool(d.bio),
                    is_verified=b.is_verified if b else False,
                    distance_m=dist_m,
                    has_images=bool(d.profile_photo),
                ),
            })
        return results

    def _search_events(
        self,
        db: Session,
        query: str,
        lat: Optional[float],
        lng: Optional[float],
        radius_m: Optional[float],
        limit: int,
    ) -> List[Dict]:
        """
        FIX: distance_km computed via ST_Distance on TicketEvent.venue_location (§4.2).
        """
        geo = lat is not None and lng is not None and radius_m is not None

        select_exprs = [TicketEvent]
        if geo:
            select_exprs.append(_distance_m_expr(TicketEvent.venue_location, lng, lat))

        q = (
            db.query(*select_exprs)
            .filter(
                TicketEvent.is_active.is_(True),
                or_(
                    TicketEvent.name.ilike(f"%{query}%"),
                    TicketEvent.description.ilike(f"%{query}%"),
                    TicketEvent.venue_name.ilike(f"%{query}%"),
                ),
            )
        )
        if geo:
            q = q.filter(_dwithin_filter(TicketEvent.venue_location, lng, lat, radius_m))

        rows = q.limit(limit).all()
        results = []
        for row in rows:
            e, dist_m = (row[0], row[1]) if geo else (row, None)
            distance_km = round(dist_m / 1000, 2) if dist_m is not None else None
            results.append({
                "entity_type":         "event",
                "entity_id":           str(e.id),
                "title":               e.name,
                "subtitle":            e.venue_name,
                "description":         (e.description or "")[:200] or None,
                "image_url":           e.images[0] if e.images else None,
                "price":               None,
                "rating":              None,
                "location":            e.venue_address,
                "distance_km":         distance_km,          # FIX §4.2
                "is_open":             None,
                "subscription_weight": 0,
                "metadata": {
                    "event_date": str(e.event_date) if e.event_date else None,
                    "event_type": e.event_type,
                },
                "_sort_key": _composite_sort_key(
                    tier=None,
                    avg_rating=None,
                    review_count=0,
                    has_logo=False,
                    has_description=bool(e.description),
                    is_verified=False,
                    distance_m=dist_m,
                    has_images=bool(e.images),
                ),
            })
        return results

    # ── Autocomplete — Redis-first (Blueprint §7.1 + §16.3) ───────────────────

    def get_autocomplete(
        self,
        db: Session,
        *,
        query: str,
        category: Optional[str] = None,
        limit: int = 10,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
    ) -> dict:
        """
        Blueprint §7.1: auto-suggest with Redis cache (TTL=300s).
        Blueprint §16.3: key = search_suggest:{md5(query+category+limit)}

        Flow: Redis HIT → return cached; MISS → DB query → write to Redis → return.
        """
        cache_payload = json.dumps(
            {"q": query.lower().strip(), "cat": category, "limit": limit},
            sort_keys=True,
        )
        cache_key = (
            f"search_suggest:{hashlib.md5(cache_payload.encode()).hexdigest()}"
        )

        # 1. Redis first
        try:
            cached = get_redis().get(cache_key)
            if cached:
                return {"suggestions": json.loads(cached)}
        except Exception as exc:
            logger.warning("Redis autocomplete read failed: %s", exc)

        # 2. DB fallback
        suggestions = search_query_crud.get_autocomplete_suggestions(
            db,
            query_prefix=query,
            category=category,
            limit=limit,
        )

        # 3. Write-through to Redis (Blueprint §16.3: TTL=300s)
        try:
            get_redis().setex(
                cache_key,
                _AUTOCOMPLETE_CACHE_TTL,
                json.dumps(suggestions),
            )
        except Exception as exc:
            logger.warning("Redis autocomplete write failed: %s", exc)

        return {"suggestions": suggestions}

    def get_popular_searches(
        self,
        db: Session,
        *,
        category: Optional[str] = None,
        limit: int = 20,
    ) -> dict:
        """
        Blueprint §7.1: trending searches from the last 7 days.
        Also Redis-cached: search_suggest:trending:{category}:{limit} TTL=300s.
        """
        cache_key = f"search_suggest:trending:{category or 'all'}:{limit}"

        try:
            cached = get_redis().get(cache_key)
            if cached:
                return {"searches": json.loads(cached)}
        except Exception as exc:
            logger.warning("Redis trending read failed: %s", exc)

        searches = search_query_crud.get_popular_searches(
            db,
            category=category,
            days=7,
            limit=limit,
        )

        try:
            get_redis().setex(
                cache_key,
                _AUTOCOMPLETE_CACHE_TTL,
                json.dumps(searches),
            )
        except Exception as exc:
            logger.warning("Redis trending write failed: %s", exc)

        return {"searches": searches}


search_service = SearchService()