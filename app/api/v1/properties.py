from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
from datetime import date, datetime, timezone
from decimal import Decimal

from app.core.database import get_db
from app.dependencies import (
    get_current_active_user,
    require_customer,
    require_business,
    get_pagination_params,
)
from app.schemas.common_schema import SuccessResponse
from app.schemas.properties_schema import (
    PropertyAgentCreateRequest,
    PropertyAgentUpdateRequest,
    PropertyAgentResponse,
    PropertyCreateRequest,
    PropertyUpdateRequest,
    PropertyResponse,
    PropertyListResponse,
    ViewingCreateRequest,
    ViewingResponse,
    OfferCreateRequest,
    CounterOfferRequest,
    RejectOfferRequest,
    OfferResponse,
    InquiryCreateRequest,
    InquiryRespondRequest,
    PropertySearchFilters,
)
from app.services.property_service import property_service
from app.crud.properties_crud import (
    property_agent_crud,
    property_crud,
    property_viewing_crud,
    property_offer_crud,
    saved_property_crud,
    property_inquiry_crud,
)
from app.models.business_model import Business
from app.models.properties_model import Property, PropertyViewing, PropertyOffer
from app.models.user_model import User
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)

router = APIRouter()


# ─────────────────────────────────────────────
# FIX: SYNC BUSINESS LOOKUP
# ─────────────────────────────────────────────
# business_crud is an AsyncCRUDBase — calling its methods without `await`
# in a sync router returns a coroutine object, not a Business instance.
# Accessing any attribute on that coroutine (.id, .category) raises:
#   AttributeError: 'coroutine' object has no attribute '...'
# Fix: query Business directly with the sync Session everywhere in this router.

def _get_business_sync(db: Session, user_id: UUID) -> Optional[Business]:
    """Sync Business lookup by user_id — safe to call from a sync router."""
    return db.query(Business).filter(Business.user_id == user_id).first()


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _get_verified_agent(db: Session, current_user: User):
    """Return the agent record for the authenticated business user or raise."""
    # FIX: was business_crud.get_by_user_id() — async crud in sync router → coroutine
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)
    if not agent:
        raise NotFoundException("Property agent. Create agent profile first.")
    return agent


def _assert_property_owned_by_agent(db: Session, property_id: UUID, agent):
    """Raise PermissionDeniedException unless the agent owns the property."""
    property_obj = property_crud.get(db, id=property_id)
    if not property_obj:
        raise NotFoundException("Property")
    if property_obj.agent_id != agent.id:
        raise PermissionDeniedException()
    return property_obj


# ── Blueprint §6.6 tier limits ────────────────────────────────────────────────
# Free:       0 listings — BLOCKED at creation. PropertyUpgradeGate shown.
# Starter:    Up to 15 active listings.
# Pro:        Up to 35 active listings.
# Enterprise: Unlimited.
#
# IMPORTANT: Starter is NOT blocked — it has a cap of 15.
# The previous code blocked Starter entirely, which is wrong per blueprint.
#
# Implementation uses business.subscription_tier (kept in sync by
# subscription_service on every plan change — Blueprint §7.2).
# NO inspect.iscoroutine() anti-pattern — reads the DB column directly.

_TIER_LISTING_LIMITS: dict[str, Optional[int]] = {
    "free":       0,     # Blocked — no listings
    "starter":    15,    # Up to 15
    "pro":        35,    # Up to 35
    "enterprise": None,  # Unlimited
}


def _get_tier_val(business) -> str:
    """Extract subscription_tier as a plain lowercase string."""
    tier = business.subscription_tier
    return tier.value if hasattr(tier, "value") else str(tier or "free").lower()


