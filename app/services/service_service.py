# app/services/service_service.py

import uuid as _uuid
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import inspect as sa_inspect
from uuid import UUID
from datetime import date, time, datetime, timezone, timedelta
from decimal import Decimal

from app.crud.services_crud import (
    service_provider_crud,
    service_crud,
    service_availability_crud,
    service_booking_crud,
)
# FIX: wallet_crud is fully async (AsyncSession). Calling its methods without
# await in this sync service returns coroutine objects that are never executed —
# wallets appear unchanged while bookings proceed as if payment happened.
# Replaced with inline sync wallet ops (same pattern as health_service.py).
from app.models.wallet_model import (
    Wallet,
    WalletTransaction,
    TransactionStatus,   # Blueprint §14: correct name
    generate_wallet_number,
)
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    InsufficientBalanceException,
    BookingNotAvailableException,
)
from app.core.constants import (
    TransactionType,
    PLATFORM_FEE_BOOKING,  # ₦100 for service bookings (Blueprint Section 4.4)
)
from app.models.user_model import User
from app.models.services_model import ServiceBooking, BookingStatusEnum, PaymentStatusEnum



# ──────────────────────────────────────────────────────────────────────────────
# ORM serialization helper
#
# jsonable_encoder fails on GeoAlchemy2 WKBElement values stored in Geography
# columns (Business.location, ServiceProvider.provider_location). It calls
# vars() on the geometry object, which raises:
#     TypeError: vars() argument must have __dict__ attribute
# then falls back to treating it as an iterable, raising:
#     ValueError: dictionary update sequence element #0 has length 1; 2 is required
#
# Fix: use SQLAlchemy column inspection to iterate ONLY mapped scalar columns,
# skip Geography/Geometry types entirely, and coerce known non-JSON types
# (UUID, Decimal, datetime, date, time, Enum) to JSON-safe primitives.
# ──────────────────────────────────────────────────────────────────────────────

_SKIP_COLUMN_TYPES = {"Geography", "Geometry"}


def _orm_to_dict(obj) -> "Optional[Dict[str, Any]]":
    """
    Convert a SQLAlchemy ORM instance to a plain JSON-serializable dict.

    Skips Geography/Geometry columns to avoid WKBElement serialization errors.
    Safe to call on None -- returns None.
    """
    if obj is None:
        return None

    mapper = sa_inspect(type(obj))
    result = {}

    for col_attr in mapper.column_attrs:
        col = col_attr.columns[0]
        # Skip spatial columns -- GeoAlchemy2 types are not JSON-serializable
        if type(col.type).__name__ in _SKIP_COLUMN_TYPES:
            continue
        val = getattr(obj, col_attr.key, None)
        if isinstance(val, UUID):
            val = str(val)
        elif isinstance(val, Decimal):
            val = float(val)
        elif isinstance(val, (datetime, date, time)):
            val = val.isoformat()
        elif hasattr(val, "value") and not isinstance(val, (str, int, float, bool)):
            # SQLAlchemy Enum / Python enum -- use the string value
            val = val.value
        result[col_attr.key] = val

    return result

# ──────────────────────────────────────────────────────────────────────────────
# Sync wallet helpers
# wallet_crud methods are all async — unusable in a sync service without await.
# These replicate the same logic inline, following the pattern in
# health_service.py and product_service.py.
# ──────────────────────────────────────────────────────────────────────────────

def _get_or_create_wallet_sync(db: Session, *, user_id: UUID) -> Wallet:
    wallet = db.query(Wallet).filter(Wallet.owner_id == user_id).first()  # Blueprint §14
    if not wallet:
        wallet = Wallet(
            owner_id=user_id,          # Blueprint §14: owner_id
            owner_type="customer",     # Blueprint §14: owner_type
            wallet_number=generate_wallet_number(),
            balance=Decimal("0.00"),
            currency="NGN",
            is_suspended=False,        # Blueprint §14: is_suspended
        )
        db.add(wallet)
        db.flush()
    return wallet


def _debit_wallet_sync(
    db: Session,
    *,
    wallet: Wallet,
    amount: Decimal,
    transaction_type,               # TransactionType from constants (Blueprint §14)
    description: str,
    external_reference: str,        # Blueprint §14: external_reference (not reference_id)
    idempotency_key: str = "",      # Blueprint §5.6 HARD RULE
) -> WalletTransaction:
    if wallet.balance < amount:
        raise InsufficientBalanceException()
    balance_before = wallet.balance
    wallet.balance -= amount
    txn = WalletTransaction(
        wallet_id=wallet.id,
        transaction_type=transaction_type,
        amount=amount,
        balance_before=balance_before,
        balance_after=wallet.balance,
        status=TransactionStatus.COMPLETED,     # Blueprint §14: TransactionStatus
        description=description,
        external_reference=external_reference,  # Blueprint §14
        idempotency_key=idempotency_key or f"SVC_{_uuid.uuid4().hex.upper()}",  # Blueprint §5.6
        completed_at=datetime.now(timezone.utc),
    )
    db.add(txn)
    return txn


