"""
File storage service using S3-compatible storage (AWS S3, MinIO, etc.)
"""
import boto3
from botocore.exceptions import ClientError
from typing import Optional, BinaryIO
from pathlib import Path
import mimetypes
from datetime import datetime, timedelta

from app.config import settings
from app.core.utils import generate_random_string, sanitize_filename


# ============================================
# STORAGE SERVICE
# ============================================

class StorageService:
    """S3-compatible storage service."""

    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            endpoint_url=getattr(settings, 'S3_ENDPOINT_URL', None),
            aws_access_key_id=getattr(settings, 'AWS_ACCESS_KEY_ID', ''),
            aws_secret_access_key=getattr(settings, 'AWS_SECRET_ACCESS_KEY', ''),
            region_name=getattr(settings, 'AWS_REGION', 'us-east-1')
        )
        self.bucket_name = getattr(settings, 'S3_BUCKET_NAME', 'localy')
        self.public_url_base = getattr(settings, 'S3_PUBLIC_URL_BASE', '')

    def _get_content_type(self, filename: str) -> str:
        """Get content type from filename."""
        content_type, _ = mimetypes.guess_type(filename)
        return content_type or 'application/octet-stream'

    def _generate_key(self, filename: str, prefix: str = "") -> str:
        """Generate unique storage key."""
        # Sanitize filename
        safe_name = sanitize_filename(filename)

        # Add timestamp and random string to avoid collisions
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        random = generate_random_string(8)

        # Get file extension
        ext = Path(safe_name).suffix
        name_without_ext = Path(safe_name).stem

        # Build key
        unique_name = f"{name_without_ext}_{timestamp}_{random}{ext}"

        if prefix:
            return f"{prefix.strip('/')}/{unique_name}"
        return unique_name

    def upload_file(
            self,
            file_obj: BinaryIO,
            filename: str,
            prefix: str = "",
            public: bool = True,
            metadata: Optional[dict] = None
    ) -> Optional[str]:
        """
        Upload file to storage.

        Args:
            file_obj: File object to upload
            filename: Original filename
            prefix: Folder prefix (e.g., 'products', 'avatars')
            public: Whether file should be publicly accessible
            metadata: Additional metadata

        Returns:
            File URL if successful, None otherwise
        """
        try:
            # Generate unique key
            key = self._generate_key(filename, prefix)

            # Prepare upload args
            extra_args = {
                'ContentType': self._get_content_type(filename)
            }

            if public:
                extra_args['ACL'] = 'public-read'

            if metadata:
                extra_args['Metadata'] = metadata

            # Upload file
            self.s3_client.upload_fileobj(
                file_obj,
                self.bucket_name,
                key,
                ExtraArgs=extra_args
            )

            # Return public URL
            if self.public_url_base:
                return f"{self.public_url_base}/{key}"
            else:
                return f"https://{self.bucket_name}.s3.amazonaws.com/{key}"

        except ClientError as e:
            print(f"Storage upload error: {e}")
            return None

    def upload_from_path(
            self,
            file_path: str,
            prefix: str = "",
            public: bool = True
    ) -> Optional[str]:
        """Upload file from local path."""
        try:
            with open(file_path, 'rb') as f:
                filename = Path(file_path).name
                return self.upload_file(f, filename, prefix, public)
        except Exception as e:
            print(f"Upload from path error: {e}")
            return None

    def delete_file(self, file_url: str) -> bool:
        """Delete file from storage."""
        try:
            # Extract key from URL
            if self.public_url_base in file_url:
                key = file_url.replace(self.public_url_base + '/', '')
            else:
                # Extract from S3 URL
                key = file_url.split('/')[-1]

            # Delete object
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=key
            )
            return True

        except ClientError as e:
            print(f"Storage delete error: {e}")
            return False

    def get_presigned_url(
            self,
            key: str,
            expiration: int = 3600,
            operation: str = 'get_object'
    ) -> Optional[str]:
        """
        Generate presigned URL for temporary access.

        Args:
            key: Object key
            expiration: URL expiration in seconds
            operation: S3 operation (get_object, put_object)

        Returns:
            Presigned URL
        """
        try:
            url = self.s3_client.generate_presigned_url(
                operation,
                Params={'Bucket': self.bucket_name, 'Key': key},
                ExpiresIn=expiration
            )
            return url
        except ClientError as e:
            print(f"Presigned URL error: {e}")
            return None

    def list_files(self, prefix: str = "", max_keys: int = 100) -> list:
        """List files in storage."""
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
                MaxKeys=max_keys
            )

            files = []
            for obj in response.get('Contents', []):
                files.append({
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'],
                    'url': f"{self.public_url_base}/{obj['Key']}"
                })

            return files

        except ClientError as e:
            print(f"List files error: {e}")
            return []

    def file_exists(self, key: str) -> bool:
        """Check if file exists in storage."""
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except ClientError:
            return False

    def get_file_size(self, key: str) -> Optional[int]:
        """Get file size in bytes."""
        try:
            response = self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
            return response['ContentLength']
        except ClientError:
            return None

    def copy_file(self, source_key: str, dest_key: str) -> bool:
        """Copy file within storage."""
        try:
            copy_source = {'Bucket': self.bucket_name, 'Key': source_key}
            self.s3_client.copy_object(
                CopySource=copy_source,
                Bucket=self.bucket_name,
                Key=dest_key
            )
            return True
        except ClientError as e:
            print(f"Copy file error: {e}")
            return False


# ============================================
# HELPER FUNCTIONS
# ============================================

def upload_image(file_obj: BinaryIO, filename: str, category: str = "general") -> Optional[str]:
    """Helper to upload image file."""
    storage = StorageService()
    return storage.upload_file(file_obj, filename, prefix=f"images/{category}", public=True)


def upload_document(file_obj: BinaryIO, filename: str, category: str = "general") -> Optional[str]:
    """Helper to upload document file."""
    storage = StorageService()
    return storage.upload_file(file_obj, filename, prefix=f"documents/{category}", public=False)


def upload_avatar(file_obj: BinaryIO, filename: str, user_id: str) -> Optional[str]:
    """Helper to upload user avatar."""
    storage = StorageService()
    return storage.upload_file(
        file_obj,
        filename,
        prefix=f"avatars/{user_id}",
        public=True
    )


def delete_file(file_url: str) -> bool:
    """Helper to delete file."""
    storage = StorageService()
    return storage.delete_file(file_url)


# Singleton instance
storage_service = StorageService()