def _check_property_listing_gate(db: Session, current_user: User) -> tuple:
    """
    Enforce property listing tier limits before any DB write.
    Blueprint §6.6: Free=0, Starter=15, Pro=35, Enterprise=unlimited.
    Blueprint §3.4 HARD RULE: paywall shown at creation screen — BEFORE
    the user starts filling in listing details.

    Returns (business, agent) for use by the calling endpoint.
    Raises HTTP 403 with upgrade prompt if limit reached.
    """
    from fastapi import HTTPException
    from app.models.properties_model import PropertyStatusEnum

    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")

    tier_val = _get_tier_val(business)
    limit    = _TIER_LISTING_LIMITS.get(tier_val, 0)

    # Free: blocked entirely
    if limit == 0:
        raise HTTPException(
            status_code=403,
            detail={
                "error":        "property_listing_blocked",
                "message":      (
                    "Property listing creation requires a Pro or Enterprise plan. "
                    "Free plan: 0 listings allowed."
                ),
                "upgrade_url":  "/plans/upgrade",
                "current_tier": tier_val,
            },
        )

    agent = property_agent_crud.get_by_business_id(db, business_id=business.id)
    if not agent:
        raise NotFoundException("Property agent. Create agent profile first.")

    # Starter and Pro: count-based limit
    if limit is not None:
        active_count = property_agent_crud.count_active_listings(
            db, agent_id=agent.id
        )
        if active_count >= limit:
            raise HTTPException(
                status_code=403,
                detail={
                    "error":         "property_listing_limit_reached",
                    "message":       (
                        f"{tier_val.capitalize()} plan allows up to {limit} "
                        f"active property listings. You have {active_count}."
                    ),
                    "upgrade_url":   "/plans/upgrade",
                    "current_count": active_count,
                    "limit":         limit,
                    "current_tier":  tier_val,
                },
            )

    # Enterprise: unlimited — no count check needed
    return business, agent


# ─────────────────────────────────────────────
# PUBLIC — SEARCH & DISCOVERY
# ─────────────────────────────────────────────

