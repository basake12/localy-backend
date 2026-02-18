"""
Rating and dynamic pricing service.
"""
from sqlalchemy.orm import Session
from uuid import UUID
from decimal import Decimal

from app.crud.business import business_crud
from app.crud.rider import rider_crud
from app.core.utils import calculate_new_average


class RateService:
    """Rating and pricing calculations."""

    def update_business_rating(
            self,
            db: Session,
            *,
            business_id: UUID,
            new_rating: float
    ) -> None:
        """Update business average rating."""
        business = business_crud.get(db, id=business_id)
        if not business:
            return

        # Calculate new average
        new_avg = calculate_new_average(
            float(business.average_rating),
            business.total_reviews,
            new_rating
        )

        # Update business
        business.average_rating = Decimal(str(new_avg))
        business.total_reviews += 1
        db.commit()

    def update_rider_rating(
            self,
            db: Session,
            *,
            rider_id: UUID,
            new_rating: float
    ) -> None:
        """Update rider average rating."""
        rider_crud.update_stats(
            db,
            rider_id=rider_id,
            new_rating=new_rating
        )

    def calculate_surge_pricing(
            self,
            base_price: Decimal,
            demand_factor: float = 1.0,
            time_of_day_factor: float = 1.0,
            weather_factor: float = 1.0
    ) -> Decimal:
        """
        Calculate dynamic pricing with surge.

        Args:
            base_price: Original price
            demand_factor: 1.0-2.0 based on demand
            time_of_day_factor: 1.0-1.5 for peak hours
            weather_factor: 1.0-1.3 for bad weather

        Returns:
            Adjusted price
        """
        surge_multiplier = demand_factor * time_of_day_factor * weather_factor
        # Cap at 2.5x
        surge_multiplier = min(surge_multiplier, 2.5)

        return base_price * Decimal(str(surge_multiplier))

    def calculate_platform_commission(
            self,
            order_amount: Decimal,
            category: str = "general"
    ) -> Decimal:
        """Calculate platform commission based on category."""
        rates = {
            "food": 0.15,  # 15%
            "products": 0.10,  # 10%
            "hotels": 0.12,  # 12%
            "services": 0.15,  # 15%
            "tickets": 0.08,  # 8%
            "general": 0.10  # 10%
        }

        rate = rates.get(category, 0.10)
        return order_amount * Decimal(str(rate))


# Singleton instance
rate_service = RateService()