"""
app/crud/address_crud.py

Full CRUD for CustomerAddress — replaces the empty stub.
Methods match every call made in app/api/v1/users.py:
  - get_by_user
  - create_for_user
  - get_for_user
  - update
  - remove
  - set_default
"""
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.address_model import CustomerAddress
from app.schemas.user_schema import CustomerAddressCreate, CustomerAddressUpdate


class AddressCRUD:

    # ── Read ──────────────────────────────────────────────────────────────

    def get_by_user(self, db: Session, *, user_id: UUID) -> List[CustomerAddress]:
        """Return all addresses for a user, default first."""
        return (
            db.query(CustomerAddress)
            .filter(CustomerAddress.user_id == user_id)
            .order_by(CustomerAddress.is_default.desc(), CustomerAddress.created_at.asc())
            .all()
        )

    def get_for_user(
        self, db: Session, *, address_id: UUID, user_id: UUID
    ) -> Optional[CustomerAddress]:
        """Return a single address only if it belongs to the given user."""
        return (
            db.query(CustomerAddress)
            .filter(
                CustomerAddress.id == address_id,
                CustomerAddress.user_id == user_id,
            )
            .first()
        )

    # ── Create ────────────────────────────────────────────────────────────

    def create_for_user(
        self, db: Session, *, user_id: UUID, obj_in: CustomerAddressCreate
    ) -> CustomerAddress:
        """
        Create a new address for the user.
        If is_default=True, demote any existing default first.
        """
        data = obj_in.model_dump()

        if data.get("is_default"):
            self._clear_default(db, user_id=user_id)

        # If this is the user's very first address, make it default automatically.
        existing_count = (
            db.query(CustomerAddress)
            .filter(CustomerAddress.user_id == user_id)
            .count()
        )
        if existing_count == 0:
            data["is_default"] = True

        address = CustomerAddress(user_id=user_id, **data)
        db.add(address)
        db.commit()
        db.refresh(address)
        return address

    # ── Update ────────────────────────────────────────────────────────────

    def update(
        self,
        db: Session,
        *,
        db_obj: CustomerAddress,
        obj_in: CustomerAddressUpdate,
    ) -> CustomerAddress:
        """Partial update — only sets fields that were explicitly provided."""
        update_data = obj_in.model_dump(exclude_none=True)

        if update_data.get("is_default"):
            self._clear_default(db, user_id=db_obj.user_id)

        for field, value in update_data.items():
            setattr(db_obj, field, value)

        db.commit()
        db.refresh(db_obj)
        return db_obj

    # ── Delete ────────────────────────────────────────────────────────────

    def remove(self, db: Session, *, id: UUID) -> None:
        """Delete an address by primary key."""
        address = db.query(CustomerAddress).filter(CustomerAddress.id == id).first()
        if address:
            db.delete(address)
            db.commit()

    # ── Set default ───────────────────────────────────────────────────────

    def set_default(self, db: Session, *, user_id: UUID, address_id: UUID) -> None:
        """
        Make address_id the default for the user.
        Clears is_default on all other addresses atomically.
        """
        self._clear_default(db, user_id=user_id)

        db.query(CustomerAddress).filter(
            CustomerAddress.id == address_id,
            CustomerAddress.user_id == user_id,
        ).update({"is_default": True})

        db.commit()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _clear_default(self, db: Session, *, user_id: UUID) -> None:
        """Demote all current defaults for a user (no commit — caller commits)."""
        db.query(CustomerAddress).filter(
            CustomerAddress.user_id == user_id,
            CustomerAddress.is_default.is_(True),
        ).update({"is_default": False})


address_crud = AddressCRUD()