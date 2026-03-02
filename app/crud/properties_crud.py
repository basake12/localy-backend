from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func, case
from uuid import UUID
from datetime import datetime, date, time
from decimal import Decimal
import random
import string

from app.crud.base_crud import CRUDBase
from app.models.properties_model import (
    PropertyAgent, Property, PropertyViewing,
    PropertyOffer, SavedProperty, PropertyInquiry,
    PropertyStatusEnum, ViewingStatusEnum, OfferStatusEnum
)
from app.models.business_model import Business
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    BookingNotAvailableException
)


class CRUDPropertyAgent(CRUDBase[PropertyAgent, dict, dict]):
    """CRUD for PropertyAgent"""

    def get_by_business_id(
            self,
            db: Session,
            *,
            business_id: UUID
    ) -> Optional[PropertyAgent]:
        """Get agent by business ID"""
        return db.query(PropertyAgent).filter(
            PropertyAgent.business_id == business_id
        ).first()


class CRUDProperty(CRUDBase[Property, dict, dict]):
    """CRUD for Property"""

    def _generate_slug(self, title: str) -> str:
        """Generate URL-friendly slug from title"""
        slug = title.lower().strip()
        slug = ''.join(c if c.isalnum() or c == ' ' else '' for c in slug)
        slug = slug.replace(' ', '-')
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"{slug}-{suffix}"

    def create_property(
            self,
            db: Session,
            *,
            agent_id: UUID,
            property_data: Dict[str, Any]
    ) -> Property:
        """Create a new property listing"""
        from geoalchemy2.elements import WKTElement

        # Generate slug
        property_data["slug"] = self._generate_slug(property_data["title"])
        property_data["agent_id"] = agent_id

        # Handle location
        if "location" in property_data and isinstance(property_data["location"], dict):
            lat = property_data["location"]["latitude"]
            lng = property_data["location"]["longitude"]
            property_data["location"] = WKTElement(f"POINT({lng} {lat})", srid=4326)

        # Calculate price_per_sqm if building size provided
        if property_data.get("building_size_sqm") and property_data.get("price"):
            property_data["price_per_sqm"] = (
                    Decimal(str(property_data["price"])) /
                    Decimal(str(property_data["building_size_sqm"]))
            )

        property_obj = Property(**property_data)
        db.add(property_obj)
        db.flush()

        # Update agent stats
        agent = db.query(PropertyAgent).get(agent_id)
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
            limit: int = 20
    ) -> List[Property]:
        """Search properties with comprehensive filters"""
        query = db.query(Property).filter(
            and_(
                Property.is_active == True,
                Property.status != PropertyStatusEnum.OFF_MARKET
            )
        )

        # Text search across multiple fields
        if query_text:
            search_filter = or_(
                Property.title.ilike(f"%{query_text}%"),
                Property.description.ilike(f"%{query_text}%"),
                Property.address.ilike(f"%{query_text}%"),
                Property.city.ilike(f"%{query_text}%")
            )
            query = query.filter(search_filter)

        # Type filters
        if property_type:
            query = query.filter(Property.property_type == property_type)

        if property_subtype:
            query = query.filter(Property.property_subtype == property_subtype)

        if listing_type:
            query = query.filter(Property.listing_type == listing_type)

        # Location filters
        if city:
            query = query.filter(Property.city.ilike(f"%{city}%"))

        if state:
            query = query.filter(Property.state.ilike(f"%{state}%"))

        # PostGIS radius-based location filter
        if location:
            lat, lng = location
            query = query.filter(
                func.ST_DWithin(
                    Property.location,
                    func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326),
                    radius_km * 1000
                )
            )

        # Price range
        if min_price is not None:
            query = query.filter(Property.price >= min_price)

        if max_price is not None:
            query = query.filter(Property.price <= max_price)

        # Bedroom filters
        if min_bedrooms is not None:
            query = query.filter(Property.bedrooms >= min_bedrooms)

        if max_bedrooms is not None:
            query = query.filter(Property.bedrooms <= max_bedrooms)

        # Bathroom filter
        if min_bathrooms is not None:
            query = query.filter(Property.bathrooms >= min_bathrooms)

        # Plot size filters
        if min_plot_size is not None:
            query = query.filter(Property.plot_size_sqm >= min_plot_size)

        if max_plot_size is not None:
            query = query.filter(Property.plot_size_sqm <= max_plot_size)

        # Furnishing status
        if furnishing_status:
            query = query.filter(Property.furnishing_status == furnishing_status)

        # Features filter (JSONB contains all requested features)
        if features:
            for feature in features:
                query = query.filter(Property.features.contains([feature]))

        # Featured / Verified filters
        if is_featured is not None:
            query = query.filter(Property.is_featured == is_featured)

        if is_verified is not None:
            query = query.filter(Property.is_verified == is_verified)

        # Sorting
        if sort_by == "price_asc":
            query = query.order_by(Property.price.asc())
        elif sort_by == "price_desc":
            query = query.order_by(Property.price.desc())
        elif sort_by == "newest":
            query = query.order_by(Property.created_at.desc())
        elif sort_by == "popular":
            query = query.order_by(Property.views_count.desc())
        else:
            # Default: featured first, then newest
            query = query.order_by(
                Property.is_featured.desc(),
                Property.created_at.desc()
            )

        return query.offset(skip).limit(limit).all()

    def get_by_slug(
            self,
            db: Session,
            *,
            slug: str
    ) -> Optional[Property]:
        """Get property by slug"""
        return db.query(Property).filter(
            Property.slug == slug
        ).first()

    def get_by_agent(
            self,
            db: Session,
            *,
            agent_id: UUID,
            skip: int = 0,
            limit: int = 50
    ) -> List[Property]:
        """Get properties by agent"""
        return db.query(Property).filter(
            Property.agent_id == agent_id
        ).order_by(
            Property.created_at.desc()
        ).offset(skip).limit(limit).all()

    def increment_views(self, db: Session, *, property_id: UUID) -> None:
        """Increment property view count"""
        db.query(Property).filter(
            Property.id == property_id
        ).update({"views_count": Property.views_count + 1})
        db.commit()

    def get_nearby_properties(
            self,
            db: Session,
            *,
            property_id: UUID,
            radius_km: float = 5.0,
            limit: int = 6
    ) -> List[Property]:
        """Get nearby properties for recommendations"""
        target = self.get(db, id=property_id)
        if not target or not target.location:
            return []

        return db.query(Property).filter(
            and_(
                Property.id != property_id,
                Property.is_active == True,
                Property.status == PropertyStatusEnum.AVAILABLE,
                func.ST_DWithin(
                    Property.location,
                    target.location,
                    radius_km * 1000
                )
            )
        ).order_by(
            Property.is_featured.desc(),
            Property.created_at.desc()
        ).limit(limit).all()


