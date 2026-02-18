"""
File upload service with validation.
"""
from typing import BinaryIO, Optional
from fastapi import UploadFile
from PIL import Image
import io

from app.core.storage import storage_service
from app.core.utils import validate_file_size, sanitize_filename
from app.core.exceptions import ValidationException


class UploadService:
    """File upload validation and processing."""

    ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/jpg'}
    ALLOWED_DOC_TYPES = {'application/pdf', 'application/msword',
                         'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
    MAX_IMAGE_SIZE_MB = 10
    MAX_DOC_SIZE_MB = 50

    def validate_image(self, file: UploadFile) -> None:
        """Validate image file."""
        # Check content type
        if file.content_type not in self.ALLOWED_IMAGE_TYPES:
            raise ValidationException(
                f"Invalid image type. Allowed: {', '.join(self.ALLOWED_IMAGE_TYPES)}"
            )

        # Check file size
        file.file.seek(0, 2)  # Seek to end
        size = file.file.tell()
        file.file.seek(0)  # Reset

        if not validate_file_size(size, self.MAX_IMAGE_SIZE_MB):
            raise ValidationException(f"Image too large. Max: {self.MAX_IMAGE_SIZE_MB}MB")

    def validate_document(self, file: UploadFile) -> None:
        """Validate document file."""
        if file.content_type not in self.ALLOWED_DOC_TYPES:
            raise ValidationException(
                f"Invalid document type. Allowed: {', '.join(self.ALLOWED_DOC_TYPES)}"
            )

        file.file.seek(0, 2)
        size = file.file.tell()
        file.file.seek(0)

        if not validate_file_size(size, self.MAX_DOC_SIZE_MB):
            raise ValidationException(f"Document too large. Max: {self.MAX_DOC_SIZE_MB}MB")

    def resize_image(
            self,
            file_content: bytes,
            max_width: int = 1920,
            max_height: int = 1920,
            quality: int = 85
    ) -> bytes:
        """Resize image if too large."""
        img = Image.open(io.BytesIO(file_content))

        # Convert RGBA to RGB if needed
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background

        # Resize if needed
        if img.width > max_width or img.height > max_height:
            img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

        # Save to bytes
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=quality, optimize=True)
        return output.getvalue()

    def upload_image(
            self,
            file: UploadFile,
            category: str = "general",
            resize: bool = True
    ) -> str:
        """Upload and process image."""
        self.validate_image(file)

        # Read file content
        content = file.file.read()

        # Resize if requested
        if resize:
            content = self.resize_image(content)

        # Create BytesIO object
        file_obj = io.BytesIO(content)

        # Upload to storage
        url = storage_service.upload_file(
            file_obj,
            sanitize_filename(file.filename),
            prefix=f"images/{category}",
            public=True
        )

        if not url:
            raise ValidationException("Upload failed")

        return url

    def upload_document(
            self,
            file: UploadFile,
            user_id: str,
            category: str = "general"
    ) -> str:
        """Upload document."""
        self.validate_document(file)

        url = storage_service.upload_file(
            file.file,
            sanitize_filename(file.filename),
            prefix=f"documents/{user_id}/{category}",
            public=False
        )

        if not url:
            raise ValidationException("Upload failed")

        return url

    def upload_avatar(self, file: UploadFile, user_id: str) -> str:
        """Upload user avatar."""
        self.validate_image(file)

        # Read and resize
        content = file.file.read()
        content = self.resize_image(content, max_width=500, max_height=500, quality=90)

        file_obj = io.BytesIO(content)

        url = storage_service.upload_file(
            file_obj,
            f"avatar_{user_id}.jpg",
            prefix=f"avatars",
            public=True
        )

        if not url:
            raise ValidationException("Upload failed")

        return url


# Singleton instance
upload_service = UploadService()