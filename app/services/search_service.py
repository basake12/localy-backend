"""
Search service — unified search across hotels, products, restaurants, services,
properties, doctors, events.

Blueprint constraints enforced:
  - Radius-only discovery via PostGIS ST_Distance / ST_GeogFromText
  - No LGA filtering anywhere (removed — blueprint: "no LGA dependency")
  - Subscription-tier ranking: Enterprise > Pro > Starter > Free
  - Default radius: 5 km (5000 m)
  - db.flush() only — caller owns the transaction

BUG FIX — _search_hotels:
  Hotel is a satellite model. It only stores hotel-specific data
  (star_rating, total_rooms, facilities, check_in/out times). ALL searchable
  fields — is_active, business_name, address, logo, average_rating,
  subscription_tier — live on the related Business model.

  Fix: join Hotel → Business; apply all filters on Business columns;
  read all display fields from h.business. Use joinedload so the
  relationship is populated in a single SQL JOIN with no N+1.
  The geo filter uses Business.location (the PostGIS point column).
"""

from typing import Optional, List, Dict
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_
from uuid import UUID

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

# Subscription tier → numeric weight used in result ranking
_SUBSCRIPTION_WEIGHT: Dict[str, int] = {
    "enterprise": 4,
    "pro": 3,
    "starter": 2,
    "free": 1,
}


def _geo_filter(model_location_col, wkt: str, radius_m: float):
    """Return a SQLAlchemy filter expression for radius search via PostGIS."""
    from geoalchemy2.functions import ST_Distance, ST_GeogFromText
    return ST_Distance(model_location_col, ST_GeogFromText(wkt)) <= radius_m