class CRUDPropertyViewing(CRUDBase[PropertyViewing, dict, dict]):
    """CRUD for PropertyViewing"""

    def _generate_confirmation_code(self, db: Session) -> str:
        """Generate unique confirmation code"""
        while True:
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            existing = db.query(PropertyViewing).filter(
                PropertyViewing.confirmation_code == code
            ).first()
            if not existing:
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
            special_requests: Optional[str] = None
    ) -> PropertyViewing:
        """Create a property viewing appointment"""
        # Check property exists and is available
        property_obj = property_crud.get(db, id=property_id)
        if not property_obj:
            raise NotFoundException("Property")

        if property_obj.status == PropertyStatusEnum.OFF_MARKET:
            raise ValidationException("Property is off-market")

        # Check for conflicting viewings on same date/time
        conflict = db.query(PropertyViewing).filter(
            and_(
                PropertyViewing.property_id == property_id,
                PropertyViewing.viewing_date == viewing_date,
                PropertyViewing.viewing_time == viewing_time,
                PropertyViewing.status.in_(["pending", "confirmed"])
            )
        ).first()

        if conflict:
            raise ValidationException("A viewing is already scheduled at this time")

        confirmation_code = self._generate_confirmation_code(db)

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
            confirmation_code=confirmation_code
        )

        db.add(viewing)
        db.flush()

        # Update property inquiries count
        property_obj.inquiries_count += 1

        db.commit()
        db.refresh(viewing)

        return viewing

    def get_customer_viewings(
            self,
            db: Session,
            *,
            customer_id: UUID,
            skip: int = 0,
            limit: int = 20
    ) -> List[PropertyViewing]:
        """Get customer's viewing appointments"""
        return db.query(PropertyViewing).options(
            joinedload(PropertyViewing.property)
        ).filter(
            PropertyViewing.customer_id == customer_id
        ).order_by(
            PropertyViewing.viewing_date.desc()
        ).offset(skip).limit(limit).all()

    def get_property_viewings(
            self,
            db: Session,
            *,
            property_id: UUID,
            viewing_date: Optional[date] = None,
            status: Optional[str] = None,
            skip: int = 0,
            limit: int = 50
    ) -> List[PropertyViewing]:
        """Get viewings scheduled for a property"""
        query = db.query(PropertyViewing).filter(
            PropertyViewing.property_id == property_id
        )

        if viewing_date:
            query = query.filter(PropertyViewing.viewing_date == viewing_date)

        if status:
            query = query.filter(PropertyViewing.status == status)

        return query.order_by(
            PropertyViewing.viewing_date,
            PropertyViewing.viewing_time
        ).offset(skip).limit(limit).all()

    def get_agent_viewings(
            self,
            db: Session,
            *,
            agent_id: UUID,
            viewing_date: Optional[date] = None,
            status: Optional[str] = None,
            skip: int = 0,
            limit: int = 50
    ) -> List[PropertyViewing]:
        """Get all viewings across agent's properties"""
        query = db.query(PropertyViewing).join(Property).filter(
            Property.agent_id == agent_id
        )

        if viewing_date:
            query = query.filter(PropertyViewing.viewing_date == viewing_date)

        if status:
            query = query.filter(PropertyViewing.status == status)

        return query.order_by(
            PropertyViewing.viewing_date,
            PropertyViewing.viewing_time
        ).offset(skip).limit(limit).all()


