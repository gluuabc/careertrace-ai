import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class StorageError(RuntimeError):
    """Safe application error for object-storage failures."""


class S3ObjectStorage:
    """Private, encrypted S3 storage using the backend AWS credential chain."""

    def __init__(
        self,
        bucket_name: str | None = None,
        region_name: str | None = None,
        client: Any | None = None,
    ):
        self.bucket_name = bucket_name or os.getenv("S3_BUCKET_NAME", "")
        self.region_name = region_name or os.getenv(
            "S3_REGION", os.getenv("AWS_REGION", "us-east-1")
        )
        self.client = client or boto3.client("s3", region_name=self.region_name)

    def _require_bucket(self) -> None:
        if not self.bucket_name:
            raise StorageError("S3_BUCKET_NAME is not configured.")

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._require_bucket()
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=data,
                ContentType=content_type,
                ServerSideEncryption="AES256",
            )
        except (BotoCoreError, ClientError) as error:
            raise StorageError(
                "The document could not be uploaded to private S3 storage."
            ) from error

    def get(self, key: str) -> bytes:
        self._require_bucket()
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=key)
            return response["Body"].read()
        except (BotoCoreError, ClientError) as error:
            raise StorageError(
                "The document could not be downloaded from private S3 storage."
            ) from error

    def delete(self, key: str) -> None:
        self._require_bucket()
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
        except (BotoCoreError, ClientError) as error:
            raise StorageError(
                "The document could not be deleted from private S3 storage."
            ) from error
