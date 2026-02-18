from typing import Generic, TypeVar, Type, Optional, List, Any, Dict, Union
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from uuid import UUID

from app.models.base import BaseModel as DBBaseModel

ModelType = TypeVar("ModelType", bound=DBBaseModel)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)


class CRUDBase(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """
    Base CRUD class with generic database operations

    Usage:
        user_crud = CRUDBase(User)
        user = user_crud.get(db, id=user_id)
    """

    def __init__(self, model: Type[ModelType]):
        """
        Initialize CRUD object with model class

        Args:
            model: SQLAlchemy model class
        """
        self.model = model

    def get(self, db: Session, id: UUID) -> Optional[ModelType]:
        """
        Get a single record by ID

        Args:
            db: Database session
            id: Record ID

        Returns:
            Model instance or None
        """
        return db.query(self.model).filter(self.model.id == id).first()

    def get_multi(
            self,
            db: Session,
            *,
            skip: int = 0,
            limit: int = 100,
            filters: Optional[Dict[str, Any]] = None,
            order_by: Optional[str] = None
    ) -> List[ModelType]:
        """
        Get multiple records with pagination and filtering

        Args:
            db: Database session
            skip: Number of records to skip
            limit: Maximum number of records to return
            filters: Dictionary of filters {column_name: value}
            order_by: Column name to order by

        Returns:
            List of model instances
        """
        query = db.query(self.model)

        # Apply filters
        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key):
                    query = query.filter(getattr(self.model, key) == value)

        # Apply ordering
        if order_by and hasattr(self.model, order_by):
            query = query.order_by(getattr(self.model, order_by).desc())
        else:
            query = query.order_by(self.model.created_at.desc())

        return query.offset(skip).limit(limit).all()

    def count(
            self,
            db: Session,
            filters: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Count total records matching filters

        Args:
            db: Database session
            filters: Dictionary of filters

        Returns:
            Total count
        """
        query = db.query(func.count(self.model.id))

        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key):
                    query = query.filter(getattr(self.model, key) == value)

        return query.scalar()

    def create(self, db: Session, *, obj_in: CreateSchemaType) -> ModelType:
        """
        Create a new record

        Args:
            db: Database session
            obj_in: Pydantic schema with data

        Returns:
            Created model instance
        """
        obj_in_data = jsonable_encoder(obj_in)
        db_obj = self.model(**obj_in_data)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def create_from_dict(self, db: Session, *, obj_in: Dict[str, Any]) -> ModelType:
        """
        Create a new record from dictionary

        Args:
            db: Database session
            obj_in: Dictionary with data

        Returns:
            Created model instance
        """
        db_obj = self.model(**obj_in)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(
            self,
            db: Session,
            *,
            db_obj: ModelType,
            obj_in: Union[UpdateSchemaType, Dict[str, Any]]
    ) -> ModelType:
        """
        Update an existing record

        Args:
            db: Database session
            db_obj: Existing model instance
            obj_in: Pydantic schema or dict with update data

        Returns:
            Updated model instance
        """
        # Extract update data from input
        if isinstance(obj_in, dict):
            update_data = obj_in
        else:
            update_data = obj_in.model_dump(exclude_unset=True)

        # Apply updates directly to db_obj
        for field, value in update_data.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)

        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def delete(self, db: Session, *, id: UUID) -> Optional[ModelType]:
        """
        Delete a record by ID

        Args:
            db: Database session
            id: Record ID

        Returns:
            Deleted model instance or None
        """
        obj = db.query(self.model).get(id)
        if obj:
            db.delete(obj)
            db.commit()
        return obj

    def exists(self, db: Session, id: UUID) -> bool:
        """
        Check if record exists

        Args:
            db: Database session
            id: Record ID

        Returns:
            True if exists, False otherwise
        """
        return db.query(
            db.query(self.model).filter(self.model.id == id).exists()
        ).scalar()

    def get_or_none(self, db: Session, **kwargs) -> Optional[ModelType]:
        """
        Get record by arbitrary filters or return None

        Args:
            db: Database session
            **kwargs: Filter conditions

        Returns:
            Model instance or None
        """
        query = db.query(self.model)
        for key, value in kwargs.items():
            if hasattr(self.model, key):
                query = query.filter(getattr(self.model, key) == value)
        return query.first()