class SearchService:

    def search(
            self, db: Session, *,
            request: SearchRequest,
            user_id: Optional[UUID] = None,
    ) -> dict:
        """
        Unified search across all entity types.
        Returns normalised SearchResultItem-shaped dicts ranked by:
          1. Subscription tier (Enterprise > Pro > Starter > Free)
          2. Rating DESC
          3. Distance ASC
        """
        query = request.query.lower().strip()
        results: List[Dict] = []

        # Resolve location context once
        has_location = bool(request.location_lat and request.location_lng)
        wkt: Optional[str] = None
        radius_meters: Optional[float] = None
        if has_location:
            wkt = f'POINT({request.location_lng} {request.location_lat})'
            radius_meters = request.radius_km * 1000

        cat = request.category  # None → search all modules

        # ── HOTELS ──────────────────────────────────────────────────────
        if not cat or cat == "hotels":
            results.extend(self._search_hotels(db, query, wkt, radius_meters))

        # ── PRODUCTS ────────────────────────────────────────────────────
        if not cat or cat == "products":
            results.extend(self._search_products(db, query))

        # ── RESTAURANTS ─────────────────────────────────────────────────
        if not cat or cat == "restaurants":
            results.extend(self._search_restaurants(db, query, wkt, radius_meters))

        # ── SERVICES ────────────────────────────────────────────────────
        if not cat or cat == "services":
            results.extend(self._search_services(db, query, wkt, radius_meters))

        # ── PROPERTIES ──────────────────────────────────────────────────
        if not cat or cat == "properties":
            results.extend(self._search_properties(db, query, wkt, radius_meters))

        # ── DOCTORS ─────────────────────────────────────────────────────
        if not cat or cat == "doctors":
            results.extend(self._search_doctors(db, query))

        # ── EVENTS ──────────────────────────────────────────────────────
        if not cat or cat == "events":
            results.extend(self._search_events(db, query, wkt, radius_meters))

        # ── SORT: subscription tier first, then rating DESC, then distance
        results.sort(key=lambda x: (
            -x.get("subscription_weight", 0),
            -(x.get("rating") or 0),
            x.get("distance_km") or 999,
        ))

        total = len(results)
        page = results[request.skip: request.skip + request.limit]

        # Track search query (flush only — caller commits)
        search_query_crud.record_search(
            db,
            user_id=user_id,
            query=query,
            category=request.category,
            results_count=total,
            location_lat=request.location_lat,
            location_lng=request.location_lng,
            filters=request.filters,
        )
        db.flush()

        return {
            "query": request.query,
            "results": page,
            "total": total,
            "skip": request.skip,
            "limit": request.limit,
            "category": request.category,
        }

    # ── HELPERS ──────────────────────────────────────────────────────────

    @staticmethod
    def _subscription_weight(business) -> int:
        tier = getattr(business, "subscription_tier", "free") or "free"
        return _SUBSCRIPTION_WEIGHT.get(tier.lower(), 0)

    # ── ENTITY-SPECIFIC SEARCH METHODS ───────────────────────────────────

    def _search_hotels(
        self, db: Session, query: str,
        wkt: Optional[str], radius_meters: Optional[float],
    ) -> List[Dict]:
        """
        Hotel is a satellite table — searchable fields live on Business.
        Join Hotel → Business; apply all filters on Business columns.
        Geo filter uses Business.location (PostGIS point).
        """
        q = (
            db.query(Hotel)
            .join(Business, Business.id == Hotel.business_id)
            .options(joinedload(Hotel.business))
            .filter(
                Business.is_active.is_(True),
                or_(
                    Business.business_name.ilike(f'%{query}%'),
                    Business.description.ilike(f'%{query}%'),
                    Business.address.ilike(f'%{query}%'),
                ),
            )
        )
        if wkt and radius_meters:
            q = q.filter(_geo_filter(Business.location, wkt, radius_meters))

        return [
            {
                "entity_type": "hotel",
                "entity_id": str(h.id),
                "title": h.business.business_name,
                "subtitle": f"{h.star_rating}★ hotel",
                "description": h.business.description[:200] if h.business.description else None,
                "image_url": h.business.logo,
                "price": float(h.base_price_per_night) if hasattr(h, "base_price_per_night") and h.base_price_per_night else None,
                "rating": float(h.business.average_rating) if h.business.average_rating else None,
                "location": h.business.address,
                "distance_km": None,
                "is_open": getattr(h.business, "is_open", None),
                "subscription_weight": self._subscription_weight(h.business),
                "metadata": {
                    "star_rating": h.star_rating,
                    "total_rooms": h.total_rooms,
                    "lat": None,
                    "lng": None,
                },
            }
            for h in q.limit(20).all()
        ]

    def _search_products(
        self, db: Session, query: str,
    ) -> List[Dict]:
        q = db.query(Product).filter(
            Product.is_active.is_(True),
            or_(
                Product.name.ilike(f'%{query}%'),
                Product.description.ilike(f'%{query}%'),
                Product.category.ilike(f'%{query}%'),
            )
        )

        return [
            {
                "entity_type": "product",
                "entity_id": str(p.id),
                "title": p.name,
                "subtitle": p.category,
                "description": p.description[:200] if p.description else None,
                "image_url": p.images[0] if p.images else None,
                "price": float(p.price),
                "rating": float(p.average_rating) if p.average_rating else None,
                "location": None,
                "distance_km": None,
                "is_open": None,
                "subscription_weight": self._subscription_weight(p),
                "metadata": {"brand": getattr(p, "brand", None)},
            }
            for p in q.limit(20).all()
        ]

    def _search_restaurants(
        self, db: Session, query: str,
        wkt: Optional[str], radius_meters: Optional[float],
    ) -> List[Dict]:
        q = (
            db.query(Restaurant)
            .join(Business, Business.id == Restaurant.business_id)
            .options(joinedload(Restaurant.business))
            .filter(
                Business.is_active.is_(True),
                or_(
                    Business.business_name.ilike(f'%{query}%'),
                    Business.description.ilike(f'%{query}%'),
                    Restaurant.cuisine_type.ilike(f'%{query}%'),
                ),
            )
        )
        if wkt and radius_meters:
            q = q.filter(_geo_filter(Business.location, wkt, radius_meters))

        return [
            {
                "entity_type": "restaurant",
                "entity_id": str(r.id),
                "title": r.business.business_name,
                "subtitle": r.cuisine_type,
                "description": r.business.description[:200] if r.business.description else None,
                "image_url": r.business.logo,
                "price": None,
                "rating": float(r.business.average_rating) if r.business.average_rating else None,
                "location": r.business.address,
                "distance_km": None,
                "is_open": getattr(r.business, "is_open", None),
                "subscription_weight": self._subscription_weight(r.business),
                "metadata": {"cuisine": r.cuisine_type},
            }
            for r in q.limit(20).all()
        ]

    def _search_services(
        self, db: Session, query: str,
        wkt: Optional[str], radius_meters: Optional[float],
    ) -> List[Dict]:
        q = db.query(Service).filter(
            Service.is_active.is_(True),
            or_(
                Service.name.ilike(f'%{query}%'),
                Service.description.ilike(f'%{query}%'),
                Service.category.ilike(f'%{query}%'),
            )
        )
        if wkt and radius_meters:
            q = q.filter(_geo_filter(Service.location, wkt, radius_meters))

        return [
            {
                "entity_type": "service",
                "entity_id": str(s.id),
                "title": s.name,
                "subtitle": s.category,
                "description": s.description[:200] if s.description else None,
                "image_url": s.images[0] if s.images else None,
                "price": float(s.base_price),
                "rating": float(s.average_rating) if s.average_rating else None,
                "location": None,
                "distance_km": None,
                "is_open": None,
                "subscription_weight": self._subscription_weight(s),
                "metadata": {"duration_minutes": s.duration_minutes},
            }
            for s in q.limit(20).all()
        ]

    def _search_properties(
        self, db: Session, query: str,
        wkt: Optional[str], radius_meters: Optional[float],
    ) -> List[Dict]:
        q = db.query(Property).filter(
            Property.is_active.is_(True),
            or_(
                Property.title.ilike(f'%{query}%'),
                Property.description.ilike(f'%{query}%'),
                Property.address.ilike(f'%{query}%'),
            )
        )
        if wkt and radius_meters:
            q = q.filter(_geo_filter(Property.location, wkt, radius_meters))

        return [
            {
                "entity_type": "property",
                "entity_id": str(p.id),
                "title": p.title,
                "subtitle": f"{p.bedrooms} bed, {p.bathrooms} bath",
                "description": p.description[:200] if p.description else None,
                "image_url": p.photos[0] if p.photos else None,
                "price": float(p.price),
                "rating": None,
                "location": p.address,
                "distance_km": None,
                "is_open": None,
                "subscription_weight": self._subscription_weight(p),
                "metadata": {
                    "property_type": p.property_type,
                    "listing_type": p.listing_type,
                },
            }
            for p in q.limit(20).all()
        ]

    def _search_doctors(
        self, db: Session, query: str,
    ) -> List[Dict]:
        q = db.query(Doctor).filter(
            Doctor.is_active.is_(True),
            or_(
                Doctor.full_name.ilike(f'%{query}%'),
                Doctor.specialization.ilike(f'%{query}%'),
                Doctor.bio.ilike(f'%{query}%'),
            )
        )

        return [
            {
                "entity_type": "doctor",
                "entity_id": str(d.id),
                "title": f"Dr. {d.full_name}",
                "subtitle": d.specialization,
                "description": d.bio[:200] if d.bio else None,
                "image_url": d.profile_photo,
                "price": float(d.consultation_fee),
                "rating": float(d.average_rating) if d.average_rating else None,
                "location": None,
                "distance_km": None,
                "is_open": None,
                "subscription_weight": self._subscription_weight(d),
                "metadata": {"years_experience": d.years_experience},
            }
            for d in q.limit(20).all()
        ]

    def _search_events(
        self, db: Session, query: str,
        wkt: Optional[str], radius_meters: Optional[float],
    ) -> List[Dict]:
        q = db.query(TicketEvent).filter(
            TicketEvent.is_active.is_(True),
            or_(
                TicketEvent.name.ilike(f'%{query}%'),
                TicketEvent.description.ilike(f'%{query}%'),
                TicketEvent.venue_name.ilike(f'%{query}%'),
            )
        )
        if wkt and radius_meters:
            q = q.filter(_geo_filter(TicketEvent.venue_location, wkt, radius_meters))

        return [
            {
                "entity_type": "event",
                "entity_id": str(e.id),
                "title": e.name,
                "subtitle": e.venue_name,
                "description": e.description[:200] if e.description else None,
                "image_url": e.images[0] if e.images else None,
                "price": None,
                "rating": None,
                "location": e.venue_address,
                "distance_km": None,
                "is_open": None,
                "subscription_weight": 0,
                "metadata": {
                    "event_date": str(e.event_date) if e.event_date else None,
                    "event_type": e.event_type,
                },
            }
            for e in q.limit(20).all()
        ]

    # ── AUTOCOMPLETE ─────────────────────────────────────────────────────

    def get_autocomplete(
            self, db: Session, *, query: str,
            category: Optional[str] = None, limit: int = 10,
    ) -> dict:
        """Returns autocomplete suggestions based on past searches."""
        suggestions = search_query_crud.get_autocomplete_suggestions(
            db, query_prefix=query, category=category, limit=limit,
        )
        return {"suggestions": suggestions}

    def get_popular_searches(
            self, db: Session, *,
            category: Optional[str] = None, limit: int = 20,
    ) -> dict:
        """Returns popular searches from the last 7 days."""
        searches = search_query_crud.get_popular_searches(
            db, category=category, days=7, limit=limit,
        )
        return {"searches": searches}


search_service = SearchService()