@router.post(
    "/search",
    response_model=SuccessResponse[List[PropertyListResponse]],
    summary="Search / filter properties",
)
def search_properties(
    *,
    db: Session = Depends(get_db),
    search_params: PropertySearchFilters,
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    """
    Search properties — public endpoint.
    Location-based via PostGIS; multi-filter; subscription-tier-aware ranking.
    """
    location = None
    if search_params.location:
        location = (search_params.location.latitude, search_params.location.longitude)

    results = property_service.search_properties(
        db,
        query_text=search_params.query,
        property_type=search_params.property_type,
        property_subtype=search_params.property_subtype,
        listing_type=search_params.listing_type,
        city=search_params.city,
        state=search_params.state,
        # local_government intentionally omitted — Blueprint §2/§4 HARD RULE
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
        features=search_params.features or None,
        is_featured=search_params.is_featured,
        is_verified=search_params.is_verified,
        sort_by=search_params.sort_by,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": results}


# NOTE: Static-prefix routes (/slug/, /agents/, /my/, /saved/, /viewings/, /offers/)
# are registered BEFORE the catch-all /{property_id} so FastAPI never tries to
# validate those literal path segments as a UUID.

@router.get(
    "/slug/{slug}",
    response_model=SuccessResponse[PropertyResponse],
    summary="Get property by SEO slug",
)
def get_property_by_slug(
    *,
    db: Session = Depends(get_db),
    slug: str,
) -> dict:
    property_obj = property_crud.get_by_slug(db, slug=slug)
    if not property_obj:
        raise NotFoundException("Property")
    property_data = property_service.get_property_details(db, property_id=property_obj.id)
    return {"success": True, "data": property_data}


@router.get(
    "/{property_id}",
    response_model=SuccessResponse[PropertyResponse],
    summary="Get property details — increments view count",
)
def get_property_details(
    *,
    db: Session = Depends(get_db),
    property_id: UUID,
) -> dict:
    property_data = property_service.get_property_details(db, property_id=property_id)
    return {"success": True, "data": property_data}


# ─────────────────────────────────────────────
# PROPERTY AGENT — BUSINESS
# ─────────────────────────────────────────────

@router.post(
    "/agents",
    response_model=SuccessResponse[PropertyAgentResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create agent profile (property_agent category businesses only)",
)
def create_property_agent(
    *,
    db: Session = Depends(get_db),
    agent_in: PropertyAgentCreateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    # FIX: use sync query instead of async business_crud.get_by_user_id()
    business = _get_business_sync(db, current_user.id)
    if not business:
        raise NotFoundException("Business")
    if business.category != "property_agent":
        raise ValidationException(
            "Only property_agent category businesses can create agent profiles"
        )

    existing = property_agent_crud.get_by_business_id(db, business_id=business.id)
    if existing:
        raise ValidationException("Agent profile already exists for this business")

    data = agent_in.model_dump()
    data["business_id"] = business.id
    agent = property_agent_crud.create_from_dict(db, obj_in=data)
    return {"success": True, "data": agent}


@router.get(
    "/agents/my",
    response_model=SuccessResponse[PropertyAgentResponse],
    summary="Get my agent profile",
)
def get_my_agent_profile(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
) -> dict:
    agent = _get_verified_agent(db, current_user)
    return {"success": True, "data": agent}


@router.patch(
    "/agents/my",
    response_model=SuccessResponse[PropertyAgentResponse],
    summary="Update my agent profile",
)
def update_my_agent_profile(
    *,
    db: Session = Depends(get_db),
    agent_in: PropertyAgentUpdateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    agent = _get_verified_agent(db, current_user)
    update_data = agent_in.model_dump(exclude_unset=True)
    agent = property_agent_crud.update(db, db_obj=agent, obj_in=update_data)
    return {"success": True, "data": agent}


# ─────────────────────────────────────────────
# PROPERTY LISTINGS — BUSINESS (AGENT)
# ─────────────────────────────────────────────

@router.post(
    "/my",
    response_model=SuccessResponse[PropertyResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create property listing (Pro/Enterprise required)",
)
def create_property(
    *,
    db: Session = Depends(get_db),
    property_in: PropertyCreateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    # Blueprint §6.6: tier gate + count limit enforced BEFORE any DB write
    # Free: blocked. Starter: ≤15. Pro: ≤35. Enterprise: unlimited.
    _, agent = _check_property_listing_gate(db, current_user)

    property_data = property_in.model_dump(exclude={"location"})
    if property_in.location:
        property_data["location"] = {
            "latitude": property_in.location.latitude,
            "longitude": property_in.location.longitude,
        }

    property_obj = property_crud.create_property(
        db, agent_id=agent.id, property_data=property_data
    )
    property_dict = property_service.get_property_details(
        db, property_id=property_obj.id
    )
    return {"success": True, "data": property_dict}


@router.get(
    "/my",
    response_model=SuccessResponse[List[PropertyListResponse]],
    summary="Get my property listings",
)
def get_my_properties(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
    pagination: dict = Depends(get_pagination_params),
    listing_status: Optional[str] = Query(None, alias="status"),
) -> dict:
    agent = _get_verified_agent(db, current_user)
    properties = property_crud.get_by_agent(
        db,
        agent_id=agent.id,
        status=listing_status,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    results = [
        property_service._property_to_dict(p, agent, None) for p in properties
    ]
    return {"success": True, "data": results}


@router.patch(
    "/my/{property_id}",
    response_model=SuccessResponse[PropertyResponse],
    summary="Update property listing",
)
def update_property(
    *,
    db: Session = Depends(get_db),
    property_id: UUID,
    property_in: PropertyUpdateRequest,
    current_user: User = Depends(require_business),
) -> dict:
    agent = _get_verified_agent(db, current_user)
    property_obj = _assert_property_owned_by_agent(db, property_id, agent)

    update_data = property_in.model_dump(exclude_unset=True, exclude={"location"})
    if property_in.location:
        from geoalchemy2.elements import WKTElement
        update_data["location"] = WKTElement(
            f"POINT({property_in.location.longitude} {property_in.location.latitude})",
            srid=4326,
        )

    property_obj = property_crud.update(db, db_obj=property_obj, obj_in=update_data)
    property_dict = property_service.get_property_details(db, property_id=property_obj.id)
    return {"success": True, "data": property_dict}


@router.delete(
    "/my/{property_id}",
    response_model=SuccessResponse[dict],
    summary="Remove (deactivate) property listing",
)
def delete_property(
    *,
    db: Session = Depends(get_db),
    property_id: UUID,
    current_user: User = Depends(require_business),
) -> dict:
    agent = _get_verified_agent(db, current_user)
    property_obj = _assert_property_owned_by_agent(db, property_id, agent)
    property_obj.is_active = False
    db.commit()
    return {"success": True, "data": {"message": "Property listing deactivated"}}


# ─────────────────────────────────────────────
# SAVED PROPERTIES — CUSTOMER
# ─────────────────────────────────────────────

@router.get(
    "/saved",
    response_model=SuccessResponse[List[PropertyListResponse]],
    summary="Get saved properties",
)
def get_saved_properties(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    saved = saved_property_crud.get_by_customer(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    results = []
    for s in saved:
        prop = property_crud.get(db, id=s.property_id)
        if prop:
            results.append(property_service._property_to_dict(prop, None, None))
    return {"success": True, "data": results}


@router.post(
    "/saved/{property_id}",
    response_model=SuccessResponse[dict],
    status_code=status.HTTP_201_CREATED,
    summary="Save a property",
)
def save_property(
    *,
    db: Session = Depends(get_db),
    property_id: UUID,
    current_user: User = Depends(require_customer),
) -> dict:
    property_obj = property_crud.get(db, id=property_id)
    if not property_obj or not property_obj.is_active:
        raise NotFoundException("Property")
    saved_property_crud.save(
        db, property_id=property_id, customer_id=current_user.id
    )
    return {"success": True, "data": {"message": "Property saved"}}


@router.delete(
    "/saved/{property_id}",
    response_model=SuccessResponse[dict],
    summary="Remove saved property",
)
def unsave_property(
    *,
    db: Session = Depends(get_db),
    property_id: UUID,
    current_user: User = Depends(require_customer),
) -> dict:
    saved_property_crud.unsave(
        db, property_id=property_id, customer_id=current_user.id
    )
    return {"success": True, "data": {"message": "Property removed from saved"}}


# ─────────────────────────────────────────────
# VIEWINGS — CUSTOMER
# ─────────────────────────────────────────────

@router.post(
    "/viewings",
    response_model=SuccessResponse[ViewingResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a property viewing",
)
def schedule_viewing(
    *,
    db: Session = Depends(get_db),
    viewing_in: ViewingCreateRequest,
    current_user: User = Depends(require_customer),
) -> dict:
    property_obj = property_crud.get(db, id=viewing_in.property_id)
    if not property_obj or not property_obj.is_active:
        raise NotFoundException("Property")

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
        special_requests=viewing_in.special_requests,
    )
    return {"success": True, "data": viewing}


@router.get(
    "/viewings/my",
    response_model=SuccessResponse[List[ViewingResponse]],
    summary="Get my scheduled viewings",
)
def get_my_viewings(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    viewings = property_viewing_crud.get_customer_viewings(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": viewings}


@router.post(
    "/viewings/{viewing_id}/cancel",
    response_model=SuccessResponse[ViewingResponse],
    summary="Cancel a viewing (customer action)",
)
def cancel_viewing(
    *,
    db: Session = Depends(get_db),
    viewing_id: UUID,
    current_user: User = Depends(require_customer),
) -> dict:
    viewing = property_viewing_crud.get(db, id=viewing_id)
    if not viewing:
        raise NotFoundException("Viewing")
    if viewing.customer_id != current_user.id:
        raise PermissionDeniedException()
    if viewing.status in ["completed", "cancelled"]:
        raise ValidationException("Cannot cancel this viewing")

    viewing.status = "cancelled"
    viewing.cancelled_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(viewing)
    return {"success": True, "data": viewing}


# ─────────────────────────────────────────────
# VIEWINGS — AGENT (BUSINESS)
# ─────────────────────────────────────────────

@router.get(
    "/viewings/my/agent",
    response_model=SuccessResponse[List[ViewingResponse]],
    summary="Get all viewings across agent's properties",
)
def get_agent_viewings(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
    pagination: dict = Depends(get_pagination_params),
    viewing_date: Optional[date] = Query(None),
    viewing_status: Optional[str] = Query(None, alias="status"),
) -> dict:
    agent = _get_verified_agent(db, current_user)
    viewings = property_viewing_crud.get_agent_viewings(
        db,
        agent_id=agent.id,
        viewing_date=viewing_date,
        status=viewing_status,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": viewings}


@router.post(
    "/viewings/{viewing_id}/confirm",
    response_model=SuccessResponse[ViewingResponse],
    summary="Confirm viewing (agent action)",
)
def confirm_viewing(
    *,
    db: Session = Depends(get_db),
    viewing_id: UUID,
    current_user: User = Depends(require_business),
) -> dict:
    viewing = property_viewing_crud.get(db, id=viewing_id)
    if not viewing:
        raise NotFoundException("Viewing")
    agent = _get_verified_agent(db, current_user)
    property_obj = property_crud.get(db, id=viewing.property_id)
    if not property_obj or property_obj.agent_id != agent.id:
        raise PermissionDeniedException()

    viewing.status = "confirmed"
    viewing.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(viewing)
    return {"success": True, "data": viewing}


@router.post(
    "/viewings/{viewing_id}/complete",
    response_model=SuccessResponse[ViewingResponse],
    summary="Mark viewing as completed (agent action)",
)
def complete_viewing(
    *,
    db: Session = Depends(get_db),
    viewing_id: UUID,
    agent_notes: Optional[str] = None,
    current_user: User = Depends(require_business),
) -> dict:
    viewing = property_viewing_crud.get(db, id=viewing_id)
    if not viewing:
        raise NotFoundException("Viewing")
    agent = _get_verified_agent(db, current_user)
    property_obj = property_crud.get(db, id=viewing.property_id)
    if not property_obj or property_obj.agent_id != agent.id:
        raise PermissionDeniedException()
    if viewing.status != "confirmed":
        raise ValidationException("Can only complete confirmed viewings")

    viewing.status = "completed"
    viewing.completed_at = datetime.now(timezone.utc)
    viewing.agent_notes = agent_notes
    db.commit()
    db.refresh(viewing)
    return {"success": True, "data": viewing}


# ─────────────────────────────────────────────
# OFFERS — CUSTOMER
# ─────────────────────────────────────────────

@router.post(
    "/offers",
    response_model=SuccessResponse[OfferResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Make offer on a property",
)
def create_offer(
    *,
    db: Session = Depends(get_db),
    offer_in: OfferCreateRequest,
    current_user: User = Depends(require_customer),
) -> dict:
    offer = property_offer_crud.create_offer(
        db,
        property_id=offer_in.property_id,
        customer_id=current_user.id,
        offer_amount=offer_in.offer_amount,
        proposed_payment_plan=offer_in.proposed_payment_plan,
        proposed_lease_duration=offer_in.proposed_lease_duration,
        message=offer_in.message,
    )
    return {"success": True, "data": offer}


@router.get(
    "/offers/my",
    response_model=SuccessResponse[List[OfferResponse]],
    summary="Get own offers",
)
def get_my_offers(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_customer),
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    offers = property_offer_crud.get_customer_offers(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": offers}


@router.post(
    "/offers/{offer_id}/withdraw",
    response_model=SuccessResponse[OfferResponse],
    summary="Withdraw own offer",
)
def withdraw_offer(
    *,
    db: Session = Depends(get_db),
    offer_id: UUID,
    current_user: User = Depends(require_customer),
) -> dict:
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
    return {"success": True, "data": offer}


# ─────────────────────────────────────────────
# OFFERS — AGENT (BUSINESS)
# ─────────────────────────────────────────────

@router.get(
    "/{property_id}/offers",
    response_model=SuccessResponse[List[OfferResponse]],
    summary="Get offers on a property (agent)",
)
def get_property_offers(
    *,
    db: Session = Depends(get_db),
    property_id: UUID,
    current_user: User = Depends(require_business),
    offer_status: Optional[str] = Query(None, alias="status"),
) -> dict:
    agent = _get_verified_agent(db, current_user)
    _assert_property_owned_by_agent(db, property_id, agent)
    offers = property_offer_crud.get_property_offers(
        db, property_id=property_id, status=offer_status
    )
    return {"success": True, "data": offers}


@router.post(
    "/offers/{offer_id}/accept",
    response_model=SuccessResponse[OfferResponse],
    summary="Accept offer — rejects all others, marks property sold/rented",
)
def accept_offer(
    *,
    db: Session = Depends(get_db),
    offer_id: UUID,
    current_user: User = Depends(require_business),
) -> dict:
    agent = _get_verified_agent(db, current_user)
    offer = property_service.accept_offer(db, offer_id=offer_id, agent_id=agent.id)
    return {"success": True, "data": offer}


@router.post(
    "/offers/{offer_id}/reject",
    response_model=SuccessResponse[OfferResponse],
    summary="Reject offer (agent action)",
)
def reject_offer(
    *,
    db: Session = Depends(get_db),
    offer_id: UUID,
    body: RejectOfferRequest,
    current_user: User = Depends(require_business),
) -> dict:
    agent = _get_verified_agent(db, current_user)
    offer = property_service.reject_offer(
        db, offer_id=offer_id, agent_id=agent.id, reason=body.reason
    )
    return {"success": True, "data": offer}


@router.post(
    "/offers/{offer_id}/counter",
    response_model=SuccessResponse[OfferResponse],
    summary="Counter offer (agent action)",
)
def counter_offer(
    *,
    db: Session = Depends(get_db),
    offer_id: UUID,
    body: CounterOfferRequest,
    current_user: User = Depends(require_business),
) -> dict:
    agent = _get_verified_agent(db, current_user)
    offer = property_service.counter_offer(
        db,
        offer_id=offer_id,
        agent_id=agent.id,
        counter_amount=body.counter_amount,
        counter_message=body.counter_message,
    )
    return {"success": True, "data": offer}


# ─────────────────────────────────────────────
# INQUIRIES — CUSTOMER
# ─────────────────────────────────────────────

@router.post(
    "/{property_id}/inquire",
    response_model=SuccessResponse[dict],
    status_code=status.HTTP_201_CREATED,
    summary="Submit inquiry about a property",
)
def create_inquiry(
    *,
    db: Session = Depends(get_db),
    property_id: UUID,
    body: InquiryCreateRequest,
    current_user: User = Depends(require_customer),
) -> dict:
    inquiry = property_inquiry_crud.create_inquiry(
        db,
        property_id=property_id,
        customer_id=current_user.id,
        subject=body.subject,
        message=body.message,
        customer_name=body.customer_name,
        customer_phone=body.customer_phone,
        customer_email=body.customer_email,
    )
    return {"success": True, "data": inquiry}


# ─────────────────────────────────────────────
# INQUIRIES — AGENT (BUSINESS)
# ─────────────────────────────────────────────

@router.get(
    "/{property_id}/inquiries",
    response_model=SuccessResponse[List[dict]],
    summary="Get inquiries for a property (agent)",
)
def get_property_inquiries(
    *,
    db: Session = Depends(get_db),
    property_id: UUID,
    current_user: User = Depends(require_business),
    is_responded: Optional[bool] = Query(None),
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    agent = _get_verified_agent(db, current_user)
    _assert_property_owned_by_agent(db, property_id, agent)
    inquiries = property_inquiry_crud.get_property_inquiries(
        db,
        property_id=property_id,
        is_responded=is_responded,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": inquiries}


@router.post(
    "/inquiries/{inquiry_id}/respond",
    response_model=SuccessResponse[dict],
    summary="Respond to inquiry (agent action)",
)
def respond_to_inquiry(
    *,
    db: Session = Depends(get_db),
    inquiry_id: UUID,
    body: InquiryRespondRequest,
    current_user: User = Depends(require_business),
) -> dict:
    inquiry = property_inquiry_crud.get(db, id=inquiry_id)
    if not inquiry:
        raise NotFoundException("Inquiry")
    agent = _get_verified_agent(db, current_user)
    property_obj = property_crud.get(db, id=inquiry.property_id)
    if not property_obj or property_obj.agent_id != agent.id:
        raise PermissionDeniedException()

    inquiry.is_responded = True
    inquiry.response_message = body.response_message
    inquiry.responded_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(inquiry)
    return {"success": True, "data": inquiry}


# ─────────────────────────────────────────────
# AGENT STATS — BUSINESS
# ─────────────────────────────────────────────

@router.get(
    "/agents/my/stats",
    response_model=SuccessResponse[dict],
    summary="Agent performance statistics",
)
def get_agent_stats(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business),
) -> dict:
    agent = _get_verified_agent(db, current_user)

    from sqlalchemy import func

    active_listings = (
        db.query(func.count(Property.id))
        .filter(Property.agent_id == agent.id, Property.is_active.is_(True))
        .scalar()
    )
    total_viewings = (
        db.query(func.count(PropertyViewing.id))
        .join(Property)
        .filter(Property.agent_id == agent.id)
        .scalar()
    )
    pending_offers = (
        db.query(func.count(PropertyOffer.id))
        .join(Property)
        .filter(
            Property.agent_id == agent.id,
            PropertyOffer.status == "pending",
        )
        .scalar()
    )
    total_offers = (
        db.query(func.count(PropertyOffer.id))
        .join(Property)
        .filter(Property.agent_id == agent.id)
        .scalar()
    )
    total_views = (
        db.query(func.sum(Property.views_count))
        .filter(Property.agent_id == agent.id)
        .scalar()
        or 0
    )

    return {
        "success": True,
        "data": {
            "total_properties": agent.total_properties,
            "active_listings": active_listings,
            "properties_sold": agent.properties_sold,
            "properties_rented": agent.properties_rented,
            "total_value_transacted": float(agent.total_value_transacted),
            "total_viewings": total_viewings,
            "pending_offers": pending_offers,
            "total_offers": total_offers,
            "total_views": total_views,
        },
    }