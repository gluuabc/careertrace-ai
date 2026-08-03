"""Object-storage abstractions for original CareerTrace documents."""

from app.storage.s3 import S3ObjectStorage, StorageError

__all__ = ["S3ObjectStorage", "StorageError"]
