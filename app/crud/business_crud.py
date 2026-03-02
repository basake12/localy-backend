from typing import Optional, Tuple, List
from sqlalchemy.orm import Session
from sqlalchemy import or_
from uuid import UUID

from app.crud.base_crud import CRUDBase
from app.models.business_model import Business


class CRUDBusiness(CRUDBase[Business, dict, dict]):
    """CRUD operations for Business"""

    def get_by_user_id(self, db: Session, *, user_id: UUID) -> Optional[Business]:
        """Get business by user ID"""
        return db.query(Business).filter(Business.user_id == user_id).first()

    def get_by_category(
            self,
            db: Session,
            *,
            category: str,
            skip: int = 0,
            limit: int = 100
    ) -> List[Business]:
        """Get businesses by category"""
        return db.query(Business).filter(
            Business.category == category,
            Business.is_active == True
        ).offset(skip).limit(limit).all()

    def search_businesses(
            self,
            db: Session,
            *,
            category: Optional[str] = None,
            search_query: Optional[str] = None,
            skip: int = 0,
            limit: int = 100
    ) -> Tuple[List[Business], int]:
        """
        Search businesses with filters

        Args:
            db: Database session
            category: Filter by business category
            search_query: Search in business name, description
            skip: Pagination offset
            limit: Max results

        Returns:
            Tuple of (businesses list, total count)
        """
        query = db.query(Business).filter(Business.is_active == True)

        # Apply category filter
        if category:
            query = query.filter(Business.category == category)

        # Apply search query
        if search_query:
            search_pattern = f"%{search_query}%"
            query = query.filter(
                or_(
                    Business.business_name.ilike(search_pattern),
                    Business.description.ilike(search_pattern),
                    Business.subcategory.ilike(search_pattern)
                )
            )

        # Get total count before pagination
        total = query.count()

        # Apply pagination and ordering
        businesses = query.order_by(
            Business.is_featured.desc(),
            Business.average_rating.desc(),
            Business.created_at.desc()
        ).offset(skip).limit(limit).all()

        return businesses, total


business_crud = CRUDBusiness(Business)