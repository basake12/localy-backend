"""
scripts/create_admin.py

Interactive script to create the first admin user.

    python scripts/create_admin.py

Never hardcode credentials — the script prompts for them at runtime.
"""
import sys
import os
import getpass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.core.constants import UserType, UserStatus
from app.models.user import User
from app.core.security import hash_password
import uuid
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
logger = logging.getLogger(__name__)


def create_admin() -> None:
    print("\n═══ Localy — Create Admin User ═══\n")

    name     = input("Full name: ").strip()
    email    = input("Email:     ").strip().lower()
    phone    = input("Phone (+234...): ").strip()
    password = getpass.getpass("Password (hidden): ")
    confirm  = getpass.getpass("Confirm password:  ")

    if not all([name, email, phone, password]):
        print("❌  All fields are required.")
        sys.exit(1)

    if password != confirm:
        print("❌  Passwords do not match.")
        sys.exit(1)

    if len(password) < 8:
        print("❌  Password must be at least 8 characters.")
        sys.exit(1)

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            print(f"❌  A user with email '{email}' already exists.")
            sys.exit(1)

        admin = User(
            id=uuid.uuid4(),
            full_name=name,
            email=email,
            phone=phone,
            hashed_password=hash_password(password),
            user_type=UserType.ADMIN,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
            is_phone_verified=True,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        print(f"\n✅  Admin user created: {admin.email} (id={admin.id})\n")
    except Exception as exc:
        db.rollback()
        print(f"❌  Error creating admin: {exc}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    create_admin()