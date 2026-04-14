"""
rating_service.py

Handles post-transaction rating propagation to entity aggregate tables.
Called by review_service after a review is committed.

NOTE: This service NEVER calls db.commit() — the caller owns the transaction.
Surge pricing and commission calculations live in app/services/pricing_service.py.
"""
from sqlalchemy.orm import Session
from uuid import UUID

from app.crud.business_crud import business_crud
from app.crud.rider_crud import rider_crud
from app.core.utils import calculate_new_average


class RatingPropagationService:
    """
    Propagates new review scores to the denormalized average_rating fields
    on Business and Rider records.

    Called synchronously after a review is saved (within the same transaction).
    For high-traffic deployments consider offloading to a Celery task.
    """

    def update_business_rating(
        self,
        db: Session,
        *,
        business_id: UUID,
        new_rating: float,
    ) -> None:
        """
        Recalculate and persist the business average rating.
        Does NOT commit — the caller is responsible for the transaction.
        """
        business = business_crud.get(db, id=business_id)
        if not business:
            return  # Business may have been deleted; silently skip

        new_avg = calculate_new_average(
            current_avg=float(business.average_rating or 0),
            current_count=business.total_reviews or 0,
            new_value=new_rating,
        )

        business.average_rating = round(new_avg, 2)
        business.total_reviews  = (business.total_reviews or 0) + 1
        db.flush()

    def update_rider_rating(
        self,
        db: Session,
        *,
        rider_id: UUID,
        new_rating: float,
    ) -> None:
        """
        Delegate rider rating update to the rider CRUD.
        Does NOT commit — the caller is responsible for the transaction.
        """
        rider_crud.update_stats(
            db,
            rider_id=rider_id,
            new_rating=new_rating,
        )


# Singleton
rating_propagation_service = RatingPropagationService()