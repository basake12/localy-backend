from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import date, datetime
from decimal import Decimal

from app.crud.tickets_crud import (
    ticket_event_crud,
    ticket_tier_crud,
    ticket_booking_crud
)
from app.crud.business_crud import business_crud
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    InsufficientBalanceException,
    BookingNotAvailableException
)
from app.core.constants import (
    TransactionType,
    # FIX: Use the blueprint-defined flat fee per ticket (₦50), not a percentage.
    # TICKET_SERVICE_CHARGE_RATE was Decimal("0.00") in constants — confirming
    # the intent was never a percentage. The old hardcoded Decimal('0.05') in
    # calculate_booking_price / book_and_pay was wrong.
    PLATFORM_FEE_TICKET,  # ₦50 per ticket (blueprint §4.4)
)
from app.models.user_model import User
from app.models.tickets_model import TicketBooking
# FIX: Import wallet models directly — wallet_crud is fully async (AsyncSession).
# Calling async crud methods without await in a sync service returns coroutine
# objects that are never executed: wallets appear unchanged while the booking
# proceeds as if payment happened.
from app.models.wallet_model import (
    Wallet,
    WalletTransaction,
    TransactionTypeEnum,
    TransactionStatusEnum,
    generate_wallet_number,
)


# ──────────────────────────────────────────────────────────────────────────────
# Sync wallet helpers (same pattern as health_service / product_service)
# ──────────────────────────────────────────────────────────────────────────────

def _get_or_create_wallet_sync(db: Session, *, user_id: UUID) -> Wallet:
    wallet = db.query(Wallet).filter(Wallet.user_id == user_id).first()
    if not wallet:
        wallet = Wallet(
            user_id=user_id,
            wallet_number=generate_wallet_number(),
            balance=Decimal("0.00"),
            currency="NGN",
            is_active=True,
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
    reference_id: str,
) -> WalletTransaction:
    if wallet.balance < amount:
        raise InsufficientBalanceException()
    balance_before = wallet.balance
    wallet.balance -= amount
    txn = WalletTransaction(
        wallet_id=wallet.id,
        transaction_type=TransactionTypeEnum.PAYMENT,
        amount=amount,
        balance_before=balance_before,
        balance_after=wallet.balance,
        status=TransactionStatusEnum.COMPLETED,
        description=description,
        reference_id=reference_id,
        completed_at=datetime.utcnow(),
    )
    db.add(txn)
    return txn


def _credit_wallet_sync(
    db: Session,
    *,
    wallet: Wallet,
    amount: Decimal,
    transaction_type: TransactionTypeEnum,
    description: str,
    reference_id: str,
) -> WalletTransaction:
    balance_before = wallet.balance
    wallet.balance += amount
    txn = WalletTransaction(
        wallet_id=wallet.id,
        transaction_type=transaction_type,
        amount=amount,
        balance_before=balance_before,
        balance_after=wallet.balance,
        status=TransactionStatusEnum.COMPLETED,
        description=description,
        reference_id=reference_id,
        completed_at=datetime.utcnow(),
    )
    db.add(txn)
    return txn


