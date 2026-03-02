from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import date, time, datetime, timedelta
from decimal import Decimal

from app.crud.properties_crud import (
    property_agent_crud,
    property_crud,
    property_viewing_crud,
    property_offer_crud,
    saved_property_crud,
    property_inquiry_crud
)
from app.crud.business_crud import business_crud
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    PermissionDeniedException
)
from app.models.user_model import User
from app.models.properties_model import (
    Property, PropertyViewing, PropertyOffer,
    PropertyStatusEnum, OfferStatusEnum
)


class PropertyService:
    """Business logic for property operations"""

    @staticmethod
    def search_properties(
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
    ) -> List[Dict[str, Any]]:
        """Search and enrich properties with agent info"""
        properties = property_crud.search_properties(
            db,
            query_text=query_text,
            property_type=property_type,
            property_subtype=property_subtype,
            listing_type=listing_type,
            city=city,
            state=state,
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
            limit=limit
        )

        results = []
        for prop in properties:
            agent = property_agent_crud.get(db, id=prop.agent_id)
            business = business_crud.get(db, id=agent.business_id) if agent else None

            results.append({
                "property": prop,
                "agent": agent,
                "business": business
            })

        return results

    @staticmethod
    def get_property_details(
            db: Session,
            *,
            property_id: UUID
    ) -> Dict[str, Any]:
        """Get full property details with agent, nearby, offers"""
        property_obj = property_crud.get(db, id=property_id)
        if not property_obj:
            raise NotFoundException("Property")

        # Increment views
        property_crud.increment_views(db, property_id=property_id)

        # Get agent info
        agent = property_agent_crud.get(db, id=property_obj.agent_id)
        business = business_crud.get(db, id=agent.business_id) if agent else None

        # Get nearby properties
        nearby = property_crud.get_nearby_properties(db, property_id=property_id)

        # Get pending offers count
        pending_offers = db.query(PropertyOffer).filter(
            PropertyOffer.property_id == property_id,
            PropertyOffer.status == OfferStatusEnum.PENDING
        ).count()

        # Get upcoming viewings count
        upcoming_viewings = db.query(PropertyViewing).filter(
            PropertyViewing.property_id == property_id,
            PropertyViewing.viewing_date >= date.today(),
            PropertyViewing.status.in_(["pending", "confirmed"])
        ).count()

        return {
            "property": property_obj,
            "agent": agent,
            "business": business,
            "nearby_properties": nearby,
            "pending_offers_count": pending_offers,
            "upcoming_viewings_count": upcoming_viewings
        }

    @staticmethod
    def accept_offer(
            db: Session,
            *,
            offer_id: UUID,
            agent_id: UUID
    ) -> PropertyOffer:
        """Accept an offer and update property status"""
        offer = property_offer_crud.get(db, id=offer_id)
        if not offer:
            raise NotFoundException("Offer")

        # Verify agent owns the property
        property_obj = property_crud.get(db, id=offer.property_id)
        if property_obj.agent_id != agent_id:
            raise PermissionDeniedException()

        if offer.status != OfferStatusEnum.PENDING:
            raise ValidationException("Only pending offers can be accepted")

        # Accept offer
        offer.status = OfferStatusEnum.ACCEPTED
        offer.accepted_at = datetime.utcnow()

        # Reject all other pending offers on same property
        db.query(PropertyOffer).filter(
            PropertyOffer.property_id == offer.property_id,
            PropertyOffer.id != offer_id,
            PropertyOffer.status == OfferStatusEnum.PENDING
        ).update({"status": OfferStatusEnum.REJECTED})

        # Update property status based on listing type
        if property_obj.listing_type in ["for_sale"]:
            property_obj.status = PropertyStatusEnum.SOLD

            # Update agent stats
            agent = property_agent_crud.get(db, id=agent_id)
            agent.properties_sold += 1
            agent.total_value_transacted += offer.offer_amount
        else:
            property_obj.status = PropertyStatusEnum.RENTED

            agent = property_agent_crud.get(db, id=agent_id)
            agent.properties_rented += 1
            agent.total_value_transacted += offer.offer_amount

        db.commit()
        db.refresh(offer)

        return offer

    @staticmethod
    def reject_offer(
            db: Session,
            *,
            offer_id: UUID,
            agent_id: UUID,
            reason: Optional[str] = None
    ) -> PropertyOffer:
        """Reject an offer"""
        offer = property_offer_crud.get(db, id=offer_id)
        if not offer:
            raise NotFoundException("Offer")

        property_obj = property_crud.get(db, id=offer.property_id)
        if property_obj.agent_id != agent_id:
            raise PermissionDeniedException()

        if offer.status != OfferStatusEnum.PENDING:
            raise ValidationException("Only pending offers can be rejected")

        offer.status = OfferStatusEnum.REJECTED
        offer.rejected_at = datetime.utcnow()
        offer.rejection_reason = reason

        # Check if other pending offers exist - if not, revert to available
        other_pending = db.query(PropertyOffer).filter(
            PropertyOffer.property_id == offer.property_id,
            PropertyOffer.id != offer_id,
            PropertyOffer.status == OfferStatusEnum.PENDING
        ).count()

        if other_pending == 0:
            property_obj.status = PropertyStatusEnum.AVAILABLE

        db.commit()
        db.refresh(offer)

        return offer

    @staticmethod
    def counter_offer(
            db: Session,
            *,
            offer_id: UUID,
            agent_id: UUID,
            counter_amount: Decimal,
            counter_message: Optional[str] = None
    ) -> PropertyOffer:
        """Counter an offer with new amount"""
        offer = property_offer_crud.get(db, id=offer_id)
        if not offer:
            raise NotFoundException("Offer")

        property_obj = property_crud.get(db, id=offer.property_id)
        if property_obj.agent_id != agent_id:
            raise PermissionDeniedException()

        if offer.status != OfferStatusEnum.PENDING:
            raise ValidationException("Only pending offers can be countered")

        offer.status = OfferStatusEnum.COUNTERED
        offer.counter_offer_amount = counter_amount
        offer.counter_message = counter_message

        db.commit()
        db.refresh(offer)

        return offer


property_service = PropertyService()