class CRUDPropertyOffer(CRUDBase[PropertyOffer, dict, dict]):
    """CRUD for PropertyOffer"""

    def create_offer(
            self,
            db: Session,
            *,
            property_id: UUID,
            customer_id: UUID,
            offer_amount: Decimal,
            proposed_payment_plan: Optional[str] = None,
            proposed_lease_duration: Optional[int] = None,
            message: Optional[str] = None
    ) -> PropertyOffer:
        """Create a property offer"""
        property_obj = property_crud.get(db, id=property_id)
        if not property_obj:
            raise NotFoundException("Property")

        if property_obj.status in [PropertyStatusEnum.SOLD, PropertyStatusEnum.OFF_MARKET]:
            raise ValidationException("Property is not available for offers")

        # Check for existing pending offer from this customer
        existing_offer = db.query(PropertyOffer).filter(
            and_(
                PropertyOffer.property_id == property_id,
                PropertyOffer.customer_id == customer_id,
                PropertyOffer.status == OfferStatusEnum.PENDING
            )
        ).first()

        if existing_offer:
            raise ValidationException("You already have a pending offer on this property")

        offer = PropertyOffer(
            property_id=property_id,
            customer_id=customer_id,
            offer_amount=offer_amount,
            original_price=property_obj.price,
            proposed_payment_plan=proposed_payment_plan,
            proposed_lease_duration=proposed_lease_duration,
            message=message
        )

        db.add(offer)
        db.flush()

        # Update property status to under_offer if currently available
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
            limit: int = 50
    ) -> List[PropertyOffer]:
        """Get offers for a property"""
        query = db.query(PropertyOffer).filter(
            PropertyOffer.property_id == property_id
        )

        if status:
            query = query.filter(PropertyOffer.status == status)

        return query.order_by(
            PropertyOffer.offer_amount.desc()
        ).offset(skip).limit(limit).all()

    def get_customer_offers(
            self,
            db: Session,
            *,
            customer_id: UUID,
            skip: int = 0,
            limit: int = 20
    ) -> List[PropertyOffer]:
        """Get customer's offers"""
        return db.query(PropertyOffer).options(
            joinedload(PropertyOffer.property)
        ).filter(
            PropertyOffer.customer_id == customer_id
        ).order_by(
            PropertyOffer.created_at.desc()
        ).offset(skip).limit(limit).all()


class CRUDSavedProperty(CRUDBase[SavedProperty, dict, dict]):
    """CRUD for SavedProperty"""

    def toggle_save(
            self,
            db: Session,
            *,
            property_id: UUID,
            customer_id: UUID,
            notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """Toggle saved state"""
        existing = db.query(SavedProperty).filter(
            and_(
                SavedProperty.property_id == property_id,
                SavedProperty.customer_id == customer_id
            )
        ).first()

        if existing:
            # Remove save
            db.delete(existing)
            db.flush()

            # Decrement count
            property_obj = property_crud.get(db, id=property_id)
            property_obj.saves_count = max(0, property_obj.saves_count - 1)

            db.commit()
            return {"saved": False}
        else:
            # Add save
            saved = SavedProperty(
                property_id=property_id,
                customer_id=customer_id,
                notes=notes
            )
            db.add(saved)
            db.flush()

            # Increment count
            property_obj = property_crud.get(db, id=property_id)
            property_obj.saves_count += 1

            db.commit()
            return {"saved": True}

    def get_saved_properties(
            self,
            db: Session,
            *,
            customer_id: UUID,
            skip: int = 0,
            limit: int = 50
    ) -> List[SavedProperty]:
        """Get user's saved properties"""
        return db.query(SavedProperty).options(
            joinedload(SavedProperty.property)
        ).filter(
            SavedProperty.customer_id == customer_id
        ).order_by(
            SavedProperty.created_at.desc()
        ).offset(skip).limit(limit).all()


class CRUDPropertyInquiry(CRUDBase[PropertyInquiry, dict, dict]):
    """CRUD for PropertyInquiry"""

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
            customer_email: str
    ) -> PropertyInquiry:
        """Create a property inquiry"""
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
            customer_email=customer_email
        )

        db.add(inquiry)
        db.flush()

        # Update property inquiries count
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
            limit: int = 50
    ) -> List[PropertyInquiry]:
        """Get inquiries for a property"""
        query = db.query(PropertyInquiry).filter(
            PropertyInquiry.property_id == property_id
        )

        if is_responded is not None:
            query = query.filter(PropertyInquiry.is_responded == is_responded)

        return query.order_by(
            PropertyInquiry.created_at.desc()
        ).offset(skip).limit(limit).all()


# Singleton instances
property_agent_crud = CRUDPropertyAgent(PropertyAgent)
property_crud = CRUDProperty(Property)
property_viewing_crud = CRUDPropertyViewing(PropertyViewing)
property_offer_crud = CRUDPropertyOffer(PropertyOffer)
saved_property_crud = CRUDSavedProperty(SavedProperty)
property_inquiry_crud = CRUDPropertyInquiry(PropertyInquiry)