class TicketService:
    """Business logic for ticket operations"""

    @staticmethod
    def search_events(
            db: Session,
            *,
            query_text: Optional[str] = None,
            event_type: Optional[str] = None,
            category: Optional[str] = None,
            location: Optional[tuple] = None,
            radius_km: float = 50.0,
            event_date_from: Optional[date] = None,
            event_date_to: Optional[date] = None,
            origin_city: Optional[str] = None,
            destination_city: Optional[str] = None,
            departure_date: Optional[date] = None,
            transport_type: Optional[str] = None,
            available_only: bool = True,
            is_featured: Optional[bool] = None,
            skip: int = 0,
            limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Search events with business info.

        NOTE: tickets.py router calls ticket_event_crud.search_events() directly
        (async) and does NOT go through this method. This method exists for
        compatibility with any non-async callers.
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
            limit=limit
        )

        results = []
        for event in events:
            biz = business_crud.get(db, id=event.business_id)
            # FIX: Return plain dicts — raw ORM objects with Geography columns
            # cause WKBElement serialization errors in jsonable_encoder.
            results.append({
                "id": event.id,
                "name": event.name,
                "event_type": event.event_type,
                "category": event.category,
                "event_date": event.event_date,
                "start_time": event.start_time,
                "venue_name": event.venue_name,
                "venue_address": event.venue_address,
                "total_capacity": event.total_capacity,
                "available_capacity": event.available_capacity,
                "is_featured": event.is_featured,
                "banner_image": event.banner_image,
                "status": event.status,
                "business": {
                    "id": biz.id,
                    "business_name": biz.business_name,
                    "logo": getattr(biz, "logo", None),
                    "is_verified": getattr(biz, "is_verified", False),
                } if biz else None,
            })

        return results

    @staticmethod
    def get_event_details(
            db: Session,
            *,
            event_id: UUID
    ) -> Dict[str, Any]:
        """Get full event details with ticket tiers."""
        event = ticket_event_crud.get(db, id=event_id)
        if not event:
            raise NotFoundException("Event")

        biz = business_crud.get(db, id=event.business_id)
        tiers = ticket_tier_crud.get_by_event(db, event_id=event_id)

        # FIX: Return plain dicts — raw ORM objects crash jsonable_encoder.
        return {
            "id": event.id,
            "name": event.name,
            "description": event.description,
            "event_type": event.event_type,
            "category": event.category,
            "event_date": event.event_date,
            "start_time": event.start_time,
            "end_time": event.end_time,
            "venue_name": event.venue_name,
            "venue_address": event.venue_address,
            "total_capacity": event.total_capacity,
            "available_capacity": event.available_capacity,
            "features": event.features or [],
            "is_featured": event.is_featured,
            "banner_image": event.banner_image,
            "images": event.images or [],
            "status": event.status,
            "tiers": [
                {
                    "id": t.id,
                    "name": t.name,
                    "description": t.description,
                    "price": t.price,
                    "available_quantity": t.available_quantity,
                    "benefits": t.benefits or [],
                    "min_purchase": t.min_purchase,
                    "max_purchase": t.max_purchase,
                }
                for t in (tiers or [])
            ],
            "business": {
                "id": biz.id,
                "business_name": biz.business_name,
                "logo": getattr(biz, "logo", None),
                "is_verified": getattr(biz, "is_verified", False),
            } if biz else None,
        }

    @staticmethod
    def book_and_pay(
            db: Session,
            *,
            current_user: User,
            event_id: UUID,
            tier_id: UUID,
            quantity: int,
            attendee_name: str,
            attendee_email: str,
            attendee_phone: str,
            additional_attendees: List[Dict],
            special_requests: Optional[str] = None,
            payment_method: str = "wallet"
    ) -> TicketBooking:
        """
        Create ticket booking and process payment.

        Blueprint §4.4 — ₦50 flat fee PER TICKET (not a percentage).
        Platform fee is charged in addition to the ticket price and is
        non-refundable.

        FIX: Replaced 5% service_charge (Decimal('0.05') × subtotal) with
        ₦50 × quantity flat fee per blueprint.

        FIX: Business wallet is now credited after customer debit.

        NOTE: tickets.py router calls ticket_booking_crud.create_booking()
        directly for the async flow. This method is the sync-compatible
        alternative for callers that use a sync Session.
        """
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
            special_requests=special_requests
        )

        if payment_method == "wallet":
            # FIX: ₦50 flat fee per ticket, not 5% of subtotal.
            platform_fee = PLATFORM_FEE_TICKET * quantity
            total_charge = booking.total_amount + platform_fee

            # FIX: Sync wallet operations — no await needed.
            customer_wallet = _get_or_create_wallet_sync(db, user_id=current_user.id)
            if customer_wallet.balance < total_charge:
                # Restore capacity before raising
                tier = ticket_tier_crud.get(db, id=tier_id)
                event = ticket_event_crud.get(db, id=event_id)
                if tier:
                    tier.available_quantity += quantity
                if event:
                    event.available_capacity += quantity
                db.delete(booking)
                db.commit()
                raise InsufficientBalanceException()

            customer_txn = _debit_wallet_sync(
                db,
                wallet=customer_wallet,
                amount=total_charge,
                description=f"Ticket booking {booking.booking_reference}",
                reference_id=str(booking.id),
            )

            booking.payment_status = "paid"
            booking.status = "confirmed"
            # FIX: Store wallet transaction ID as reference, not booking's own ID.
            booking.payment_reference = str(customer_txn.id)

            # FIX: Credit the event organiser's business wallet.
            event = ticket_event_crud.get(db, id=event_id)
            event.total_tickets_sold += quantity
            event.total_revenue += booking.total_amount

            organiser_biz = business_crud.get(db, id=event.business_id)
            if organiser_biz and organiser_biz.user_id:
                biz_wallet = _get_or_create_wallet_sync(
                    db, user_id=organiser_biz.user_id
                )
                _credit_wallet_sync(
                    db,
                    wallet=biz_wallet,
                    amount=booking.total_amount,  # net: customer paid + platform fee, business gets subtotal
                    transaction_type=TransactionTypeEnum.CREDIT,
                    description=f"Ticket sale {booking.booking_reference}",
                    reference_id=f"biz_{booking.id}",
                )

            db.commit()
            db.refresh(booking)

        return booking

    @staticmethod
    def calculate_booking_price(
            db: Session,
            *,
            tier_id: UUID,
            quantity: int
    ) -> Dict[str, Decimal]:
        """
        Calculate booking price with breakdown.

        FIX: Blueprint §4.4 — ₦50 flat fee per ticket.
        Replaced 5% percentage service_charge with PLATFORM_FEE_TICKET × quantity.
        """
        tier = ticket_tier_crud.get(db, id=tier_id)
        if not tier:
            raise NotFoundException("Ticket tier")

        unit_price = tier.price
        subtotal = unit_price * quantity
        # FIX: ₦50 per ticket flat fee (blueprint §4.4 §11.7) — not 5% of subtotal.
        platform_fee = PLATFORM_FEE_TICKET * quantity
        total_amount = subtotal + platform_fee

        return {
            "unit_price": unit_price,
            "subtotal": subtotal,
            "platform_fee": platform_fee,   # renamed from service_charge
            "total_amount": total_amount,
            "quantity": quantity,
        }


ticket_service = TicketService()