from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from uuid import UUID
from datetime import datetime, date, time
from decimal import Decimal
import random
import string

from app.crud.base_crud import CRUDBase
from app.models.properties_model import (
    PropertyAgent, Property, PropertyViewing,
    PropertyOffer, SavedProperty, PropertyInquiry,
    PropertyStatusEnum, ViewingStatusEnum, OfferStatusEnum,
)
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
)


class CRUDPropertyAgent(CRUDBase[PropertyAgent, dict, dict]):

    def get_by_business_id(
            self, db: Session, *, business_id: UUID
    ) -> Optional[PropertyAgent]:
        return (
            db.query(PropertyAgent)
            .filter(PropertyAgent.business_id == business_id)
            .first()
        )


class CRUDProperty(CRUDBase[Property, dict, dict]):

    def _generate_slug(self, title: str) -> str:
        slug = title.lower().strip()
        slug = "".join(c if c.isalnum() or c == " " else "" for c in slug)
        slug = slug.replace(" ", "-")
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"{slug}-{suffix}"

    def create_property(
            self, db: Session, *, agent_id: UUID, property_data: Dict[str, Any]
    ) -> Property:
        from geoalchemy2.elements import WKTElement

        property_data["slug"] = self._generate_slug(property_data["title"])
        property_data["agent_id"] = agent_id

        if "location" in property_data and isinstance(property_data["location"], dict):
            lat = property_data["location"]["latitude"]
            lng = property_data["location"]["longitude"]
            property_data["location"] = WKTElement(f"POINT({lng} {lat})", srid=4326)

        if property_data.get("building_size_sqm") and property_data.get("price"):
            property_data["price_per_sqm"] = (
                Decimal(str(property_data["price"]))
                / Decimal(str(property_data["building_size_sqm"]))
            )

        property_obj = Property(**property_data)
        db.add(property_obj)
        db.flush()

        # FIX: legacy Session.query().get() pattern replaced with db.get()
        # which is the SQLAlchemy 2.x idiomatic approach.
        agent = db.get(PropertyAgent, agent_id)
        if agent:
            agent.total_properties += 1

        db.commit()
        db.refresh(property_obj)
        return property_obj

    def search_properties(
            self,
            db: Session,
            *,
            query_text: Optional[str] = None,
            property_type: Optional[str] = None,
            property_subtype: Optional[str] = None,
            listing_type: Optional[str] = None,
            city: Optional[str] = None,
            state: Optional[str] = None,
            local_government: Optional[str] = None,
            location: Optional[tuple] = None,
            radius_km: float = 20.0,
            min_price: Optional[Decimal] = None,
            max_price: Optional[Decimal] = None,
            min_bedrooms: Optional[int] = None,
            max_bedrooms: Optional[int] = None,
            min_bathrooms: Optional[int] = None,
            min_plot_size: Optional[Decimal] = None,
            max_plot_size: Optional[Decimal] = None,
            furnishing_status: Optional[str] = None,
            features: Optional[List[str]] = None,
            is_featured: Optional[bool] = None,
            is_verified: Optional[bool] = None,
            sort_by: str = "created_at",
            skip: int = 0,
            limit: int = 20,
    ) -> List[Property]:
        query = db.query(Property).filter(
            and_(
                Property.is_active.is_(True),
                Property.status != PropertyStatusEnum.OFF_MARKET,
            )
        )

        if query_text:
            query = query.filter(
                or_(
                    Property.title.ilike(f"%{query_text}%"),
                    Property.description.ilike(f"%{query_text}%"),
                    Property.address.ilike(f"%{query_text}%"),
                    Property.city.ilike(f"%{query_text}%"),
                )
            )

        if property_type:
            query = query.filter(Property.property_type == property_type)
        if property_subtype:
            query = query.filter(Property.property_subtype == property_subtype)
        if listing_type:
            query = query.filter(Property.listing_type == listing_type)
        if city:
            query = query.filter(Property.city.ilike(f"%{city}%"))
        if state:
            query = query.filter(Property.state.ilike(f"%{state}%"))
        if local_government:
            query = query.filter(Property.local_government.ilike(f"%{local_government}%"))

        if location:
            lat, lng = location
            point = func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)
            # FIX: Include properties with no location — ST_DWithin(NULL) returns NULL → empty results
            query = query.filter(or_(
                Property.location.is_(None),
                func.ST_DWithin(Property.location, point, radius_km * 1000)
            ))

        if min_price is not None:
            query = query.filter(Property.price >= min_price)
        if max_price is not None:
            query = query.filter(Property.price <= max_price)
        if min_bedrooms is not None:
            query = query.filter(Property.bedrooms >= min_bedrooms)
        if max_bedrooms is not None:
            query = query.filter(Property.bedrooms <= max_bedrooms)
        if min_bathrooms is not None:
            query = query.filter(Property.bathrooms >= min_bathrooms)
        if min_plot_size is not None:
            query = query.filter(Property.plot_size_sqm >= min_plot_size)
        if max_plot_size is not None:
            query = query.filter(Property.plot_size_sqm <= max_plot_size)
        if furnishing_status:
            query = query.filter(Property.furnishing_status == furnishing_status)
        if features:
            for feature in features:
                query = query.filter(Property.features.contains([feature]))
        if is_featured is not None:
            query = query.filter(Property.is_featured.is_(is_featured))
        if is_verified is not None:
            query = query.filter(Property.is_verified.is_(is_verified))

        if sort_by == "price_asc":
            query = query.order_by(Property.price.asc())
        elif sort_by == "price_desc":
            query = query.order_by(Property.price.desc())
        elif sort_by == "newest":
            query = query.order_by(Property.created_at.desc())
        elif sort_by == "popular":
            query = query.order_by(Property.views_count.desc())
        else:
            query = query.order_by(
                Property.is_featured.desc(), Property.created_at.desc()
            )

        return query.offset(skip).limit(limit).all()

    def get_by_slug(self, db: Session, *, slug: str) -> Optional[Property]:
        return db.query(Property).filter(Property.slug == slug).first()

    def get_by_agent(
            self, db: Session, *, agent_id: UUID, skip: int = 0, limit: int = 50
    ) -> List[Property]:
        return (
            db.query(Property)
            .filter(Property.agent_id == agent_id)
            .order_by(Property.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def increment_views(self, db: Session, *, property_id: UUID) -> None:
        db.query(Property).filter(Property.id == property_id).update(
            {"views_count": Property.views_count + 1}
        )
        db.commit()

    def get_nearby_properties(
            self, db: Session, *, property_id: UUID, radius_km: float = 5.0, limit: int = 6
    ) -> List[Property]:
        target = self.get(db, id=property_id)
        if not target or not target.location:
            return []

        return (
            db.query(Property)
            .filter(
                and_(
                    Property.id != property_id,
                    Property.is_active.is_(True),
                    Property.status == PropertyStatusEnum.AVAILABLE,
                    func.ST_DWithin(
                        Property.location, target.location, radius_km * 1000
                    ),
                )
            )
            .order_by(Property.is_featured.desc(), Property.created_at.desc())
            .limit(limit)
            .all()
        )


class CRUDPropertyViewing(CRUDBase[PropertyViewing, dict, dict]):

    def _generate_confirmation_code(self, db: Session) -> str:
        while True:
            code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
            if not db.query(PropertyViewing).filter(
                PropertyViewing.confirmation_code == code
            ).first():
                return code

    def create_viewing(
            self,
            db: Session,
            *,
            property_id: UUID,
            customer_id: UUID,
            viewing_date: date,
            viewing_time: time,
            viewing_type: str,
            customer_name: str,
            customer_phone: str,
            customer_email: Optional[str] = None,
            number_of_people: int = 1,
            special_requests: Optional[str] = None,
    ) -> PropertyViewing:
        property_obj = property_crud.get(db, id=property_id)
        if not property_obj:
            raise NotFoundException("Property")
        if property_obj.status == PropertyStatusEnum.OFF_MARKET:
            raise ValidationException("Property is off-market")

        conflict = (
            db.query(PropertyViewing)
            .filter(
                and_(
                    PropertyViewing.property_id == property_id,
                    PropertyViewing.viewing_date == viewing_date,
                    PropertyViewing.viewing_time == viewing_time,
                    PropertyViewing.status.in_(["pending", "confirmed"]),
                )
            )
            .first()
        )
        if conflict:
            raise ValidationException("A viewing is already scheduled at this time")

        viewing = PropertyViewing(
            property_id=property_id,
            customer_id=customer_id,
            viewing_date=viewing_date,
            viewing_time=viewing_time,
            viewing_type=viewing_type,
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_email=customer_email,
            number_of_people=number_of_people,
            special_requests=special_requests,
            confirmation_code=self._generate_confirmation_code(db),
        )
        db.add(viewing)
        db.flush()
        property_obj.inquiries_count += 1
        db.commit()
        db.refresh(viewing)
        return viewing

    def get_customer_viewings(
            self, db: Session, *, customer_id: UUID, skip: int = 0, limit: int = 20
    ) -> List[PropertyViewing]:
        return (
            db.query(PropertyViewing)
            .options(joinedload(PropertyViewing.property))
            .filter(PropertyViewing.customer_id == customer_id)
            .order_by(PropertyViewing.viewing_date.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def get_property_viewings(
            self,
            db: Session,
            *,
            property_id: UUID,
            viewing_date: Optional[date] = None,
            status: Optional[str] = None,
            skip: int = 0,
            limit: int = 50,
    ) -> List[PropertyViewing]:
        q = db.query(PropertyViewing).filter(PropertyViewing.property_id == property_id)
        if viewing_date:
            q = q.filter(PropertyViewing.viewing_date == viewing_date)
        if status:
            q = q.filter(PropertyViewing.status == status)
        return (
            q.order_by(PropertyViewing.viewing_date, PropertyViewing.viewing_time)
            .offset(skip)
            .limit(limit)
            .all()
        )

    def get_agent_viewings(
            self,
            db: Session,
            *,
            agent_id: UUID,
            viewing_date: Optional[date] = None,
            status: Optional[str] = None,
            skip: int = 0,
            limit: int = 50,
    ) -> List[PropertyViewing]:
        q = db.query(PropertyViewing).join(Property).filter(Property.agent_id == agent_id)
        if viewing_date:
            q = q.filter(PropertyViewing.viewing_date == viewing_date)
        if status:
            q = q.filter(PropertyViewing.status == status)
        return (
            q.order_by(PropertyViewing.viewing_date, PropertyViewing.viewing_time)
            .offset(skip)
            .limit(limit)
            .all()
        )


class CRUDPropertyOffer(CRUDBase[PropertyOffer, dict, dict]):

    def create_offer(
            self,
            db: Session,
            *,
            property_id: UUID,
            customer_id: UUID,
            offer_amount: Decimal,
            proposed_payment_plan: Optional[str] = None,
            proposed_lease_duration: Optional[int] = None,
            message: Optional[str] = None,
    ) -> PropertyOffer:
        property_obj = property_crud.get(db, id=property_id)
        if not property_obj:
            raise NotFoundException("Property")
        if property_obj.status in [PropertyStatusEnum.SOLD, PropertyStatusEnum.OFF_MARKET]:
            raise ValidationException("Property is not available for offers")

        existing = (
            db.query(PropertyOffer)
            .filter(
                and_(
                    PropertyOffer.property_id == property_id,
                    PropertyOffer.customer_id == customer_id,
                    PropertyOffer.status == OfferStatusEnum.PENDING,
                )
            )
            .first()
        )
        if existing:
            raise ValidationException("You already have a pending offer on this property")

        offer = PropertyOffer(
            property_id=property_id,
            customer_id=customer_id,
            offer_amount=offer_amount,
            original_price=property_obj.price,
            proposed_payment_plan=proposed_payment_plan,
            proposed_lease_duration=proposed_lease_duration,
            message=message,
        )
        db.add(offer)
        db.flush()

        if property_obj.status == PropertyStatusEnum.AVAILABLE:
            property_obj.status = PropertyStatusEnum.UNDER_OFFER

        db.commit()
        db.refresh(offer)
        return offer

    def get_property_offers(
            self,
            db: Session,
            *,
            property_id: UUID,
            status: Optional[str] = None,
            skip: int = 0,
            limit: int = 50,
    ) -> List[PropertyOffer]:
        q = db.query(PropertyOffer).filter(PropertyOffer.property_id == property_id)
        if status:
            q = q.filter(PropertyOffer.status == status)
        return q.order_by(PropertyOffer.offer_amount.desc()).offset(skip).limit(limit).all()

    def get_customer_offers(
            self, db: Session, *, customer_id: UUID, skip: int = 0, limit: int = 20
    ) -> List[PropertyOffer]:
        return (
            db.query(PropertyOffer)
            .options(joinedload(PropertyOffer.property))
            .filter(PropertyOffer.customer_id == customer_id)
            .order_by(PropertyOffer.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )


class CRUDSavedProperty(CRUDBase[SavedProperty, dict, dict]):

    def toggle_save(
            self,
            db: Session,
            *,
            property_id: UUID,
            customer_id: UUID,
            notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        existing = (
            db.query(SavedProperty)
            .filter(
                and_(
                    SavedProperty.property_id == property_id,
                    SavedProperty.customer_id == customer_id,
                )
            )
            .first()
        )
        if existing:
            db.delete(existing)
            db.flush()
            prop = db.get(Property, property_id)
            if prop:
                prop.saves_count = max(0, prop.saves_count - 1)
            db.commit()
            return {"saved": False}
        else:
            db.add(SavedProperty(property_id=property_id, customer_id=customer_id, notes=notes))
            db.flush()
            prop = db.get(Property, property_id)
            if prop:
                prop.saves_count += 1
            db.commit()
            return {"saved": True}

    def get_saved_properties(
            self, db: Session, *, customer_id: UUID, skip: int = 0, limit: int = 50
    ) -> List[SavedProperty]:
        return (
            db.query(SavedProperty)
            .options(joinedload(SavedProperty.property))
            .filter(SavedProperty.customer_id == customer_id)
            .order_by(SavedProperty.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )


class CRUDPropertyInquiry(CRUDBase[PropertyInquiry, dict, dict]):

    def create_inquiry(
            self,
            db: Session,
            *,
            property_id: UUID,
            customer_id: UUID,
            subject: str,
            message: str,
            customer_name: str,
            customer_phone: str,
            customer_email: str,
    ) -> PropertyInquiry:
        property_obj = property_crud.get(db, id=property_id)
        if not property_obj:
            raise NotFoundException("Property")

        inquiry = PropertyInquiry(
            property_id=property_id,
            customer_id=customer_id,
            subject=subject,
            message=message,
            customer_name=customer_name,
            customer_phone=customer_phone,
            customer_email=customer_email,
        )
        db.add(inquiry)
        db.flush()
        property_obj.inquiries_count += 1
        db.commit()
        db.refresh(inquiry)
        return inquiry

    def get_property_inquiries(
            self,
            db: Session,
            *,
            property_id: UUID,
            is_responded: Optional[bool] = None,
            skip: int = 0,
            limit: int = 50,
    ) -> List[PropertyInquiry]:
        q = db.query(PropertyInquiry).filter(PropertyInquiry.property_id == property_id)
        if is_responded is not None:
            q = q.filter(PropertyInquiry.is_responded.is_(is_responded))
        return q.order_by(PropertyInquiry.created_at.desc()).offset(skip).limit(limit).all()


# ─────────────────────────────────────────────
# Singletons
# ─────────────────────────────────────────────
property_agent_crud = CRUDPropertyAgent(PropertyAgent)
property_crud = CRUDProperty(Property)
property_viewing_crud = CRUDPropertyViewing(PropertyViewing)
property_offer_crud = CRUDPropertyOffer(PropertyOffer)
saved_property_crud = CRUDSavedProperty(SavedProperty)
property_inquiry_crud = CRUDPropertyInquiry(PropertyInquiry)