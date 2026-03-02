"""
Search service — unified search across hotels, products, restaurants, services, properties, doctors, events.
"""

from typing import Optional, List, Dict
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from uuid import UUID
from geoalchemy2.elements import WKTElement
from geoalchemy2.functions import ST_Distance, ST_GeogFromText

from app.crud.search_crud import search_query_crud
from app.models.hotels_model import Hotel
from app.models.products_model import Product
from app.models.food_model import Restaurant
from app.models.services_model import Service
from app.models.properties_model import Property
from app.models.health_model import Doctor
from app.models.tickets_model import TicketEvent
from app.schemas.search_model import SearchRequest, SearchResultItem


class SearchService:

    def search(
            self, db: Session, *,
            request: SearchRequest,
            user_id: Optional[UUID] = None,
    ) -> dict:
        """
        Unified search across all entity types.
        Returns normalized SearchResultItem objects.
        """
        query = request.query.lower().strip()
        results = []

        # Build location filter if provided
        location_filter = None
        if request.location_lat and request.location_lng:
            point = WKTElement(
                f'POINT({request.location_lng} {request.location_lat})',
                srid=4326
            )
            radius_meters = request.radius_km * 1000

        # ── HOTELS ────────────────────────────────────────────────────────
        if not request.category or request.category == "hotels":
            hotels = self._search_hotels(db, query, point if request.location_lat else None,
                                         radius_meters if request.location_lat else None)
            results.extend(hotels)

        # ── PRODUCTS ──────────────────────────────────────────────────────
        if not request.category or request.category == "products":
            products = self._search_products(db, query)
            results.extend(products)

        # ── RESTAURANTS ───────────────────────────────────────────────────
        if not request.category or request.category == "restaurants":
            restaurants = self._search_restaurants(db, query, point if request.location_lat else None,
                                                   radius_meters if request.location_lat else None)
            results.extend(restaurants)

        # ── SERVICES ──────────────────────────────────────────────────────
        if not request.category or request.category == "services":
            services = self._search_services(db, query, point if request.location_lat else None,
                                             radius_meters if request.location_lat else None)
            results.extend(services)

        # ── PROPERTIES ────────────────────────────────────────────────────
        if not request.category or request.category == "properties":
            properties = self._search_properties(db, query, point if request.location_lat else None,
                                                 radius_meters if request.location_lat else None)
            results.extend(properties)

        # ── DOCTORS ───────────────────────────────────────────────────────
        if not request.category or request.category == "doctors":
            doctors = self._search_doctors(db, query)
            results.extend(doctors)

        # ── EVENTS ────────────────────────────────────────────────────────
        if not request.category or request.category == "events":
            events = self._search_events(db, query, point if request.location_lat else None,
                                         radius_meters if request.location_lat else None)
            results.extend(events)

        # Sort by relevance (for now: rating DESC, then distance)
        results.sort(key=lambda x: (-(x.get('rating') or 0), x.get('distance_km') or 999))

        total = len(results)
        results = results[request.skip:request.skip + request.limit]

        # Track search query
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
        db.commit()

        return {
            "query": request.query,
            "results": results,
            "total": total,
            "skip": request.skip,
            "limit": request.limit,
            "category": request.category,
        }

    # ── ENTITY-SPECIFIC SEARCH METHODS ───────────────────────────────────

    def _search_hotels(self, db: Session, query: str, point, radius_meters) -> List[Dict]:
        q = db.query(Hotel).filter(
            Hotel.is_active == True,
            or_(
                Hotel.name.ilike(f'%{query}%'),
                Hotel.description.ilike(f'%{query}%'),
                Hotel.address.ilike(f'%{query}%'),
            )
        )

        if point and radius_meters:
            q = q.filter(
                ST_Distance(Hotel.location, ST_GeogFromText(point.desc)) <= radius_meters
            )

        hotels = q.limit(20).all()

        return [
            {
                "entity_type": "hotel",
                "entity_id": h.id,
                "title": h.name,
                "subtitle": h.address,
                "description": h.description[:200] if h.description else None,
                "image_url": h.photos[0] if h.photos else None,
                "price": float(h.min_price) if h.min_price else None,
                "rating": float(h.average_rating) if h.average_rating else None,
                "location": h.address,
                "distance_km": None,  # Calculate if location provided
                "metadata": {"stars": h.star_rating},
            }
            for h in hotels
        ]

    def _search_products(self, db: Session, query: str) -> List[Dict]:
        products = (
            db.query(Product)
            .filter(
                Product.is_active == True,
                or_(
                    Product.name.ilike(f'%{query}%'),
                    Product.description.ilike(f'%{query}%'),
                    Product.category.ilike(f'%{query}%'),
                )
            )
            .limit(20)
            .all()
        )

        return [
            {
                "entity_type": "product",
                "entity_id": p.id,
                "title": p.name,
                "subtitle": p.category,
                "description": p.description[:200] if p.description else None,
                "image_url": p.images[0] if p.images else None,
                "price": float(p.price),
                "rating": float(p.average_rating) if p.average_rating else None,
                "location": None,
                "distance_km": None,
                "metadata": {"in_stock": p.stock_quantity > 0},
            }
            for p in products
        ]

    def _search_restaurants(self, db: Session, query: str, point, radius_meters) -> List[Dict]:
        q = db.query(Restaurant).filter(
            Restaurant.is_active == True,
            or_(
                Restaurant.name.ilike(f'%{query}%'),
                Restaurant.description.ilike(f'%{query}%'),
                Restaurant.cuisine_type.ilike(f'%{query}%'),
            )
        )

        if point and radius_meters:
            q = q.filter(
                ST_Distance(Restaurant.location, ST_GeogFromText(point.desc)) <= radius_meters
            )

        restaurants = q.limit(20).all()

        return [
            {
                "entity_type": "restaurant",
                "entity_id": r.id,
                "title": r.name,
                "subtitle": r.cuisine_type,
                "description": r.description[:200] if r.description else None,
                "image_url": r.photos[0] if r.photos else None,
                "price": None,
                "rating": float(r.average_rating) if r.average_rating else None,
                "location": r.address,
                "distance_km": None,
                "metadata": {"cuisine": r.cuisine_type},
            }
            for r in restaurants
        ]

    def _search_services(self, db: Session, query: str, point, radius_meters) -> List[Dict]:
        q = db.query(Service).filter(
            Service.is_active == True,
            or_(
                Service.name.ilike(f'%{query}%'),
                Service.description.ilike(f'%{query}%'),
                Service.category.ilike(f'%{query}%'),
            )
        )

        if point and radius_meters:
            q = q.filter(
                ST_Distance(Service.location, ST_GeogFromText(point.desc)) <= radius_meters
            )

        services = q.limit(20).all()

        return [
            {
                "entity_type": "service",
                "entity_id": s.id,
                "title": s.name,
                "subtitle": s.category,
                "description": s.description[:200] if s.description else None,
                "image_url": s.images[0] if s.images else None,
                "price": float(s.base_price),
                "rating": float(s.average_rating) if s.average_rating else None,
                "location": None,
                "distance_km": None,
                "metadata": {"duration_minutes": s.duration_minutes},
            }
            for s in services
        ]

    def _search_properties(self, db: Session, query: str, point, radius_meters) -> List[Dict]:
        q = db.query(Property).filter(
            Property.is_active == True,
            or_(
                Property.title.ilike(f'%{query}%'),
                Property.description.ilike(f'%{query}%'),
                Property.address.ilike(f'%{query}%'),
            )
        )

        if point and radius_meters:
            q = q.filter(
                ST_Distance(Property.location, ST_GeogFromText(point.desc)) <= radius_meters
            )

        properties = q.limit(20).all()

        return [
            {
                "entity_type": "property",
                "entity_id": p.id,
                "title": p.title,
                "subtitle": f"{p.bedrooms} bed, {p.bathrooms} bath",
                "description": p.description[:200] if p.description else None,
                "image_url": p.photos[0] if p.photos else None,
                "price": float(p.price),
                "rating": None,
                "location": p.address,
                "distance_km": None,
                "metadata": {"property_type": p.property_type, "listing_type": p.listing_type},
            }
            for p in properties
        ]

    def _search_doctors(self, db: Session, query: str) -> List[Dict]:
        doctors = (
            db.query(Doctor)
            .filter(
                Doctor.is_active == True,
                or_(
                    Doctor.full_name.ilike(f'%{query}%'),
                    Doctor.specialization.ilike(f'%{query}%'),
                    Doctor.bio.ilike(f'%{query}%'),
                )
            )
            .limit(20)
            .all()
        )

        return [
            {
                "entity_type": "doctor",
                "entity_id": d.id,
                "title": f"Dr. {d.full_name}",
                "subtitle": d.specialization,
                "description": d.bio[:200] if d.bio else None,
                "image_url": d.profile_photo,
                "price": float(d.consultation_fee),
                "rating": float(d.average_rating) if d.average_rating else None,
                "location": None,
                "distance_km": None,
                "metadata": {"years_experience": d.years_experience},
            }
            for d in doctors
        ]

    def _search_events(self, db: Session, query: str, point, radius_meters) -> List[Dict]:
        q = db.query(TicketEvent).filter(
            TicketEvent.is_active == True,
            or_(
                TicketEvent.name.ilike(f'%{query}%'),
                TicketEvent.description.ilike(f'%{query}%'),
                TicketEvent.venue_name.ilike(f'%{query}%'),
            )
        )

        if point and radius_meters:
            q = q.filter(
                ST_Distance(TicketEvent.venue_location, ST_GeogFromText(point.desc)) <= radius_meters
            )

        events = q.limit(20).all()

        return [
            {
                "entity_type": "event",
                "entity_id": e.id,
                "title": e.name,
                "subtitle": e.venue_name,
                "description": e.description[:200] if e.description else None,
                "image_url": e.images[0] if e.images else None,
                "price": None,  # Events have tiered pricing
                "rating": None,
                "location": e.venue_address,
                "distance_km": None,
                "metadata": {"event_date": str(e.event_date) if e.event_date else None, "event_type": e.event_type},
            }
            for e in events
        ]

    # ── AUTOCOMPLETE ──────────────────────────────────────────────────────

    def get_autocomplete(
            self, db: Session, *, query: str, category: Optional[str] = None, limit: int = 10
    ) -> dict:
        """Returns autocomplete suggestions based on past searches."""
        suggestions = search_query_crud.get_autocomplete_suggestions(
            db, query_prefix=query, category=category, limit=limit
        )
        return {"suggestions": suggestions}

    def get_popular_searches(
            self, db: Session, *, category: Optional[str] = None, limit: int = 20
    ) -> dict:
        """Returns popular searches from the last 7 days."""
        searches = search_query_crud.get_popular_searches(
            db, category=category, days=7, limit=limit
        )
        return {"searches": searches}


search_service = SearchService()