"""
File upload service with Cloudinary storage.
"""
import io
from typing import Optional

import cloudinary
import cloudinary.uploader
from fastapi import UploadFile
from PIL import Image

from app.config import settings
from app.core.utils import validate_file_size, sanitize_filename
from app.core.exceptions import ValidationException

# ─── Cloudinary Config ────────────────────────────────────────────────────────

cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
    secure=True,
)


class UploadService:
    """File upload validation and processing via Cloudinary."""

    ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/jpg'}
    ALLOWED_DOC_TYPES = {
        'application/pdf',
        'application/msword',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    }
    MAX_IMAGE_SIZE_MB = 10
    MAX_DOC_SIZE_MB   = 50

    # ── Validation ────────────────────────────────────────────────────────────

    def validate_image(self, file: UploadFile) -> None:
        if file.content_type not in self.ALLOWED_IMAGE_TYPES:
            raise ValidationException(
                f"Invalid image type. Allowed: {', '.join(self.ALLOWED_IMAGE_TYPES)}"
            )
        file.file.seek(0, 2)
        size = file.file.tell()
        file.file.seek(0)
        if not validate_file_size(size, self.MAX_IMAGE_SIZE_MB):
            raise ValidationException(f"Image too large. Max: {self.MAX_IMAGE_SIZE_MB}MB")

    def validate_document(self, file: UploadFile) -> None:
        if file.content_type not in self.ALLOWED_DOC_TYPES:
            raise ValidationException(
                f"Invalid document type. Allowed: {', '.join(self.ALLOWED_DOC_TYPES)}"
            )
        file.file.seek(0, 2)
        size = file.file.tell()
        file.file.seek(0)
        if not validate_file_size(size, self.MAX_DOC_SIZE_MB):
            raise ValidationException(f"Document too large. Max: {self.MAX_DOC_SIZE_MB}MB")

    # ── Image Processing ──────────────────────────────────────────────────────

    def resize_image(
        self,
        file_content: bytes,
        max_width: int = 1920,
        max_height: int = 1920,
        quality: int = 85,
    ) -> bytes:
        img = Image.open(io.BytesIO(file_content))
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        if img.width > max_width or img.height > max_height:
            img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=quality, optimize=True)
        return output.getvalue()

    # ── Upload Methods ────────────────────────────────────────────────────────

    def upload_image(
        self,
        file: UploadFile,
        category: str = "general",
        folder: Optional[str] = None,
        resize: bool = True,
    ) -> str:
        """Upload and process image to Cloudinary."""
        self.validate_image(file)
        content = file.file.read()
        if resize:
            content = self.resize_image(content)

        result = cloudinary.uploader.upload(
            io.BytesIO(content),
            folder=f"localy/images/{folder or category}",
            resource_type="image",
        )
        url = result.get("secure_url")
        if not url:
            raise ValidationException("Upload failed")
        return url

    def upload_document(
        self,
        file: UploadFile,
        user_id: str,
        category: str = "general",
    ) -> str:
        """Upload document to Cloudinary."""
        self.validate_document(file)
        result = cloudinary.uploader.upload(
            file.file,
            folder=f"localy/documents/{user_id}/{category}",
            resource_type="raw",
            public_id=sanitize_filename(file.filename),
        )
        url = result.get("secure_url")
        if not url:
            raise ValidationException("Upload failed")
        return url

    def upload_avatar(self, file: UploadFile, user_id: str) -> str:
        """Upload and resize user avatar to Cloudinary."""
        self.validate_image(file)
        content = file.file.read()
        content = self.resize_image(content, max_width=500, max_height=500, quality=90)

        result = cloudinary.uploader.upload(
            io.BytesIO(content),
            folder="localy/avatars",
            public_id=f"avatar_{user_id}",   # deterministic — overwrites old avatar
            overwrite=True,
            resource_type="image",
        )
        url = result.get("secure_url")
        if not url:
            raise ValidationException("Upload failed")
        return url


# Singleton instance
upload_service = UploadService()