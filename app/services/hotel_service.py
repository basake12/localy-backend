"""
app/services/hotel_service.py

Hotel booking orchestration service with payment processing.

Per Blueprint Section 6.1 — Hotels Module:
- Real-time availability checking
- Instant booking with ₦100 platform fee
- Wallet payment with atomic transactions
- Instant refunds on cancellation
- Transaction atomicity via transaction_service

BUG FIXES IN THIS VERSION:
────────────────────────────
BUG-01 FIX (hotel_service.py — AttributeError on AddOnItem.items()):
  In create_booking_and_pay(), the sanitized_add_ons list comprehension called
  item.items() on each element of the add_ons argument.  The router passes
  List[AddOnItem] — Pydantic v2 models — which do NOT have an .items() method.
  This raised AttributeError on every booking that included an add-on.

  Root cause traced: the BookingCreateRequest schema defines
      add_ons: List[AddOnItem]
  The router passes booking_in.add_ons directly to the service without
  converting to dicts.  The CRUD (hotels_crud.py) already handles this
  correctly with:
      item if isinstance(item, dict) else item.model_dump()
  The service sanitization step needed the same treatment.

  Fix: Convert each AddOnItem to a dict via model_dump() before iterating
  with .items().  The conversion also handles any Decimal price values so
  the JSONB INSERT does not raise TypeError.

BUG-03 FIX (hotel_service.py — 'local_government' LGA field in _business_to_dict):
  Blueprint Section 2.2 HARD RULE: "Location is radius-based exclusively.
  There is no local government area (LGA) filtering anywhere in the codebase.
  No LGA column exists in any database table.  Remove immediately if discovered
  in legacy code."
  _business_to_dict() included "local_government": getattr(biz, ...) in the
  projected dict.  This surfaces LGA data in every hotel search result and
  every hotel detail response that calls this helper.  Removed.

Original FIX note (v2):
  search_hotels() previously returned raw ORM objects with duplicated keys
  and PydanticSerializationError on PostGIS WKBElement fields.
  Now returns flat plain dicts.  No ORM objects in any response shape.
"""
import logging
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from datetime import date
from decimal import Decimal

from app.crud.hotels_crud import hotel_crud, room_type_crud, hotel_booking_crud
from app.crud.business_crud import business_crud
from app.services.transaction_service import transaction_service
from app.models.hotels_model import HotelBooking, BookingStatusEnum, PaymentStatusEnum
from app.models.wallet_model import WalletTransaction, PlatformRevenue
from app.core.exceptions import (
    NotFoundException,
    ValidationException,
    InsufficientBalanceException,
)
from app.core.constants import DEFAULT_RADIUS_METERS

logger = logging.getLogger(__name__)


