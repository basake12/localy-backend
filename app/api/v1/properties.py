from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
from datetime import date, datetime
from decimal import Decimal

from app.core.database import get_db
from app.dependencies import (
    get_current_active_user,
    require_customer,
    require_business,
    get_pagination_params
)
from app.schemas.common import SuccessResponse
from app.schemas.properties import (
    PropertyAgentCreateRequest,
    PropertyAgentResponse,
    PropertyCreateRequest,
    PropertyResponse,
    PropertyListResponse,
    ViewingCreateRequest,
    ViewingResponse,
    OfferCreateRequest,
    OfferResponse,
    PropertySearchFilters
)
from app.services.property_service import property_service
from app.crud.properties import (
    property_agent_crud,
    property_crud,
    property_viewing_crud,
    property_offer_crud,
    saved_property_crud,
    property_inquiry_crud
)
from app.crud.business import business_crud
from app.models.user import User
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException
)

router = APIRouter()


# ============================================
# PROPERTY SEARCH & DISCOVERY (PUBLIC)
# ============================================

@router.post("/search", response_model=SuccessResponse[List[dict]])
def search_properties(
        *,
        db: Session = Depends(get_db),
        search_params: PropertySearchFilters,
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """
    Search properties

    - Public endpoint
    - Location-based with PostGIS
    - Multi-filter: type, price, bedrooms, features
    - Sort by price, popularity, newest
    """
    location = None
    if search_params.location:
        location = (
            search_params.location.latitude,
            search_params.location.longitude
        )

    results = property_service.search_properties(
        db,
        query_text=search_params.query,
        property_type=search_params.property_type,
        property_subtype=search_params.property_subtype,
        listing_type=search_params.listing_type,
        city=search_params.city,
        state=search_params.state,
        location=location,
        radius_km=search_params.radius_km or 20.0,
        min_price=search_params.min_price,
        max_price=search_params.max_price,
        min_bedrooms=search_params.min_bedrooms,
        max_bedrooms=search_params.max_bedrooms,
        min_bathrooms=search_params.min_bathrooms,
        min_plot_size=search_params.min_plot_size,
        max_plot_size=search_params.max_plot_size,
        furnishing_status=search_params.furnishing_status,
        features=search_params.features if search_params.features else None,
        is_featured=search_params.is_featured,
        is_verified=search_params.is_verified,
        sort_by=search_params.sort_by,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": results
    }


@router.get("/{property_id}", response_model=SuccessResponse[dict])
def get_property_details(
        *,
        db: Session = Depends(get_db),
        property_id: UUID
) -> dict:
    """
    Get property details

    - Public endpoint
    - Increments view count
    - Returns nearby properties
    """
    property_data = property_service.get_property_details(
        db,
        property_id=property_id
    )

    return {
        "success": True,
        "data": property_data
    }


@router.get("/slug/{slug}", response_model=SuccessResponse[dict])
def get_property_by_slug(
        *,
        db: Session = Depends(get_db),
        slug: str
) -> dict:
    """Get property by URL slug"""
    property_obj = property_crud.get_by_slug(db, slug=slug)
    if not property_obj:
        raise NotFoundException("Property")

    property_data = property_service.get_property_details(
        db,
        property_id=property_obj.id
    )

    return {
        "success": True,
        "data": property_data
    }


# ============================================
# PROPERTY AGENT MANAGEMENT (BUSINESS)
# ============================================

@router.post("/agents", response_model=SuccessResponse[PropertyAgentResponse], status_code=status.HTTP_201_CREATED)
def create_property_agent(
        *,
        db: Session = Depends(get_db),
        agent_in: PropertyAgentCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Create property agent profile

    - Business must be in 'properties' category
    """
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    if business.category != "properties":
        raise ValidationException("Only properties category businesses can create agents")

    # Check if agent already exists
    existing = property_agent_crud.get_by_business_id(db, business_id=business.id)
    if existing:
        raise ValidationException("Property agent already exists for this business")

    agent_data = agent_in.model_dump()
    agent_data["business_id"] = business.id

    agent = property_agent_crud.create_from_dict(db, obj_in=agent_data)

    return {
        "success": True,
        "data": agent
    }


@router.get("/agents/my", response_model=SuccessResponse[PropertyAgentResponse])
def get_my_agent_profile(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business)
) -> dict:
    """Get current business's agent profile"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)
    if not agent:
        raise NotFoundException("Property agent")

    return {
        "success": True,
        "data": agent
    }


# ============================================
# PROPERTY MANAGEMENT (BUSINESS / AGENT)
# ============================================

@router.post("/", response_model=SuccessResponse[PropertyResponse], status_code=status.HTTP_201_CREATED)
def create_property(
        *,
        db: Session = Depends(get_db),
        property_in: PropertyCreateRequest,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Create property listing

    - Agent must exist
    - Auto-generates slug, price_per_sqm
    """
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business")

    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)
    if not agent:
        raise NotFoundException("Property agent. Create agent profile first.")

    property_data = property_in.model_dump()

    property_obj = property_crud.create_property(
        db,
        agent_id=agent.id,
        property_data=property_data
    )

    return {
        "success": True,
        "data": property_obj
    }


@router.get("/my/listings", response_model=SuccessResponse[List[PropertyResponse]])
def get_my_properties(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business),
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get agent's property listings"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)

    if not agent:
        raise NotFoundException("Property agent")

    properties = property_crud.get_by_agent(
        db,
        agent_id=agent.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": properties
    }


@router.put("/{property_id}", response_model=SuccessResponse[PropertyResponse])
def update_property(
        *,
        db: Session = Depends(get_db),
        property_id: UUID,
        title: Optional[str] = None,
        price: Optional[Decimal] = None,
        status: Optional[str] = None,
        is_featured: Optional[bool] = None,
        is_active: Optional[bool] = None,
        current_user: User = Depends(require_business)
) -> dict:
    """Update property listing"""
    property_obj = property_crud.get(db, id=property_id)
    if not property_obj:
        raise NotFoundException("Property")

    # Verify ownership
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)

    if not agent or property_obj.agent_id != agent.id:
        raise PermissionDeniedException()

    update_data = {}
    if title is not None:
        update_data["title"] = title
    if price is not None:
        update_data["price"] = price
    if status is not None:
        update_data["status"] = status
    if is_featured is not None:
        update_data["is_featured"] = is_featured
    if is_active is not None:
        update_data["is_active"] = is_active

    property_obj = property_crud.update(db, db_obj=property_obj, obj_in=update_data)

    return {
        "success": True,
        "data": property_obj
    }


@router.delete("/{property_id}", response_model=SuccessResponse[dict])
def delete_property(
        *,
        db: Session = Depends(get_db),
        property_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Soft-delete property listing"""
    property_obj = property_crud.get(db, id=property_id)
    if not property_obj:
        raise NotFoundException("Property")

    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)

    if not agent or property_obj.agent_id != agent.id:
        raise PermissionDeniedException()

    property_obj.is_active = False
    db.commit()

    return {
        "success": True,
        "data": {"message": "Property listing removed"}
    }


# ============================================
# SAVED PROPERTIES (CUSTOMER)
# ============================================

@router.post("/{property_id}/save", response_model=SuccessResponse[dict])
def toggle_save_property(
        *,
        db: Session = Depends(get_db),
        property_id: UUID,
        notes: Optional[str] = None,
        current_user: User = Depends(require_customer)
) -> dict:
    """Toggle save/unsave property"""
    result = saved_property_crud.toggle_save(
        db,
        property_id=property_id,
        customer_id=current_user.id,
        notes=notes
    )

    return {
        "success": True,
        "data": result
    }


@router.get("/saved/my", response_model=SuccessResponse[List[dict]])
def get_saved_properties(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_customer),
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get customer's saved properties"""
    saved = saved_property_crud.get_saved_properties(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    results = []
    for item in saved:
        results.append({
            "saved_at": item.created_at,
            "notes": item.notes,
            "property": item.property
        })

    return {
        "success": True,
        "data": results
    }


# ============================================
# PROPERTY VIEWINGS (CUSTOMER)
# ============================================

@router.post("/viewings", response_model=SuccessResponse[ViewingResponse], status_code=status.HTTP_201_CREATED)
def create_viewing(
        *,
        db: Session = Depends(get_db),
        viewing_in: ViewingCreateRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Schedule property viewing

    - Checks for conflicting viewings
    - Generates confirmation code
    """
    viewing = property_viewing_crud.create_viewing(
        db,
        property_id=viewing_in.property_id,
        customer_id=current_user.id,
        viewing_date=viewing_in.viewing_date,
        viewing_time=viewing_in.viewing_time,
        viewing_type=viewing_in.viewing_type,
        customer_name=viewing_in.customer_name,
        customer_phone=viewing_in.customer_phone,
        customer_email=viewing_in.customer_email,
        number_of_people=viewing_in.number_of_people,
        special_requests=viewing_in.special_requests
    )

    return {
        "success": True,
        "data": viewing
    }


@router.get("/viewings/my", response_model=SuccessResponse[List[ViewingResponse]])
def get_my_viewings(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_customer),
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get customer's viewing appointments"""
    viewings = property_viewing_crud.get_customer_viewings(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": viewings
    }


@router.post("/viewings/{viewing_id}/cancel", response_model=SuccessResponse[ViewingResponse])
def cancel_viewing(
        *,
        db: Session = Depends(get_db),
        viewing_id: UUID,
        reason: Optional[str] = None,
        current_user: User = Depends(require_customer)
) -> dict:
    """Cancel viewing appointment"""
    viewing = property_viewing_crud.get(db, id=viewing_id)
    if not viewing:
        raise NotFoundException("Viewing")

    if viewing.customer_id != current_user.id:
        raise PermissionDeniedException()

    if viewing.status in ["completed", "cancelled"]:
        raise ValidationException("Cannot cancel completed or already cancelled viewing")

    viewing.status = "cancelled"
    viewing.cancelled_at = datetime.utcnow()
    viewing.cancellation_reason = reason
    db.commit()
    db.refresh(viewing)

    return {
        "success": True,
        "data": viewing
    }


# ============================================
# PROPERTY OFFERS (CUSTOMER)
# ============================================

@router.post("/offers", response_model=SuccessResponse[OfferResponse], status_code=status.HTTP_201_CREATED)
def create_offer(
        *,
        db: Session = Depends(get_db),
        offer_in: OfferCreateRequest,
        current_user: User = Depends(require_customer)
) -> dict:
    """
    Make offer on property

    - Checks property availability
    - Prevents duplicate pending offers
    - Updates property status to under_offer
    """
    offer = property_offer_crud.create_offer(
        db,
        property_id=offer_in.property_id,
        customer_id=current_user.id,
        offer_amount=offer_in.offer_amount,
        proposed_payment_plan=offer_in.proposed_payment_plan,
        proposed_lease_duration=offer_in.proposed_lease_duration,
        message=offer_in.message
    )

    return {
        "success": True,
        "data": offer
    }


@router.get("/offers/my", response_model=SuccessResponse[List[OfferResponse]])
def get_my_offers(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_customer),
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get customer's offers"""
    offers = property_offer_crud.get_customer_offers(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": offers
    }


@router.post("/offers/{offer_id}/withdraw", response_model=SuccessResponse[OfferResponse])
def withdraw_offer(
        *,
        db: Session = Depends(get_db),
        offer_id: UUID,
        current_user: User = Depends(require_customer)
) -> dict:
    """Withdraw own offer"""
    offer = property_offer_crud.get(db, id=offer_id)
    if not offer:
        raise NotFoundException("Offer")

    if offer.customer_id != current_user.id:
        raise PermissionDeniedException()

    if offer.status not in ["pending", "countered"]:
        raise ValidationException("Can only withdraw pending or countered offers")

    offer.status = "withdrawn"
    db.commit()
    db.refresh(offer)

    return {
        "success": True,
        "data": offer
    }


# ============================================
# PROPERTY INQUIRIES (CUSTOMER)
# ============================================

@router.post("/{property_id}/inquire", response_model=SuccessResponse[dict], status_code=status.HTTP_201_CREATED)
def create_inquiry(
        *,
        db: Session = Depends(get_db),
        property_id: UUID,
        subject: str,
        message: str,
        customer_name: str,
        customer_phone: str,
        customer_email: str,
        current_user: User = Depends(require_customer)
) -> dict:
    """Submit inquiry about a property"""
    inquiry = property_inquiry_crud.create_inquiry(
        db,
        property_id=property_id,
        customer_id=current_user.id,
        subject=subject,
        message=message,
        customer_name=customer_name,
        customer_phone=customer_phone,
        customer_email=customer_email
    )

    return {
        "success": True,
        "data": inquiry
    }


# ============================================
# AGENT VIEWING MANAGEMENT (BUSINESS)
# ============================================

@router.get("/viewings/my/agent", response_model=SuccessResponse[List[ViewingResponse]])
def get_agent_viewings(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business),
        pagination: dict = Depends(get_pagination_params),
        viewing_date: Optional[date] = Query(None),
        status: Optional[str] = Query(None)
) -> dict:
    """Get all viewings across agent's properties"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)

    if not agent:
        raise NotFoundException("Property agent")

    viewings = property_viewing_crud.get_agent_viewings(
        db,
        agent_id=agent.id,
        viewing_date=viewing_date,
        status=status,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": viewings
    }


@router.post("/viewings/{viewing_id}/confirm", response_model=SuccessResponse[ViewingResponse])
def confirm_viewing(
        *,
        db: Session = Depends(get_db),
        viewing_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """Confirm viewing (agent action)"""
    viewing = property_viewing_crud.get(db, id=viewing_id)
    if not viewing:
        raise NotFoundException("Viewing")

    # Verify ownership
    property_obj = property_crud.get(db, id=viewing.property_id)
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)

    if not agent or property_obj.agent_id != agent.id:
        raise PermissionDeniedException()

    viewing.status = "confirmed"
    viewing.confirmed_at = datetime.utcnow()
    db.commit()
    db.refresh(viewing)

    return {
        "success": True,
        "data": viewing
    }


@router.post("/viewings/{viewing_id}/complete", response_model=SuccessResponse[ViewingResponse])
def complete_viewing(
        *,
        db: Session = Depends(get_db),
        viewing_id: UUID,
        agent_notes: Optional[str] = None,
        current_user: User = Depends(require_business)
) -> dict:
    """Mark viewing as completed (agent action)"""
    viewing = property_viewing_crud.get(db, id=viewing_id)
    if not viewing:
        raise NotFoundException("Viewing")

    property_obj = property_crud.get(db, id=viewing.property_id)
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)

    if not agent or property_obj.agent_id != agent.id:
        raise PermissionDeniedException()

    if viewing.status != "confirmed":
        raise ValidationException("Can only complete confirmed viewings")

    viewing.status = "completed"
    viewing.completed_at = datetime.utcnow()
    viewing.agent_notes = agent_notes
    db.commit()
    db.refresh(viewing)

    return {
        "success": True,
        "data": viewing
    }


# ============================================
# AGENT OFFER MANAGEMENT (BUSINESS)
# ============================================

@router.get("/{property_id}/offers", response_model=SuccessResponse[List[OfferResponse]])
def get_property_offers(
        *,
        db: Session = Depends(get_db),
        property_id: UUID,
        current_user: User = Depends(require_business),
        status: Optional[str] = Query(None)
) -> dict:
    """Get offers on a property (agent)"""
    property_obj = property_crud.get(db, id=property_id)
    if not property_obj:
        raise NotFoundException("Property")

    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)

    if not agent or property_obj.agent_id != agent.id:
        raise PermissionDeniedException()

    offers = property_offer_crud.get_property_offers(
        db,
        property_id=property_id,
        status=status
    )

    return {
        "success": True,
        "data": offers
    }


@router.post("/offers/{offer_id}/accept", response_model=SuccessResponse[OfferResponse])
def accept_offer(
        *,
        db: Session = Depends(get_db),
        offer_id: UUID,
        current_user: User = Depends(require_business)
) -> dict:
    """
    Accept offer (agent action)

    - Rejects all other pending offers
    - Updates property status to sold/rented
    - Updates agent stats
    """
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)

    if not agent:
        raise NotFoundException("Property agent")

    offer = property_service.accept_offer(
        db,
        offer_id=offer_id,
        agent_id=agent.id
    )

    return {
        "success": True,
        "data": offer
    }


@router.post("/offers/{offer_id}/reject", response_model=SuccessResponse[OfferResponse])
def reject_offer(
        *,
        db: Session = Depends(get_db),
        offer_id: UUID,
        reason: Optional[str] = None,
        current_user: User = Depends(require_business)
) -> dict:
    """Reject offer (agent action)"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)

    if not agent:
        raise NotFoundException("Property agent")

    offer = property_service.reject_offer(
        db,
        offer_id=offer_id,
        agent_id=agent.id,
        reason=reason
    )

    return {
        "success": True,
        "data": offer
    }


@router.post("/offers/{offer_id}/counter", response_model=SuccessResponse[OfferResponse])
def counter_offer(
        *,
        db: Session = Depends(get_db),
        offer_id: UUID,
        counter_amount: Decimal,
        counter_message: Optional[str] = None,
        current_user: User = Depends(require_business)
) -> dict:
    """Counter offer with new amount (agent action)"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)

    if not agent:
        raise NotFoundException("Property agent")

    offer = property_service.counter_offer(
        db,
        offer_id=offer_id,
        agent_id=agent.id,
        counter_amount=counter_amount,
        counter_message=counter_message
    )

    return {
        "success": True,
        "data": offer
    }


# ============================================
# AGENT INQUIRY MANAGEMENT (BUSINESS)
# ============================================

@router.get("/{property_id}/inquiries", response_model=SuccessResponse[List[dict]])
def get_property_inquiries(
        *,
        db: Session = Depends(get_db),
        property_id: UUID,
        current_user: User = Depends(require_business),
        is_responded: Optional[bool] = Query(None),
        pagination: dict = Depends(get_pagination_params)
) -> dict:
    """Get inquiries for a property (agent)"""
    property_obj = property_crud.get(db, id=property_id)
    if not property_obj:
        raise NotFoundException("Property")

    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)

    if not agent or property_obj.agent_id != agent.id:
        raise PermissionDeniedException()

    inquiries = property_inquiry_crud.get_property_inquiries(
        db,
        property_id=property_id,
        is_responded=is_responded,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": inquiries
    }


@router.post("/inquiries/{inquiry_id}/respond", response_model=SuccessResponse[dict])
def respond_to_inquiry(
        *,
        db: Session = Depends(get_db),
        inquiry_id: UUID,
        response_message: str,
        current_user: User = Depends(require_business)
) -> dict:
    """Respond to an inquiry (agent action)"""
    inquiry = property_inquiry_crud.get(db, id=inquiry_id)
    if not inquiry:
        raise NotFoundException("Inquiry")

    property_obj = property_crud.get(db, id=inquiry.property_id)
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)

    if not agent or property_obj.agent_id != agent.id:
        raise PermissionDeniedException()

    inquiry.is_responded = True
    inquiry.response_message = response_message
    inquiry.responded_at = datetime.utcnow()
    db.commit()
    db.refresh(inquiry)

    return {
        "success": True,
        "data": inquiry
    }


# ============================================
# AGENT STATS (BUSINESS)
# ============================================

@router.get("/agents/my/stats", response_model=SuccessResponse[dict])
def get_agent_stats(
        *,
        db: Session = Depends(get_db),
        current_user: User = Depends(require_business)
) -> dict:
    """Get agent performance statistics"""
    business = business_crud.get_by_user_id(db, user_id=current_user.id)
    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)

    if not agent:
        raise NotFoundException("Property agent")

    from sqlalchemy import func
    from app.models.properties import Property, PropertyViewing, PropertyOffer

    # Active listings
    active_listings = db.query(func.count(Property.id)).filter(
        Property.agent_id == agent.id,
        Property.is_active == True
    ).scalar()

    # Total viewings
    total_viewings = db.query(func.count(PropertyViewing.id)).join(Property).filter(
        Property.agent_id == agent.id
    ).scalar()

    # Total offers received
    total_offers = db.query(func.count(PropertyOffer.id)).join(Property).filter(
        Property.agent_id == agent.id
    ).scalar()

    # Total views across all listings
    total_views = db.query(func.sum(Property.views_count)).filter(
        Property.agent_id == agent.id
    ).scalar() or 0

    return {
        "success": True,
        "data": {
            "total_properties": agent.total_properties,
            "active_listings": active_listings,
            "properties_sold": agent.properties_sold,
            "properties_rented": agent.properties_rented,
            "total_value_transacted": float(agent.total_value_transacted),
            "total_viewings": total_viewings,
            "total_offers": total_offers,
            "total_views": total_views
        }
    }