def _credit_wallet_sync(
    db: Session,
    *,
    wallet: Wallet,
    amount: Decimal,
    transaction_type,               # TransactionType from constants (Blueprint §14)
    description: str,
    external_reference: str,        # Blueprint §14: external_reference
    idempotency_key: str = "",      # Blueprint §5.6 HARD RULE
) -> WalletTransaction:
    balance_before = wallet.balance
    wallet.balance += amount
    txn = WalletTransaction(
        wallet_id=wallet.id,
        transaction_type=transaction_type,
        amount=amount,
        balance_before=balance_before,
        balance_after=wallet.balance,
        status=TransactionStatus.COMPLETED,     # Blueprint §14: TransactionStatus
        description=description,
        external_reference=external_reference,  # Blueprint §14
        idempotency_key=idempotency_key or f"SVC_CR_{_uuid.uuid4().hex.upper()}",  # Blueprint §5.6
        completed_at=datetime.now(timezone.utc),
    )
    db.add(txn)
    return txn


def search_services(
    db: Session,
    *,
    query_text: Optional[str] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    min_price: Optional[Decimal] = None,
    max_price: Optional[Decimal] = None,
    location: Optional[tuple] = None,   # (lat, lng) GPS coordinates
    radius_km: float = 5.0,             # Blueprint default: 5 km (Section 3.1)
    service_location_type: Optional[str] = None,
    sort_by: str = "created_at",
    skip: int = 0,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """
    Search services using radius-based location filter only.

    Per Blueprint Section 3.1: no LGA filtering anywhere. All discovery is
    driven by GPS coordinates + radius (PostGIS ST_DWithin).

    Provider + business data is eagerly loaded by the CRUD layer (no N+1).
    """
    services = service_crud.search_services(
        db,
        query_text=query_text,
        category=category,
        subcategory=subcategory,
        min_price=min_price,
        max_price=max_price,
        location=location,
        radius_km=radius_km,
        service_location_type=service_location_type,
        sort_by=sort_by,
        skip=skip,
        limit=limit,
    )

    # provider and business are already loaded via joinedload — no extra queries.
    # FIX: _orm_to_dict skips Geography/Geometry columns (WKBElement) that
    # jsonable_encoder cannot serialize, coerces UUID/Decimal/datetime to
    # JSON-safe types, and returns plain dicts Pydantic can handle.
    return [
        {
            "service":  _orm_to_dict(s),
            "provider": _orm_to_dict(s.provider) if s.provider else None,
            "business": _orm_to_dict(s.provider.business) if s.provider else None,
        }
        for s in services
    ]


def get_service_details(db: Session, *, service_id: UUID) -> Dict[str, Any]:
    service = service_crud.get(db, id=service_id)
    if not service:
        raise NotFoundException("Service")

    provider = service_provider_crud.get(db, id=service.provider_id)
    if not provider:
        raise NotFoundException("ServiceProvider")

    from app.crud.business_crud import business_crud
    # FIX: business_crud.get() is async (AsyncCRUDBase); use get_sync() for sync Session
    business = business_crud.get_sync(db, id=provider.business_id)

    # FIX: use _orm_to_dict -- Geography columns cause WKBElement errors in jsonable_encoder.
    return {
        "service":  _orm_to_dict(service),
        "provider": _orm_to_dict(provider),
        "business": _orm_to_dict(business),
    }


def get_available_slots(
    db: Session,
    *,
    service_id: UUID,
    booking_date: date,
) -> List[Dict[str, Any]]:
    service = service_crud.get(db, id=service_id)
    if not service:
        raise NotFoundException("Service")

    return service_availability_crud.get_available_slots(
        db,
        provider_id=service.provider_id,
        service_duration=service.duration_minutes or 60,
        booking_date=booking_date,
    )


def calculate_booking_price(
    db: Session,
    *,
    service_id: UUID,
    selected_options: List[Dict],
    service_location_type: str,
) -> Dict[str, Decimal]:
    service = service_crud.get(db, id=service_id)
    if not service:
        raise NotFoundException("Service")

    provider = service_provider_crud.get(db, id=service.provider_id)
    if not provider:
        raise NotFoundException("ServiceProvider")

    add_ons_price = sum(
        Decimal(str(opt["price"])) for opt in selected_options if "price" in opt
    )
    travel_fee = (
        provider.travel_fee if service_location_type == "in_home" else Decimal("0.00")
    )
    # Blueprint §5.4: ₦100 from EACH side (customer + business).
    # Customer's ₦100 is added to total_price so it is included in their debit.
    # Business's ₦100 is deducted from business_credit in book_and_pay().
    # Platform earns: ₦100 + ₦100 = ₦200 per service booking.
    customer_platform_fee = PLATFORM_FEE_BOOKING   # ₦100
    total_price = service.base_price + add_ons_price + travel_fee + customer_platform_fee

    return {
        "base_price":           service.base_price,
        "add_ons_price":        add_ons_price,
        "travel_fee":           travel_fee,
        "customer_platform_fee": customer_platform_fee,
        "total_price":          total_price,   # includes customer's ₦100 fee
    }


def book_and_pay(
    db: Session,
    *,
    current_user: User,
    service_id: UUID,
    booking_date: date,
    booking_time: time,
    number_of_people: int,
    service_location_type: str,
    service_address: Optional[str],
    selected_options: List[Dict],
    special_requests: Optional[str],
    payment_method: str,
) -> ServiceBooking:
    """
    Validate → price → check slot → create booking → process payment.

    Payment flow (Blueprint Section 4.4):
      1. Debit customer wallet with total_price.
      2. Credit business wallet with total_price - ₦100 platform fee.
      3. ₦100 is retained by the platform.

    All steps run inside a single transaction; DB committed only on success.
    """
    service = service_crud.get(db, id=service_id)
    if not service or not service.is_active:
        raise NotFoundException("Service")

    provider = service_provider_crud.get(db, id=service.provider_id)
    if not provider:
        raise NotFoundException("ServiceProvider")

    # ── Availability check ─────────────────────────────────────────────────
    if not service_availability_crud.check_slot_availability(
        db,
        provider_id=service.provider_id,
        booking_date=booking_date,
        booking_time=booking_time,
    ):
        raise BookingNotAvailableException()

    # ── Price calculation ──────────────────────────────────────────────────
    price_breakdown = calculate_booking_price(
        db,
        service_id=service_id,
        selected_options=selected_options,
        service_location_type=service_location_type,
    )

    # ── Create booking record (flush only — caller controls commit) ─────────
    booking = service_booking_crud.create_booking_record(
        db,
        service_id=service_id,
        provider_id=service.provider_id,
        customer_id=current_user.id,
        booking_date=booking_date,
        booking_time=booking_time,
        duration_minutes=service.duration_minutes or 60,
        number_of_people=number_of_people,
        service_location_type=service_location_type,
        service_address=service_address,
        base_price=price_breakdown["base_price"],
        add_ons_price=price_breakdown["add_ons_price"],
        travel_fee=price_breakdown["travel_fee"],
        total_price=price_breakdown["total_price"],
        selected_options=selected_options,
        special_requests=special_requests,
    )

    # ── Payment ────────────────────────────────────────────────────────────
    if payment_method == "wallet":
        # FIX: Use sync helpers — wallet_crud methods are all async.
        customer_wallet = _get_or_create_wallet_sync(db, user_id=current_user.id)

        if customer_wallet.balance < booking.total_price:
            db.rollback()
            raise InsufficientBalanceException()

        # 1. Debit customer wallet
        customer_txn = _debit_wallet_sync(
            db,
            wallet=customer_wallet,
            amount=booking.total_price,
            transaction_type=TransactionType.PAYMENT,
            description=f"Service booking – {service.name}",
            external_reference=f"SVC_DEBIT_{booking.id}",   # Blueprint §14
            idempotency_key=f"SVC_D_{_uuid.uuid4().hex.upper()}",  # Blueprint §5.6
        )

        # 2. Credit business wallet minus ₦100 platform fee (Blueprint 4.4)
        # Blueprint §5.4: ₦100 from EACH side (two-sided fee).
        # total_price already includes customer's ₦100 (added in calculate_booking_price).
        # business_earnings = total_price - customer_fee - business_fee = total_price - ₦200
        customer_fee     = PLATFORM_FEE_BOOKING   # ₦100 (customer side — in total_price)
        business_fee     = PLATFORM_FEE_BOOKING   # ₦100 (business side — deducted separately)
        business_earnings = booking.total_price - customer_fee - business_fee

        from app.crud.business_crud import business_crud as biz_crud
        business = biz_crud.get_sync(db, id=provider.business_id)
        if business and business.user_id:
            biz_wallet = _get_or_create_wallet_sync(
                db, user_id=business.user_id
            )
            _credit_wallet_sync(
                db,
                wallet=biz_wallet,
                amount=business_earnings,
                transaction_type=TransactionType.CREDIT,
                description=f"Service booking – {service.name}",
                external_reference=f"SVC_BIZ_{booking.id}",   # Blueprint §14
                idempotency_key=f"SVC_BIZ_D_{_uuid.uuid4().hex.upper()}",  # Blueprint §5.6
            )

        booking.payment_status = PaymentStatusEnum.PAID.value   # Blueprint §14: lowercase "paid"
        booking.status = BookingStatusEnum.CONFIRMED
        # FIX: Store wallet transaction ID, not booking's own ID.
        booking.payment_reference = str(customer_txn.id)
    else:
        raise ValidationException(f"Unsupported payment method: {payment_method}")

    db.commit()
    db.refresh(booking)
    return booking


def start_service(
    db: Session, *, booking_id: UUID, provider_id: UUID
) -> ServiceBooking:
    booking = service_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")
    if booking.provider_id != provider_id:
        from app.core.exceptions import PermissionDeniedException
        raise PermissionDeniedException()
    if booking.status != BookingStatusEnum.CONFIRMED:
        raise ValidationException("Only confirmed bookings can be started")

    booking.status = BookingStatusEnum.IN_PROGRESS
    booking.started_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(booking)
    return booking


def complete_service(
    db: Session, *, booking_id: UUID, provider_id: UUID
) -> ServiceBooking:
    booking = service_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")
    if booking.provider_id != provider_id:
        from app.core.exceptions import PermissionDeniedException
        raise PermissionDeniedException()
    if booking.status != BookingStatusEnum.IN_PROGRESS:
        raise ValidationException("Only in-progress bookings can be completed")

    booking.status = BookingStatusEnum.COMPLETED
    booking.completed_at = datetime.now(timezone.utc)

    # Atomic increments — no race condition
    service_booking_crud.increment_service_stats(db, service_id=booking.service_id)
    service_booking_crud.increment_provider_stats(db, provider_id=booking.provider_id)

    db.commit()
    db.refresh(booking)
    return booking


def cancel_booking(
    db: Session,
    *,
    booking_id: UUID,
    customer_id: UUID,
    reason: Optional[str],
) -> ServiceBooking:
    booking = service_booking_crud.get(db, id=booking_id)
    if not booking:
        raise NotFoundException("Booking")
    if booking.customer_id != customer_id:
        from app.core.exceptions import PermissionDeniedException
        raise PermissionDeniedException()
    if booking.status in [BookingStatusEnum.COMPLETED, BookingStatusEnum.CANCELLED]:
        raise ValidationException(
            "Cannot cancel a completed or already cancelled booking"
        )

    booking.status = BookingStatusEnum.CANCELLED
    booking.cancelled_at = datetime.now(timezone.utc)
    booking.cancellation_reason = reason

    # Instant wallet refund on cancellation (Blueprint Section 4.1.2)
    if booking.payment_status == PaymentStatusEnum.PAID.value:  # Blueprint §14: lowercase
        # FIX: Use sync helpers — wallet_crud methods are all async.
        customer_wallet = _get_or_create_wallet_sync(
            db, user_id=booking.customer_id
        )
        _credit_wallet_sync(
            db,
            wallet=customer_wallet,
            amount=booking.total_price,
            transaction_type=TransactionType.REFUND,
            description="Refund for cancelled service booking",
            external_reference=f"SVC_REFUND_{booking.id}",   # Blueprint §14
            idempotency_key=f"SVC_REF_{_uuid.uuid4().hex.upper()}",  # Blueprint §5.6
        )
        booking.payment_status = PaymentStatusEnum.REFUNDED.value  # Blueprint §14: lowercase

        # Reverse business wallet credit (deduct earnings that were already credited)
        # Blueprint §5.4: reverse business earnings (total_price - ₦100 customer - ₦100 business)
        customer_fee     = PLATFORM_FEE_BOOKING
        business_fee     = PLATFORM_FEE_BOOKING
        business_earnings = booking.total_price - customer_fee - business_fee

        from app.crud.business_crud import business_crud as biz_crud
        provider = service_provider_crud.get(db, id=booking.provider_id)
        if provider:
            business = biz_crud.get_sync(db, id=provider.business_id)
            if business and business.user_id:
                biz_wallet = _get_or_create_wallet_sync(
                    db, user_id=business.user_id
                )
                if biz_wallet.balance >= business_earnings:
                    _debit_wallet_sync(
                        db,
                        wallet=biz_wallet,
                        amount=business_earnings,
                        transaction_type=TransactionType.REFUND,
                        description="Reversal for cancelled booking",
                        external_reference=f"SVC_BIZ_REFUND_{booking.id}",  # Blueprint §14
                        idempotency_key=f"SVC_BREF_{_uuid.uuid4().hex.upper()}",  # Blueprint §5.6
                    )

    db.commit()
    db.refresh(booking)
    return booking


def toggle_service_active(
    db: Session, *, service_id: UUID, provider_id: UUID, is_active: bool
) -> None:
    """Enable or disable a service offering."""
    service = service_crud.get(db, id=service_id)
    if not service:
        raise NotFoundException("Service")
    if service.provider_id != provider_id:
        from app.core.exceptions import PermissionDeniedException
        raise PermissionDeniedException()

    service.is_active = is_active
    db.commit()


def get_provider_analytics(
    db: Session, *, provider_id: UUID
) -> Dict[str, Any]:
    """
    Aggregate analytics for a service provider's dashboard.

    Returns: total bookings, revenue, completion rate, status breakdown,
    revenue trend (last 7 days), top services by booking count.
    """
    from sqlalchemy import func, case
    from app.models.services_model import Service

    today = datetime.now(timezone.utc).date()
    seven_days_ago = today - timedelta(days=7)

    # ── Overall booking stats ──────────────────────────────────────────────
    stats = db.query(
        func.count(ServiceBooking.id).label("total_bookings"),
        func.coalesce(
            func.sum(
                case(
                    (
                        ServiceBooking.status == BookingStatusEnum.COMPLETED,
                        ServiceBooking.total_price,
                    ),
                    else_=0,
                )
            ),
            0,
        ).label("total_revenue"),
        func.count(
            case(
                (ServiceBooking.status == BookingStatusEnum.PENDING, 1),
                else_=None,
            )
        ).label("pending_count"),
        func.count(
            case(
                (ServiceBooking.status == BookingStatusEnum.CONFIRMED, 1),
                else_=None,
            )
        ).label("confirmed_count"),
        func.count(
            case(
                (ServiceBooking.status == BookingStatusEnum.COMPLETED, 1),
                else_=None,
            )
        ).label("completed_count"),
        func.count(
            case(
                (ServiceBooking.status == BookingStatusEnum.CANCELLED, 1),
                else_=None,
            )
        ).label("cancelled_count"),
    ).filter(ServiceBooking.provider_id == provider_id).first()

    total = stats.total_bookings or 0
    completed = stats.completed_count or 0
    completion_rate = round((completed / total * 100) if total else 0, 1)

    # ── Revenue trend — last 7 days ────────────────────────────────────────
    daily_rows = (
        db.query(
            ServiceBooking.booking_date,
            func.sum(ServiceBooking.total_price).label("revenue"),
        )
        .filter(
            ServiceBooking.provider_id == provider_id,
            ServiceBooking.status == BookingStatusEnum.COMPLETED,
            ServiceBooking.booking_date >= seven_days_ago,
        )
        .group_by(ServiceBooking.booking_date)
        .order_by(ServiceBooking.booking_date)
        .all()
    )

    # Fill gaps for days with no revenue
    daily_map = {str(r.booking_date): float(r.revenue or 0) for r in daily_rows}
    revenue_trend = []
    for i in range(7):
        d = seven_days_ago + timedelta(days=i)
        revenue_trend.append({"date": str(d), "amount": daily_map.get(str(d), 0.0)})

    # ── Top services by booking count ──────────────────────────────────────
    top_services_rows = (
        db.query(
            Service.name,
            func.count(ServiceBooking.id).label("bookings"),
            func.coalesce(func.sum(ServiceBooking.total_price), 0).label("revenue"),
        )
        .join(ServiceBooking, Service.id == ServiceBooking.service_id)
        .filter(ServiceBooking.provider_id == provider_id)
        .group_by(Service.name)
        .order_by(func.count(ServiceBooking.id).desc())
        .limit(5)
        .all()
    )

    return {
        "total_bookings":   total,
        "total_revenue":    float(stats.total_revenue or 0),
        "pending_bookings": stats.pending_count or 0,
        "confirmed_bookings": stats.confirmed_count or 0,
        "completed_bookings": completed,
        "cancelled_bookings": stats.cancelled_count or 0,
        "completion_rate":  completion_rate,
        "revenue_trend":    revenue_trend,
        "top_services": [
            {
                "name":     r.name,
                "bookings": r.bookings,
                "revenue":  float(r.revenue),
            }
            for r in top_services_rows
        ],
    }