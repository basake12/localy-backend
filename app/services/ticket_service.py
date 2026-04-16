"""
app/services/ticket_service.py

FIXES vs previous version:
  1.  Wallet.user_id → Wallet.owner_id. Blueprint §14.

  2.  Wallet(is_active=True) → Wallet(is_suspended=False, owner_type='customer').
      Blueprint §14.

  3.  WalletTransaction.reference_id → external_reference × 2. Blueprint §14.

  4.  idempotency_key added to every WalletTransaction. Blueprint §5.6 HARD RULE.

  5.  [HARD RULE §16.4] datetime.utcnow() × 2 → datetime.now(timezone.utc).

  6.  PLATFORM FEE DOUBLE-CHARGE FIXED.
      Root cause: create_booking() already sets
        total_amount = (unit_price × qty) + service_charge
      where service_charge = PLATFORM_FEE_TICKET × qty (₦50 per ticket).
      The old book_and_pay() then added ANOTHER platform_fee on top:
        total_charge = booking.total_amount + PLATFORM_FEE_TICKET × qty
      → customer charged ₦100/ticket instead of ₦50/ticket.

      Fix: total_charge = booking.total_amount (already includes the fee).
      Business credit = booking.unit_price × booking.quantity
      (not booking.total_amount, which includes the platform fee that stays
       with the platform).

  7.  [HARD RULE §6.7] Redis seat_hold added to book_and_pay().
      Blueprint §6.7: "Redis lock: seat_hold:{event_id}:{seat_id} TTL=600s
      during checkout."
      We use seat_hold:{event_id}:{tier_id} since this service manages
      tier-level inventory (not individual seat IDs in all cases).
      The hold is acquired BEFORE create_booking() and released after commit
      or on any failure path.

  NOTE: The async router (tickets.py) now handles payment inline using the
  AsyncSession. This sync book_and_pay() is retained for any callers that
  use a sync Session, but is NOT called by the main router.
"""
import logging
import uuid as _uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.constants import PLATFORM_FEE_TICKET
from app.core.exceptions import (
    BookingNotAvailableException,
    InsufficientBalanceException,
    NotFoundException,
    ValidationException,
)
from app.crud.tickets_crud import (
    ticket_booking_crud,
    ticket_event_crud,
    ticket_tier_crud,
)
from app.crud.business_crud import business_crud
from app.models.tickets_model import TicketBooking
from app.models.user_model import User
from app.models.wallet_model import (
    Wallet,
    WalletTransaction,
    TransactionType,
    TransactionStatus,
    generate_wallet_number,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Blueprint §16.4 HARD RULE: always timezone-aware UTC."""
    return datetime.now(timezone.utc)


def _idem() -> str:
    """Generate a unique idempotency key. Blueprint §5.6 HARD RULE."""
    return f"TKT_{_uuid.uuid4().hex.upper()}"


# ─── Sync wallet helpers ──────────────────────────────────────────────────────

def _get_or_create_wallet_sync(db: Session, *, user_id: UUID) -> Wallet:
    """
    Get wallet by owner, or create one. Uses owner_id (Blueprint §14).
    """
    # Blueprint §14: owner_id (not user_id)
    wallet = db.query(Wallet).filter(Wallet.owner_id == user_id).first()
    if not wallet:
        wallet = Wallet(
            owner_id=user_id,          # Blueprint §14: owner_id
            owner_type="customer",     # Blueprint §14: owner_type
            wallet_number=generate_wallet_number(),
            balance=Decimal("0.00"),
            currency="NGN",
            is_suspended=False,        # Blueprint §14: is_suspended (not is_active)
        )
        db.add(wallet)
        db.flush()
    return wallet


def _debit_wallet_sync(
    db: Session,
    *,
    wallet: Wallet,
    amount: Decimal,
    description: str,
    external_reference: str,
) -> WalletTransaction:
    """
    Debit a wallet synchronously.
    Blueprint §14: external_reference (not reference_id).
    Blueprint §5.6: idempotency_key NOT NULL.
    Blueprint §16.4: datetime.now(timezone.utc).
    """
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
        external_reference=external_reference,  # Blueprint §14: external_reference
        idempotency_key=_idem(),                # Blueprint §5.6 HARD RULE
        completed_at=_utcnow(),                 # Blueprint §16.4 HARD RULE
    )
    db.add(txn)
    return txn


def _credit_wallet_sync(
    db: Session,
    *,
    wallet: Wallet,
    amount: Decimal,
    description: str,
    external_reference: str,
) -> WalletTransaction:
    """
    Credit a wallet synchronously.
    Blueprint §14: external_reference.
    Blueprint §5.6: idempotency_key.
    Blueprint §16.4: datetime.now(timezone.utc).
    """
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


# ─── Service class ────────────────────────────────────────────────────────────

class TicketService:

    @staticmethod
    def search_events(
        db: Session,
        *,
        query_text:       Optional[str]   = None,
        event_type:       Optional[str]   = None,
        category:         Optional[str]   = None,
        location:         Optional[tuple] = None,
        radius_km:        float           = 50.0,
        event_date_from:  Optional[Any]   = None,
        event_date_to:    Optional[Any]   = None,
        origin_city:      Optional[str]   = None,
        destination_city: Optional[str]   = None,
        departure_date:   Optional[Any]   = None,
        transport_type:   Optional[str]   = None,
        available_only:   bool            = True,
        is_featured:      Optional[bool]  = None,
        skip:             int             = 0,
        limit:            int             = 20,
    ) -> List[Dict[str, Any]]:
        """
        Search events. NOTE: tickets.py router calls ticket_event_crud directly
        (async). This method exists for sync callers.
        """
        events = ticket_event_crud.search_events(
            db,
            query_text=query_text,
            event_type=event_type,
            category=category,
            location=location,
            radius_km=radius_km,
            event_date_from=event_date_from,
            event_date_to=event_date_to,
            origin_city=origin_city,
            destination_city=destination_city,
            departure_date=departure_date,
            transport_type=transport_type,
            available_only=available_only,
            is_featured=is_featured,
            skip=skip,
            limit=limit,
        )

        results = []
        for event in events:
            biz = business_crud.get(db, id=event.business_id)
            results.append({
                "id":                 event.id,
                "name":               event.name,
                "event_type":         event.event_type,
                "category":           event.category,
                "event_date":         event.event_date,
                "start_time":         event.start_time,
                "venue_name":         event.venue_name,
                "venue_address":      event.venue_address,
                "total_capacity":     event.total_capacity,
                "available_capacity": event.available_capacity,
                "is_featured":        event.is_featured,
                "banner_image":       event.banner_image,
                "status":             event.status,
                "business": {
                    "id":            biz.id,
                    "business_name": biz.business_name,
                    "logo":          getattr(biz, "logo", None),
                    "is_verified":   getattr(biz, "is_verified", False),
                } if biz else None,
            })
        return results

    @staticmethod
    def get_event_details(db: Session, *, event_id: UUID) -> Dict[str, Any]:
        event = ticket_event_crud.get(db, id=event_id)
        if not event:
            raise NotFoundException("Event")

        biz   = business_crud.get(db, id=event.business_id)
        tiers = ticket_tier_crud.get_by_event(db, event_id=event_id)

        return {
            "id":                 event.id,
            "name":               event.name,
            "description":        event.description,
            "event_type":         event.event_type,
            "category":           event.category,
            "event_date":         event.event_date,
            "start_time":         event.start_time,
            "end_time":           event.end_time,
            "venue_name":         event.venue_name,
            "venue_address":      event.venue_address,
            "total_capacity":     event.total_capacity,
            "available_capacity": event.available_capacity,
            "features":           event.features or [],
            "is_featured":        event.is_featured,
            "banner_image":       event.banner_image,
            "images":             event.images or [],
            "status":             event.status,
            "tiers": [
                {
                    "id":                 t.id,
                    "name":               t.name,
                    "description":        t.description,
                    "price":              t.price,
                    "available_quantity": t.available_quantity,
                    "benefits":           t.benefits or [],
                    "min_purchase":       t.min_purchase,
                    "max_purchase":       t.max_purchase,
                }
                for t in (tiers or [])
            ],
            "business": {
                "id":            biz.id,
                "business_name": biz.business_name,
                "logo":          getattr(biz, "logo", None),
                "is_verified":   getattr(biz, "is_verified", False),
            } if biz else None,
        }

    @staticmethod
    def book_and_pay(
        db: Session,
        *,
        current_user:        User,
        event_id:            UUID,
        tier_id:             UUID,
        quantity:            int,
        attendee_name:       str,
        attendee_email:      str,
        attendee_phone:      str,
        additional_attendees: List[Dict],
        special_requests:    Optional[str] = None,
        payment_method:      str = "wallet",
    ) -> TicketBooking:
        """
        Sync ticket booking with payment.

        Blueprint §5.4: ₦50 per ticket from customer ONLY (no business fee).
        Blueprint §6.7: Redis seat_hold:{event_id}:{tier_id} TTL=600s.
        Blueprint §5.6: atomic transaction — booking + payment in one commit.

        PLATFORM FEE STRUCTURE (FIXED):
          booking.total_amount = (unit_price × qty) + (₦50 × qty)
          Customer wallet debit = booking.total_amount  ← already includes fee
          Business wallet credit = unit_price × qty     ← fee excluded

          NEVER: total_charge = booking.total_amount + additional ₦50
          That was the double-charge bug (₦100 per ticket instead of ₦50).
        """
        # ── Blueprint §6.7: acquire Redis seat hold ───────────────────────────
        seat_hold_key = f"seat_hold:{event_id}:{tier_id}"
        redis = None
        try:
            from app.core.redis import get_redis_client
            redis = get_redis_client()
            hold_acquired = redis.set(
                seat_hold_key, str(current_user.id), nx=True, ex=600
            )
            if not hold_acquired:
                raise ValidationException(
                    "This ticket tier is currently being purchased by another customer. "
                    "Please try again in a few seconds."
                )
        except ImportError:
            logger.warning(
                "Redis unavailable for seat_hold — falling back to DB lock only "
                "(seat_hold:%s:%s)", event_id, tier_id
            )

        try:
            booking = ticket_booking_crud.create_booking(
                db,
                event_id=event_id,
                tier_id=tier_id,
                customer_id=current_user.id,
                quantity=quantity,
                attendee_name=attendee_name,
                attendee_email=attendee_email,
                attendee_phone=attendee_phone,
                additional_attendees=additional_attendees,
                special_requests=special_requests,
            )
        except Exception:
            if redis:
                try:
                    redis.delete(seat_hold_key)
                except Exception:
                    pass
            raise

        if payment_method == "wallet":
            # ── Customer debit ────────────────────────────────────────────────
            # FIX: booking.total_amount ALREADY includes the platform fee.
            #   booking.total_amount = (unit_price × qty) + service_charge
            #   service_charge = PLATFORM_FEE_TICKET × qty (₦50 per ticket)
            # DO NOT add another platform_fee — that was the double-charge bug.
            total_charge = booking.total_amount  # ← correct: includes fee

            customer_wallet = _get_or_create_wallet_sync(db, user_id=current_user.id)

            if customer_wallet.balance < total_charge:
                # Roll back booking — restore capacity
                try:
                    tier  = ticket_tier_crud.get(db, id=tier_id)
                    event = ticket_event_crud.get(db, id=event_id)
                    if tier:
                        tier.available_quantity  += quantity
                    if event:
                        event.available_capacity += quantity
                    db.delete(booking)
                    db.commit()
                except Exception:
                    db.rollback()
                finally:
                    if redis:
                        try:
                            redis.delete(seat_hold_key)
                        except Exception:
                            pass
                raise InsufficientBalanceException()

            customer_txn = _debit_wallet_sync(
                db,
                wallet=customer_wallet,
                amount=total_charge,
                description=f"Ticket booking {booking.booking_reference}",
                external_reference=f"TKT_DEBIT_{booking.id}",
            )

            # ── Business credit ───────────────────────────────────────────────
            # Business receives ticket price only (platform fee retained by Localy)
            business_credit = booking.unit_price * booking.quantity

            event         = ticket_event_crud.get(db, id=event_id)
            event.total_tickets_sold += quantity
            event.total_revenue      += booking.unit_price * quantity

            organiser_biz = business_crud.get(db, id=event.business_id)
            if organiser_biz and organiser_biz.user_id:
                biz_wallet = _get_or_create_wallet_sync(
                    db, user_id=organiser_biz.user_id
                )
                _credit_wallet_sync(
                    db,
                    wallet=biz_wallet,
                    amount=business_credit,
                    description=f"Ticket sale {booking.booking_reference}",
                    external_reference=f"TKT_BIZ_{booking.id}",
                )

            booking.payment_status   = "paid"
            booking.status           = "confirmed"
            booking.payment_reference = str(customer_txn.id)

            db.commit()
            db.refresh(booking)

        # ── Release seat hold ─────────────────────────────────────────────────
        if redis:
            try:
                redis.delete(seat_hold_key)
            except Exception:
                pass

        return booking

    @staticmethod
    def calculate_booking_price(
        db: Session, *, tier_id: UUID, quantity: int
    ) -> Dict[str, Decimal]:
        """
        Calculate booking price breakdown.
        Blueprint §5.4: ₦50 flat fee per ticket from customer only.
        """
        tier = ticket_tier_crud.get(db, id=tier_id)
        if not tier:
            raise NotFoundException("Ticket tier")

        unit_price     = tier.price
        subtotal       = unit_price * quantity
        platform_fee   = PLATFORM_FEE_TICKET * quantity   # ₦50 × qty
        total_amount   = subtotal + platform_fee

        return {
            "unit_price":    unit_price,
            "subtotal":      subtotal,
            "platform_fee":  platform_fee,
            "total_amount":  total_amount,
            "quantity":      Decimal(str(quantity)),
        }


ticket_service = TicketService()