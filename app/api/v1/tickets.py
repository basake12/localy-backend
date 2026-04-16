"""
app/api/v1/tickets.py

FIXES vs previous version:
  1.  [CRITICAL] purchase_tickets() now processes wallet payment.
      The previous version called ticket_booking_crud.create_booking() and
      returned without debiting the customer wallet. Every ticket was free.

      Fix: After create_booking() (which uses flush, not commit), the router
      acquires the customer wallet via AsyncSession, debits total_amount,
      credits the business wallet, then commits — all in one transaction.
      If payment fails, db.rollback() restores everything.
      Blueprint §5.6: "All financial operations are wrapped in PostgreSQL
      transactions."

  2.  [HARD RULE §6.7] Redis seat_hold:{event_id}:{tier_id} TTL=600s
      acquired BEFORE create_booking(). Blueprint §6.7: "seat hold activates
      during checkout to prevent double-booking."

  3.  Blueprint §5.4 fee structure:
      Customer pays: total_amount (already includes ₦50 × qty platform fee)
      Business receives: unit_price × qty (fee excluded)
      Platform earns: ₦50 × qty (from customer side only for tickets)

  4.  All WalletTransaction fields use Blueprint §14 names:
      owner_id (not user_id), external_reference (not reference_id),
      idempotency_key NOT NULL (§5.6), datetime.now(timezone.utc) (§16.4).

  5.  Wallet constructor: owner_id, owner_type, is_suspended (not is_active).
"""
import logging
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)
from app.crud.business_crud import business_crud
from app.crud.tickets_crud import (
    ticket_booking_crud,
    ticket_event_crud,
    ticket_tier_crud,
)
from app.dependencies import (
    get_current_active_user,
    get_pagination_params,
    require_business,
    require_customer,
)
from app.models.tickets_model import EventCategoryEnum, TicketBooking, TicketEvent
from app.models.user_model import User
from app.models.wallet_model import (
    Wallet,
    WalletTransaction,
    TransactionType,
    TransactionStatus,
    generate_wallet_number,
)
from app.schemas.common_schema import SuccessResponse
from app.schemas.tickets_schema import (
    CheckInRequest,
    TicketBookingCreateRequest,
    TicketBookingListResponse,
    TicketBookingResponse,
    TicketEventCreateRequest,
    TicketEventListResponse,
    TicketEventResponse,
    TicketEventSearchFilters,
    TicketEventUpdateRequest,
    TicketTierCreateRequest,
    TicketTierResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Async wallet helpers ─────────────────────────────────────────────────────

def _idem() -> str:
    """Unique idempotency key. Blueprint §5.6 HARD RULE."""
    return f"TKT_{_uuid.uuid4().hex.upper()}"


def _utcnow() -> datetime:
    """Blueprint §16.4 HARD RULE."""
    return datetime.now(timezone.utc)


async def _get_or_create_wallet_async(
    db: AsyncSession, *, user_id: UUID, owner_type: str = "customer"
) -> Wallet:
    """
    Get wallet by owner_id or create one.
    Blueprint §14: owner_id (not user_id), owner_type, is_suspended.
    """
    result = await db.execute(
        select(Wallet).where(Wallet.owner_id == user_id)
    )
    wallet = result.scalar_one_or_none()
    if not wallet:
        wallet = Wallet(
            owner_id=user_id,              # Blueprint §14: owner_id
            owner_type=owner_type,         # Blueprint §14: owner_type
            wallet_number=generate_wallet_number(),
            balance=Decimal("0.00"),
            currency="NGN",
            is_suspended=False,            # Blueprint §14: is_suspended
        )
        db.add(wallet)
        await db.flush()
    return wallet


async def _debit_wallet_async(
    db: AsyncSession,
    *,
    wallet: Wallet,
    amount: Decimal,
    description: str,
    external_reference: str,
) -> WalletTransaction:
    """
    Debit wallet async. Blueprint §14 + §5.6 + §16.4.
    """
    from app.core.exceptions import InsufficientBalanceException

    if wallet.balance < amount:
        raise InsufficientBalanceException()

    balance_before  = wallet.balance
    wallet.balance -= amount

    txn = WalletTransaction(
        wallet_id=wallet.id,
        transaction_type=TransactionType.PAYMENT,
        amount=amount,
        balance_before=balance_before,
        balance_after=wallet.balance,
        status=TransactionStatus.COMPLETED,
        description=description,
        external_reference=external_reference,  # Blueprint §14
        idempotency_key=_idem(),                # Blueprint §5.6 HARD RULE
        completed_at=_utcnow(),                 # Blueprint §16.4 HARD RULE
    )
    db.add(txn)
    return txn


async def _credit_wallet_async(
    db: AsyncSession,
    *,
    wallet: Wallet,
    amount: Decimal,
    description: str,
    external_reference: str,
) -> WalletTransaction:
    """Credit wallet async. Blueprint §14 + §5.6 + §16.4."""
    balance_before  = wallet.balance
    wallet.balance += amount

    txn = WalletTransaction(
        wallet_id=wallet.id,
        transaction_type=TransactionType.CREDIT,
        amount=amount,
        balance_before=balance_before,
        balance_after=wallet.balance,
        status=TransactionStatus.COMPLETED,
        description=description,
        external_reference=external_reference,  # Blueprint §14
        idempotency_key=_idem(),                # Blueprint §5.6 HARD RULE
        completed_at=_utcnow(),                 # Blueprint §16.4 HARD RULE
    )
    db.add(txn)
    return txn


# ─── SEARCH & DISCOVERY (public) ─────────────────────────────────────────────

@router.post("/search", response_model=SuccessResponse[List[TicketEventListResponse]])
async def search_events(
    *,
    db:            AsyncSession = Depends(get_async_db),
    search_params: TicketEventSearchFilters,
    pagination:    dict         = Depends(get_pagination_params),
) -> dict:
    """
    Search events and transport tickets.
    Blueprint §4: radius-based, no LGA filtering.
    """
    location = None
    if search_params.location:
        location = (
            search_params.location.latitude,
            search_params.location.longitude,
        )

    results = await ticket_event_crud.search_events(
        db,
        query_text=search_params.query,
        event_type=search_params.event_type,
        category=search_params.category,
        # lga_name intentionally omitted — Blueprint §4 HARD RULE
        location=location,
        radius_km=search_params.radius_km or 50.0,
        event_date_from=search_params.event_date_from,
        event_date_to=search_params.event_date_to,
        origin_city=search_params.origin_city,
        destination_city=search_params.destination_city,
        departure_date=search_params.departure_date,
        transport_type=search_params.transport_type,
        available_only=search_params.available_only,
        is_featured=search_params.is_featured,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": results}


# NOTE: Static-prefix routes (GET /bookings/my, GET /my-events, etc.) MUST be
# registered BEFORE the wildcard GET /{event_id} so FastAPI never treats a
# literal string like "bookings" as a UUID and returns 422.


# ─── MY BOOKINGS (static path — before /{event_id} wildcard) ─────────────────

@router.get(
    "/bookings/my",
    response_model=SuccessResponse[List[TicketBookingListResponse]],
)
async def get_my_bookings(
    *,
    db:           AsyncSession = Depends(get_async_db),
    current_user: User         = Depends(require_customer),
    pagination:   dict         = Depends(get_pagination_params),
) -> dict:
    """Get current customer's ticket bookings."""
    bookings = await ticket_booking_crud.get_customer_bookings(
        db,
        customer_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": bookings}


# ─── MY EVENTS (static path — before /{event_id} wildcard) ───────────────────

@router.get(
    "/my-events",
    response_model=SuccessResponse[List[TicketEventListResponse]],
)
async def get_my_events(
    *,
    db:           AsyncSession = Depends(get_async_db),
    current_user: User         = Depends(require_business),
    pagination:   dict         = Depends(get_pagination_params),
) -> dict:
    """Get all events owned by the authenticated business."""
    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business profile")

    events = await ticket_event_crud.get_by_business_id(
        db,
        business_id=business.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": events}


# ─── TICKET PURCHASING (customer) ────────────────────────────────────────────

@router.post(
    "/bookings",
    response_model=SuccessResponse[TicketBookingResponse],
    status_code=status.HTTP_201_CREATED,
)
async def purchase_tickets(
    *,
    db:           AsyncSession             = Depends(get_async_db),
    booking_data: TicketBookingCreateRequest,
    current_user: User                     = Depends(require_customer),
) -> dict:
    """
    Purchase tickets for an event.

    Blueprint §6.7: Redis seat_hold:{event_id}:{tier_id} TTL=600s.
    Blueprint §5.4: ₦50 flat fee per ticket from customer only.
    Blueprint §5.6: booking + payment in a single atomic transaction.

    Flow:
      1. Acquire Redis seat hold (prevents double-booking during checkout window)
      2. Create booking via CRUD (flush only — no commit yet)
      3. Debit customer wallet
      4. Credit business wallet (ticket price only, fee retained by platform)
      5. Commit
      6. Release seat hold
      If any step fails → rollback → seat hold released → inventory restored.
    """
    from app.core.exceptions import InsufficientBalanceException

    event_id  = booking_data.event_id
    tier_id   = booking_data.tier_id
    quantity  = booking_data.quantity

    # ── Step 1: Redis seat hold (Blueprint §6.7) ──────────────────────────────
    seat_hold_key = f"seat_hold:{event_id}:{tier_id}"
    redis = None
    try:
        from app.core.redis import get_async_redis_client
        redis = await get_async_redis_client()
        hold = await redis.set(
            seat_hold_key, str(current_user.id), nx=True, ex=600
        )
        if not hold:
            raise ValidationException(
                "This ticket tier is currently being reserved by another customer. "
                "Please try again in a few seconds."
            )
    except ImportError:
        logger.warning(
            "Redis unavailable — seat_hold:%s:%s skipped, DB lock only",
            event_id, tier_id,
        )

    async def _release_hold():
        if redis:
            try:
                await redis.delete(seat_hold_key)
            except Exception:
                pass

    try:
        # ── Step 2: Create booking (flush only, not commit) ───────────────────
        booking = await ticket_booking_crud.create_booking(
            db,
            event_id=event_id,
            tier_id=tier_id,
            customer_id=current_user.id,
            quantity=quantity,
            attendee_name=booking_data.attendee_name,
            attendee_email=booking_data.attendee_email,
            attendee_phone=booking_data.attendee_phone,
            additional_attendees=[a.model_dump() for a in booking_data.additional_attendees],
            special_requests=booking_data.special_requests,
        )

        # ── Step 3: Debit customer wallet ─────────────────────────────────────
        # Blueprint §5.4: customer pays total_amount which already includes
        # service_charge (= PLATFORM_FEE_TICKET × qty). Do NOT add more.
        customer_wallet = await _get_or_create_wallet_async(
            db, user_id=current_user.id, owner_type="customer"
        )

        if customer_wallet.balance < booking.total_amount:
            await db.rollback()
            await _release_hold()
            raise InsufficientBalanceException()

        customer_txn = await _debit_wallet_async(
            db,
            wallet=customer_wallet,
            amount=booking.total_amount,
            description=f"Ticket booking {booking.booking_reference}",
            external_reference=f"TKT_DEBIT_{booking.id}",
        )

        # ── Step 4: Credit business wallet ───────────────────────────────────
        # Business receives unit_price × qty (platform fee excluded).
        # Blueprint §5.4: ₦50 from customer only — business does not pay a fee.
        business_credit = booking.unit_price * booking.quantity

        event = await ticket_event_crud.get(db, id=event_id)
        if event:
            event.total_tickets_sold += quantity
            event.total_revenue      += business_credit

            organiser_biz = await business_crud.get(db, id=event.business_id)
            if organiser_biz and organiser_biz.user_id:
                biz_wallet = await _get_or_create_wallet_async(
                    db, user_id=organiser_biz.user_id, owner_type="business"
                )
                await _credit_wallet_async(
                    db,
                    wallet=biz_wallet,
                    amount=business_credit,
                    description=f"Ticket sale {booking.booking_reference}",
                    external_reference=f"TKT_BIZ_{booking.id}",
                )

        # ── Step 5: Confirm booking ───────────────────────────────────────────
        booking.payment_status    = "paid"
        booking.status            = "confirmed"
        booking.payment_reference = str(customer_txn.id)

        await db.commit()
        await db.refresh(booking)

    except (InsufficientBalanceException, ValidationException, NotFoundException):
        await db.rollback()
        await _release_hold()
        raise
    except Exception as exc:
        logger.error(
            "Ticket purchase failed for user %s event %s: %s",
            current_user.id, event_id, exc, exc_info=True,
        )
        await db.rollback()
        await _release_hold()
        raise ValidationException(
            "Ticket purchase failed — please try again."
        ) from exc

    # ── Step 6: Release seat hold on success ──────────────────────────────────
    await _release_hold()

    return {"success": True, "data": booking}


@router.post(
    "/bookings/{booking_id}/cancel",
    response_model=SuccessResponse[TicketBookingResponse],
)
async def cancel_booking(
    *,
    db:           AsyncSession  = Depends(get_async_db),
    booking_id:   UUID,
    reason:       Optional[str] = Query(None, max_length=500),
    current_user: User          = Depends(require_customer),
) -> dict:
    """
    Cancel a ticket booking.
    24-hour cancellation window enforced before event start.
    Capacity restored on cancellation.
    Blueprint §5.1: refund credited to customer wallet.
    """
    booking = await ticket_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")

    if booking.customer_id != current_user.id:
        raise PermissionDeniedException()

    event = await ticket_event_crud.get(db, id=booking.event_id)
    event_date = event.event_date or event.departure_date
    if event_date:
        event_dt = datetime.combine(
            event_date, datetime.min.time()
        ).replace(tzinfo=timezone.utc)
        if _utcnow() > event_dt - timedelta(hours=24):
            raise ValidationException(
                "Cannot cancel within 24 hours of the event"
            )

    # Refund to customer wallet
    if booking.payment_status == "paid":
        refund_amount   = booking.total_amount
        customer_wallet = await _get_or_create_wallet_async(
            db, user_id=current_user.id, owner_type="customer"
        )
        await _credit_wallet_async(
            db,
            wallet=customer_wallet,
            amount=refund_amount,
            description=f"Refund: ticket booking {booking.booking_reference}",
            external_reference=f"TKT_REFUND_{booking.id}",
        )

    booking = await ticket_booking_crud.cancel_booking(
        db, booking_id=booking_id, reason=reason
    )
    return {"success": True, "data": booking}


# ─── CHECK-IN (business / door staff) ────────────────────────────────────────

@router.post("/checkin", response_model=SuccessResponse[TicketBookingResponse])
async def check_in_ticket(
    *,
    db:           AsyncSession  = Depends(get_async_db),
    checkin_data: CheckInRequest,
    current_user: User          = Depends(require_business),
) -> dict:
    """Check in a ticket by QR booking reference."""
    booking  = await ticket_booking_crud.get_by_reference(
        db, booking_reference=checkin_data.booking_reference
    )
    if not booking:
        raise NotFoundException("Booking")

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    event    = await ticket_event_crud.get(db, id=booking.event_id)
    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    booking = await ticket_booking_crud.check_in_ticket(
        db, booking_reference=checkin_data.booking_reference
    )
    return {"success": True, "data": booking}


# ─── EVENT MANAGEMENT (business) ─────────────────────────────────────────────

@router.post(
    "/events",
    response_model=SuccessResponse[TicketEventResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_event(
    *,
    db:           AsyncSession             = Depends(get_async_db),
    event_data:   TicketEventCreateRequest,
    current_user: User                     = Depends(require_business),
) -> dict:
    """Create a new event or transport schedule."""
    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business:
        raise NotFoundException("Business profile")

    event_dict = event_data.model_dump(
        exclude={"venue_location", "origin_location", "destination_location"}
    )
    event_dict["business_id"]        = business.id
    event_dict["available_capacity"] = event_data.total_capacity

    event = await ticket_event_crud.create(db, obj_in=event_dict)
    return {"success": True, "data": event}


@router.patch(
    "/events/{event_id}",
    response_model=SuccessResponse[TicketEventResponse],
)
async def update_event(
    *,
    db:           AsyncSession              = Depends(get_async_db),
    event_id:     UUID,
    update_data:  TicketEventUpdateRequest,
    current_user: User                      = Depends(require_business),
) -> dict:
    """Update event details."""
    event = await ticket_event_crud.get(db, id=event_id)
    if not event:
        raise NotFoundException("Event")

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    event = await ticket_event_crud.update(
        db, db_obj=event, obj_in=update_data.model_dump(exclude_none=True)
    )
    return {"success": True, "data": event}


@router.post(
    "/events/{event_id}/tiers",
    response_model=SuccessResponse[TicketTierResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_tier(
    *,
    db:           AsyncSession            = Depends(get_async_db),
    event_id:     UUID,
    tier_data:    TicketTierCreateRequest,
    current_user: User                    = Depends(require_business),
) -> dict:
    """Add a ticket tier to an event."""
    event = await ticket_event_crud.get(db, id=event_id)
    if not event:
        raise NotFoundException("Event")

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    tier_dict = tier_data.model_dump()
    tier_dict["event_id"]          = event_id
    tier_dict["available_quantity"] = tier_data.total_quantity

    tier = await ticket_tier_crud.create(db, obj_in=tier_dict)
    return {"success": True, "data": tier}


@router.get(
    "/events/{event_id}/bookings",
    response_model=SuccessResponse[List[TicketBookingResponse]],
)
async def get_event_bookings(
    *,
    db:           AsyncSession  = Depends(get_async_db),
    event_id:     UUID,
    current_user: User          = Depends(require_business),
    pagination:   dict          = Depends(get_pagination_params),
    status:       Optional[str] = Query(None),
) -> dict:
    """Get all bookings for a specific event (business only)."""
    event = await ticket_event_crud.get(db, id=event_id)
    if not event:
        raise NotFoundException("Event")

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    bookings = await ticket_booking_crud.get_event_bookings(
        db,
        event_id=event_id,
        status=status,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": bookings}


@router.get(
    "/events/{event_id}/stats",
    response_model=SuccessResponse[dict],
)
async def get_event_stats(
    *,
    db:           AsyncSession = Depends(get_async_db),
    event_id:     UUID,
    current_user: User         = Depends(require_business),
) -> dict:
    """Get live sales stats for a specific event."""
    from sqlalchemy import func

    event = await ticket_event_crud.get(db, id=event_id)
    if not event:
        raise NotFoundException("Event")

    business = await business_crud.get_by_user_id(db, user_id=current_user.id)
    if not business or event.business_id != business.id:
        raise PermissionDeniedException()

    total = await db.scalar(
        select(func.count(TicketBooking.id))
        .where(TicketBooking.event_id == event_id)
    )
    confirmed = await db.scalar(
        select(func.count(TicketBooking.id))
        .where(
            TicketBooking.event_id == event_id,
            TicketBooking.status   == "confirmed",
        )
    )
    checked_in = await db.scalar(
        select(func.count(TicketBooking.id))
        .where(
            TicketBooking.event_id == event_id,
            TicketBooking.status   == "checked_in",
        )
    )

    return {
        "success": True,
        "data": {
            "total_capacity":     event.total_capacity,
            "available_capacity": event.available_capacity,
            "tickets_sold":       event.total_tickets_sold,
            "total_revenue":      float(event.total_revenue),
            "total_bookings":     total,
            "confirmed_bookings": confirmed,
            "checked_in_count":   checked_in,
            "average_rating":     float(event.average_rating),
        },
    }


# ─── EVENT DETAIL (wildcard /{event_id} — MUST be last) ──────────────────────

@router.get("/{event_id}", response_model=SuccessResponse[TicketEventResponse])
async def get_event_details(
    *,
    db:       AsyncSession = Depends(get_async_db),
    event_id: UUID,
) -> dict:
    """Get full event details including tiers. Public endpoint."""
    event = await ticket_event_crud.get(db, id=event_id)
    if not event or not event.is_active:
        raise NotFoundException("Event")
    return {"success": True, "data": event}


@router.get(
    "/{event_id}/tiers",
    response_model=SuccessResponse[List[TicketTierResponse]],
)
async def get_event_tiers(
    *,
    db:       AsyncSession = Depends(get_async_db),
    event_id: UUID,
) -> dict:
    """Get active ticket tiers for an event. Public."""
    tiers = await ticket_tier_crud.get_by_event(db, event_id=event_id)
    return {"success": True, "data": tiers}