"""
app/services/property_service.py

FIXES vs previous version:
  1.  [HARD RULE §2/§4] local_government removed from search_properties()
      signature and from the call to property_crud.search_properties().
      Blueprint §2: no LGA filtering anywhere in the codebase.
      local_government remains in _property_to_dict() for DISPLAY only.

  2.  [HARD RULE §16.4] datetime.utcnow() × 2 → datetime.now(timezone.utc)
      in accept_offer() and reject_offer().

  3.  timezone imported from datetime module.
"""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.crud.business_crud import business_crud
from app.crud.properties_crud import (
    property_agent_crud,
    property_crud,
    property_inquiry_crud,
    property_offer_crud,
    property_viewing_crud,
    saved_property_crud,
)
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)
from app.models.properties_model import (
    OfferStatusEnum,
    Property,
    PropertyOffer,
    PropertyStatusEnum,
    PropertyViewing,
)
from app.models.user_model import User


def _utcnow() -> datetime:
    """Blueprint §16.4 HARD RULE: always timezone-aware UTC."""
    return datetime.now(timezone.utc)


class PropertyService:

    @staticmethod
    def _agent_to_dict(agent) -> Optional[Dict[str, Any]]:
        if agent is None:
            return None
        return {
            "id":                   agent.id,
            "business_id":          agent.business_id,
            "agent_license_number": agent.agent_license_number,
            "years_of_experience":  agent.years_of_experience,
            "specializations":      agent.specializations or [],
            "service_areas":        agent.service_areas or [],
            "languages":            agent.languages or [],
            "total_properties":     agent.total_properties,
            "properties_sold":      agent.properties_sold,
            "properties_rented":    agent.properties_rented,
        }

    @staticmethod
    def _business_to_dict(biz) -> Optional[Dict[str, Any]]:
        if biz is None:
            return None
        return {
            "id":                biz.id,
            "business_name":     biz.business_name,
            "address":           biz.address,
            "city":              getattr(biz, "city", None),
            "state":             getattr(biz, "state", None),
            "business_phone":    getattr(biz, "business_phone", None),
            "logo":              getattr(biz, "logo", None),
            "average_rating":    float(biz.average_rating) if biz.average_rating else 0.0,
            "total_reviews":     getattr(biz, "total_reviews", 0),
            "is_verified":       getattr(biz, "is_verified", False),
            "subscription_tier": getattr(biz, "subscription_tier", None),
        }

    @staticmethod
    def _property_to_dict(prop, agent, biz) -> Dict[str, Any]:
        """
        Project Property ORM to plain dict.
        Geography/WKBElement columns excluded. local_government included
        for display only — NOT used for filtering.
        """
        return {
            "id":                    prop.id,
            "agent_id":              prop.agent_id,
            "title":                 prop.title,
            "description":           prop.description,
            "property_type":         prop.property_type,
            "property_subtype":      prop.property_subtype,
            "listing_type":          prop.listing_type,
            "price":                 prop.price,
            "price_per_sqm":         prop.price_per_sqm,
            "monthly_rent":          prop.monthly_rent,
            "service_charge":        prop.service_charge,
            "payment_frequency":     prop.payment_frequency,
            "security_deposit":      prop.security_deposit,
            "lease_duration_months": prop.lease_duration_months,
            "address":               prop.address,
            "city":                  prop.city,
            "state":                 prop.state,
            # local_government: display label only — NOT for filtering
            "local_government":      prop.local_government,
            "postal_code":           prop.postal_code,
            # location (Geography/WKBElement) intentionally omitted
            "bedrooms":              prop.bedrooms,
            "bathrooms":             prop.bathrooms,
            "toilets":               prop.toilets,
            "living_rooms":          prop.living_rooms,
            "plot_size_sqm":         prop.plot_size_sqm,
            "building_size_sqm":     prop.building_size_sqm,
            "year_built":            prop.year_built,
            "floors":                prop.floors,
            "floor_number":          prop.floor_number,
            "parking_spaces":        prop.parking_spaces,
            "condition":             prop.condition,
            "furnishing_status":     prop.furnishing_status,
            "features":              prop.features or [],
            "title_document_type":   prop.title_document_type,
            "has_survey_plan":       prop.has_survey_plan,
            "has_building_plan":     prop.has_building_plan,
            "images":                prop.images or [],
            "videos":                prop.videos or [],
            "virtual_tour_url":      prop.virtual_tour_url,
            "floor_plan_images":     prop.floor_plan_images or [],
            "nearby_landmarks":      prop.nearby_landmarks or [],
            "available_from":        prop.available_from,
            "is_negotiable":         prop.is_negotiable,
            "status":                prop.status,
            "is_featured":           prop.is_featured,
            "is_verified":           prop.is_verified,
            "views_count":           prop.views_count,
            "saves_count":           prop.saves_count,
            "inquiries_count":       prop.inquiries_count,
            "created_at":            prop.created_at,
            "agent":                 PropertyService._agent_to_dict(agent),
            "business":              PropertyService._business_to_dict(biz),
        }

    @staticmethod
    def search_properties(
        db: Session,
        *,
        query_text:        Optional[str]     = None,
        property_type:     Optional[str]     = None,
        property_subtype:  Optional[str]     = None,
        listing_type:      Optional[str]     = None,
        city:              Optional[str]     = None,
        state:             Optional[str]     = None,
        # local_government DELETED — Blueprint §2/§4 HARD RULE
        location:          Optional[tuple]   = None,
        radius_km:         float             = 5.0,
        min_price:         Optional[Decimal] = None,
        max_price:         Optional[Decimal] = None,
        min_bedrooms:      Optional[int]     = None,
        max_bedrooms:      Optional[int]     = None,
        min_bathrooms:     Optional[int]     = None,
        min_plot_size:     Optional[Decimal] = None,
        max_plot_size:     Optional[Decimal] = None,
        furnishing_status: Optional[str]     = None,
        features:          Optional[List[str]] = None,
        is_featured:       Optional[bool]    = None,
        is_verified:       Optional[bool]    = None,
        sort_by:           str               = "created_at",
        skip:              int               = 0,
        limit:             int               = 20,
    ) -> List[Dict[str, Any]]:
        properties = property_crud.search_properties(
            db,
            query_text=query_text,
            property_type=property_type,
            property_subtype=property_subtype,
            listing_type=listing_type,
            city=city,
            state=state,
            # local_government intentionally omitted — Blueprint §2 HARD RULE
            location=location,
            radius_km=radius_km,
            min_price=min_price,
            max_price=max_price,
            min_bedrooms=min_bedrooms,
            max_bedrooms=max_bedrooms,
            min_bathrooms=min_bathrooms,
            min_plot_size=min_plot_size,
            max_plot_size=max_plot_size,
            furnishing_status=furnishing_status,
            features=features,
            is_featured=is_featured,
            is_verified=is_verified,
            sort_by=sort_by,
            skip=skip,
            limit=limit,
        )

        results = []
        for prop in properties:
            agent = property_agent_crud.get(db, id=prop.agent_id)
            biz   = business_crud.get(db, id=agent.business_id) if agent else None
            results.append(PropertyService._property_to_dict(prop, agent, biz))
        return results

    @staticmethod
    def get_property_details(db: Session, *, property_id: UUID) -> Dict[str, Any]:
        property_obj = property_crud.get(db, id=property_id)
        if not property_obj:
            raise NotFoundException("Property")

        property_crud.increment_views(db, property_id=property_id)
        agent       = property_agent_crud.get(db, id=property_obj.agent_id)
        biz         = business_crud.get(db, id=agent.business_id) if agent else None
        nearby_objs = property_crud.get_nearby_properties(db, property_id=property_id)

        pending_offers = db.query(PropertyOffer).filter(
            PropertyOffer.property_id == property_id,
            PropertyOffer.status      == OfferStatusEnum.PENDING,
        ).count()

        from datetime import date as _date
        upcoming_viewings = db.query(PropertyViewing).filter(
            PropertyViewing.property_id == property_id,
            PropertyViewing.viewing_date >= _date.today(),
            PropertyViewing.status.in_(["pending", "confirmed"]),
        ).count()

        return {
            **PropertyService._property_to_dict(property_obj, agent, biz),
            "nearby_properties": [
                PropertyService._property_to_dict(n, None, None)
                for n in (nearby_objs or [])
            ],
            "pending_offers_count":    pending_offers,
            "upcoming_viewings_count": upcoming_viewings,
        }

    @staticmethod
    def accept_offer(db: Session, *, offer_id: UUID, agent_id: UUID) -> PropertyOffer:
        offer = property_offer_crud.get(db, id=offer_id)
        if not offer:
            raise NotFoundException("Offer")

        property_obj = property_crud.get(db, id=offer.property_id)
        if property_obj.agent_id != agent_id:
            raise PermissionDeniedException()
        if offer.status != OfferStatusEnum.PENDING:
            raise ValidationException("Only pending offers can be accepted")

        offer.status      = OfferStatusEnum.ACCEPTED
        offer.accepted_at = _utcnow()   # Blueprint §16.4 HARD RULE

        db.query(PropertyOffer).filter(
            PropertyOffer.property_id == offer.property_id,
            PropertyOffer.id          != offer_id,
            PropertyOffer.status      == OfferStatusEnum.PENDING,
        ).update({"status": OfferStatusEnum.REJECTED})

        agent = property_agent_crud.get(db, id=agent_id)
        if property_obj.listing_type in ["for_sale"]:
            property_obj.status = PropertyStatusEnum.SOLD
            if agent:
                agent.properties_sold        += 1
                agent.total_value_transacted += offer.offer_amount
        else:
            property_obj.status = PropertyStatusEnum.RENTED
            if agent:
                agent.properties_rented      += 1
                agent.total_value_transacted += offer.offer_amount

        db.commit()
        db.refresh(offer)
        return offer

    @staticmethod
    def reject_offer(
        db: Session, *, offer_id: UUID, agent_id: UUID,
        reason: Optional[str] = None
    ) -> PropertyOffer:
        offer = property_offer_crud.get(db, id=offer_id)
        if not offer:
            raise NotFoundException("Offer")

        property_obj = property_crud.get(db, id=offer.property_id)
        if property_obj.agent_id != agent_id:
            raise PermissionDeniedException()
        if offer.status != OfferStatusEnum.PENDING:
            raise ValidationException("Only pending offers can be rejected")

        offer.status           = OfferStatusEnum.REJECTED
        offer.rejected_at      = _utcnow()   # Blueprint §16.4 HARD RULE
        offer.rejection_reason = reason

        other_pending = db.query(PropertyOffer).filter(
            PropertyOffer.property_id == offer.property_id,
            PropertyOffer.id          != offer_id,
            PropertyOffer.status      == OfferStatusEnum.PENDING,
        ).count()

        if other_pending == 0:
            property_obj.status = PropertyStatusEnum.AVAILABLE

        db.commit()
        db.refresh(offer)
        return offer

    @staticmethod
    def counter_offer(
        db: Session, *, offer_id: UUID, agent_id: UUID,
        counter_amount: Decimal, counter_message: Optional[str] = None
    ) -> PropertyOffer:
        offer = property_offer_crud.get(db, id=offer_id)
        if not offer:
            raise NotFoundException("Offer")

        property_obj = property_crud.get(db, id=offer.property_id)
        if property_obj.agent_id != agent_id:
            raise PermissionDeniedException()
        if offer.status != OfferStatusEnum.PENDING:
            raise ValidationException("Only pending offers can be countered")

        offer.status               = OfferStatusEnum.COUNTERED
        offer.counter_offer_amount = counter_amount
        offer.counter_message      = counter_message

        db.commit()
        db.refresh(offer)
        return offer


property_service = PropertyService()