class HotelService:
    """
    Hotel booking orchestration with integrated payment processing.

    All payment operations use transaction_service to ensure:
    - Atomic transactions (all-or-nothing)
    - Platform fee deduction (₦100 for hotel bookings per Blueprint §5.4)
    - Wallet balance management
    - Revenue tracking
    """

    # ── Private projection helpers ─────────────────────────────────────────

    @staticmethod
    def _business_to_dict(biz) -> Optional[Dict[str, Any]]:
        """
        Project a Business ORM object to a plain serialisable dict.

        BUG-03 FIX: 'local_government' has been removed.
        Blueprint Section 2.2 HARD RULE: no LGA column exists anywhere in the
        codebase.  The original helper included:
            "local_government": getattr(biz, "local_government", None)
        which surfaced LGA data in every search result.  Removed.
        """
        if biz is None:
            return None
        return {
            "id": biz.id,
            "business_name": biz.business_name,
            "category": biz.category,
            "subcategory": getattr(biz, "subcategory", None),
            "description": getattr(biz, "description", None),
            "address": biz.registered_address,
            "city": getattr(biz, "city", None),
            # BUG-03 FIX: "local_government" field removed — LGA HARD RULE.
            "state": getattr(biz, "state", None),
            "latitude": getattr(biz, "latitude", None),
            "longitude": getattr(biz, "longitude", None),
            "business_phone": getattr(biz, "business_phone", None),
            "business_email": getattr(biz, "business_email", None),
            "website": getattr(biz, "website", None),
            "instagram": getattr(biz, "instagram", None),
            "whatsapp": getattr(biz, "whatsapp", None),
            "logo": getattr(biz, "logo", None),
            "banner_image": getattr(biz, "banner_image", None),
            "average_rating": float(biz.average_rating) if biz.average_rating else 0.0,
            "total_reviews": getattr(biz, "total_reviews", 0),
            "verification_badge": getattr(biz, "verification_badge", None),
            "subscription_tier": getattr(biz, "subscription_tier", None),
            "is_verified": getattr(biz, "is_verified", False),
            "is_featured": getattr(biz, "is_featured", False),
        }

    @staticmethod
    def _room_type_to_dict(rt) -> Dict[str, Any]:
        """Project a RoomType ORM object to a plain serialisable dict."""
        return {
            "id": rt.id,
            "hotel_id": rt.hotel_id,
            "name": rt.name,
            "description": rt.description,
            "bed_configuration": rt.bed_configuration,
            "max_occupancy": rt.max_occupancy,
            "size_sqm": float(rt.size_sqm) if rt.size_sqm else None,
            "floor_range": rt.floor_range,
            "view_type": rt.view_type,
            "amenities": rt.amenities or [],
            "base_price_per_night": float(rt.base_price_per_night),
            "images": rt.images or [],
            "total_rooms": rt.total_rooms,
            "created_at": rt.created_at,
        }

    @staticmethod
    def _hotel_to_dict(
        hotel,
        business_dict: Optional[Dict[str, Any]],
        room_types: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Project a Hotel ORM object + pre-built sub-dicts to a flat plain dict.

        No nested ORM objects — safe for jsonable_encoder and Flutter parsing.
        Single 'room_types' key always present (never 'available_room_types').
        """
        return {
            "id": hotel.id,
            "business_id": hotel.business_id,
            "star_rating": hotel.star_rating,
            "total_rooms": hotel.total_rooms,
            "check_in_time": hotel.check_in_time,
            "check_out_time": hotel.check_out_time,
            "facilities": hotel.facilities or [],
            "policies": hotel.policies,
            "cancellation_policy": hotel.cancellation_policy,
            "created_at": hotel.created_at,
            "business": business_dict,
            "room_types": room_types,
        }

    # ── Public methods ─────────────────────────────────────────────────────

    async def search_hotels(
        self,
        db: AsyncSession,
        *,
        location: Optional[Tuple[float, float]] = None,
        radius_km: float = 5.0,
        check_in: Optional[date] = None,
        check_out: Optional[date] = None,
        guests: int = 1,
        rooms: int = 1,
        star_rating: Optional[int] = None,
        facilities: Optional[List[str]] = None,
        min_price: Optional[Decimal] = None,
        max_price: Optional[Decimal] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Search hotels with optional availability filtering.

        Always returns a list of flat plain dicts — no ORM objects, no
        duplicated keys, consistent shape regardless of whether date params
        are supplied.  Single 'room_types' key always present.
        """
        radius_meters = int(radius_km * 1000)

        hotels = await hotel_crud.search_hotels(
            db,
            skip=skip,
            limit=limit,
            location=location,
            radius_meters=radius_meters,
            star_rating=star_rating,
            facilities=facilities,
            min_price=min_price,
            max_price=max_price,
        )

        results: List[Dict[str, Any]] = []
        for hotel in hotels:
            if check_in and check_out:
                # Availability-filtered room types (already plain dicts from CRUD).
                room_types: List[Dict[str, Any]] = (
                    await room_type_crud.get_available_room_types(
                        db,
                        hotel_id=hotel.id,
                        check_in=check_in,
                        check_out=check_out,
                        number_of_rooms=rooms,
                        number_of_guests=guests,
                    )
                )
            else:
                # Project eagerly-loaded ORM list to plain dicts.
                room_types = [
                    self._room_type_to_dict(rt)
                    for rt in (hotel.room_types or [])
                ]

            results.append(
                self._hotel_to_dict(
                    hotel,
                    business_dict=self._business_to_dict(hotel.business),
                    room_types=room_types,
                )
            )

        return results

    async def create_booking_and_pay(
        self,
        db: AsyncSession,
        *,
        customer_id: UUID,
        hotel_id: UUID,
        room_type_id: UUID,
        check_in: date,
        check_out: date,
        number_of_rooms: int,
        number_of_guests: int,
        add_ons: List[Any],
        special_requests: Optional[str] = None,
    ) -> Tuple[HotelBooking, WalletTransaction, WalletTransaction, PlatformRevenue]:
        """
        Create booking and process payment atomically.

        Returns: (booking, customer_txn, business_txn, revenue)

        BUG-01 FIX (add_ons AttributeError):
          The add_ons list may contain either Pydantic AddOnItem instances (from
          the router) or plain dicts (from internal callers / tests).  The
          previous implementation called item.items() unconditionally, raising
          AttributeError on Pydantic model instances (Pydantic v2 models have
          no .items() method).

          Fix: normalise each item to a plain dict first via model_dump() if it
          is not already a dict, then handle Decimal→float conversion.  This is
          the same defensive pattern already used in hotels_crud.create_booking().
        """
        hotel = await hotel_crud.get(db, id=hotel_id)
        if not hotel:
            raise NotFoundException("Hotel")

        business = await business_crud.get(db, id=hotel.business_id)
        if not business or not business.user_id:
            raise NotFoundException("Hotel business owner")

        try:
            # BUG-01 FIX: Normalise add_ons items to plain dicts before
            # iterating with .items().
            #
            # Before (broken): item.items() — raises AttributeError when item is
            # an AddOnItem Pydantic model (the type coming from the router).
            #
            # After (correct): convert to dict first, then convert Decimal values
            # to float so stdlib json (used by SQLAlchemy for JSONB) can serialise
            # them without TypeError.
            sanitized_add_ons = [
                {
                    k: float(v) if isinstance(v, Decimal) else v
                    for k, v in (
                        item if isinstance(item, dict) else item.model_dump()
                    ).items()
                }
                for item in (add_ons or [])
            ]

            booking = await hotel_booking_crud.create_booking(
                db,
                hotel_id=hotel_id,
                room_type_id=room_type_id,
                customer_id=customer_id,
                check_in=check_in,
                check_out=check_out,
                number_of_rooms=number_of_rooms,
                number_of_guests=number_of_guests,
                add_ons=sanitized_add_ons,
                special_requests=special_requests,
            )

            await db.flush()

            customer_txn, business_txn, revenue = await transaction_service.process_payment(
                db,
                customer_id=customer_id,
                business_id=business.user_id,
                gross_amount=booking.total_price,
                transaction_type="hotel_booking",
                description=f"Hotel booking #{booking.id}",
                reference=f"HOTEL_BOOKING_{booking.id}",
                related_entity_id=booking.id,
                metadata={
                    "hotel_id": str(hotel_id),
                    "room_type_id": str(room_type_id),
                    "check_in": check_in.isoformat(),
                    "check_out": check_out.isoformat(),
                    "number_of_rooms": number_of_rooms,
                    "number_of_guests": number_of_guests,
                },
            )

            booking.payment_status = PaymentStatusEnum.PAID
            booking.status = BookingStatusEnum.CONFIRMED

            await db.commit()
            await db.refresh(booking)

            logger.info(
                "Booking created: %s | Amount: ₦%s | Platform fee: ₦100",
                booking.id,
                booking.total_price,
            )

            return booking, customer_txn, business_txn, revenue

        except InsufficientBalanceException:
            await db.rollback()
            raise
        except Exception as e:
            await db.rollback()
            logger.error("Booking failed for hotel %s: %s", hotel_id, e)
            raise ValidationException(f"Booking failed: {str(e)}")

    async def cancel_booking_and_refund(
        self,
        db: AsyncSession,
        *,
        booking_id: UUID,
        customer_id: UUID,
        reason: Optional[str] = None,
    ) -> Tuple[HotelBooking, WalletTransaction, WalletTransaction, Optional[PlatformRevenue]]:
        """
        Cancel booking and process refund atomically.

        Returns: (booking, customer_refund, business_debit, reversed_revenue)
        """
        booking = await hotel_booking_crud.get(db, id=booking_id)
        if not booking:
            raise NotFoundException("Booking")

        if booking.customer_id != customer_id:
            raise ValidationException("You can only cancel your own bookings")

        if booking.status in [BookingStatusEnum.CHECKED_IN, BookingStatusEnum.CHECKED_OUT]:
            raise ValidationException("Cannot cancel a booking that is checked-in or already completed")

        if booking.status == BookingStatusEnum.CANCELLED:
            raise ValidationException("Booking is already cancelled")

        hotel = await hotel_crud.get(db, id=booking.hotel_id)
        if not hotel:
            raise NotFoundException("Hotel")

        business = await business_crud.get(db, id=hotel.business_id)
        if not business or not business.user_id:
            raise NotFoundException("Hotel business owner")

        try:
            await hotel_booking_crud.cancel_booking(
                db,
                booking_id=booking_id,
                reason=reason,
            )

            customer_refund, business_debit, reversed_revenue = (
                await transaction_service.process_refund(
                    db,
                    original_reference=f"HOTEL_BOOKING_{booking_id}",
                    refund_reference=f"HOTEL_REFUND_{booking_id}",
                    refund_amount=None,
                    description=reason or "Hotel booking cancellation",
                    metadata={
                        "booking_id": str(booking_id),
                        "hotel_id": str(hotel.id),
                    },
                )
            )

            booking.payment_status = PaymentStatusEnum.REFUNDED

            await db.commit()
            await db.refresh(booking)

            logger.info(
                "Booking cancelled: %s | Refund: ₦%s",
                booking_id,
                booking.total_price,
            )

            return booking, customer_refund, business_debit, reversed_revenue

        except Exception as e:
            await db.rollback()
            logger.error("Cancellation failed for booking %s: %s", booking_id, e)
            raise ValidationException(f"Cancellation failed: {str(e)}")


hotel_service = HotelService()