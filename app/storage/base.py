from typing import Protocol


class ObjectStorage(Protocol):
    """Small object-storage contract independent of AWS and SQL."""

    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...
