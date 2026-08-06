"""Validated loading of a persisted Azure Service Tags cache."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import ValidationError

from azure_ipranges_api.models import CacheMetadata, ServiceTagsDocument


RawJson = bytes | str


class CacheStoreProtocol(Protocol):
    """Minimal read-side contract required from a cache store."""

    def read_json(self) -> RawJson | None: ...

    def read_metadata(self) -> RawJson | None: ...


class CacheLoadErrorReason(StrEnum):
    """Machine-readable, safe cache load failure reasons."""

    MISSING_DATA = "missing_data"
    INVALID_JSON = "invalid_json"
    INVALID_SCHEMA = "invalid_schema"
    INVALID_METADATA = "invalid_metadata"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    METADATA_CONTENT_MISMATCH = "metadata_content_mismatch"


class CacheLoadError(RuntimeError):
    """Raised when persisted cache data cannot be safely loaded."""

    def __init__(self, reason: CacheLoadErrorReason) -> None:
        if not isinstance(reason, CacheLoadErrorReason):
            raise TypeError("reason must be a CacheLoadErrorReason")
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class LoadedCache:
    """A fully validated cached document and its metadata."""

    document: ServiceTagsDocument
    metadata: CacheMetadata
    raw_json: bytes


class CacheLoader:
    """Load and cross-check cache content without refreshing it."""

    def __init__(self, store: CacheStoreProtocol) -> None:
        self._store = store

    def load(self) -> LoadedCache:
        """Read, parse, and verify the complete persisted cache."""

        try:
            raw_json_value = self._store.read_json()
            raw_metadata = self._store.read_metadata()
        except FileNotFoundError as exc:
            raise CacheLoadError(CacheLoadErrorReason.MISSING_DATA) from exc

        if raw_json_value is None or raw_metadata is None:
            raise CacheLoadError(CacheLoadErrorReason.MISSING_DATA)

        raw_json = self._as_bytes(
            raw_json_value,
            invalid_reason=CacheLoadErrorReason.INVALID_JSON,
        )
        metadata_bytes = self._as_bytes(
            raw_metadata,
            invalid_reason=CacheLoadErrorReason.INVALID_METADATA,
        )

        document = self._parse_document(raw_json)
        metadata = self._parse_metadata(metadata_bytes)
        self._verify_checksum(raw_json, metadata)
        self._verify_metadata(document, metadata)

        return LoadedCache(
            document=document,
            metadata=metadata,
            raw_json=raw_json,
        )

    @staticmethod
    def _as_bytes(
        value: RawJson,
        *,
        invalid_reason: CacheLoadErrorReason,
    ) -> bytes:
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8")
        raise CacheLoadError(invalid_reason)

    @staticmethod
    def _parse_document(raw_json: bytes) -> ServiceTagsDocument:
        try:
            payload = json.loads(raw_json)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CacheLoadError(CacheLoadErrorReason.INVALID_JSON) from exc

        try:
            return ServiceTagsDocument.model_validate(payload)
        except ValidationError as exc:
            raise CacheLoadError(CacheLoadErrorReason.INVALID_SCHEMA) from exc

    @staticmethod
    def _parse_metadata(raw_metadata: bytes) -> CacheMetadata:
        try:
            payload = json.loads(raw_metadata)
            return CacheMetadata.model_validate(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            raise CacheLoadError(CacheLoadErrorReason.INVALID_METADATA) from exc

    @staticmethod
    def _verify_checksum(raw_json: bytes, metadata: CacheMetadata) -> None:
        actual_sha256 = hashlib.sha256(raw_json).hexdigest()
        if not hmac.compare_digest(actual_sha256, metadata.sha256):
            raise CacheLoadError(CacheLoadErrorReason.CHECKSUM_MISMATCH)

    @staticmethod
    def _verify_metadata(
        document: ServiceTagsDocument,
        metadata: CacheMetadata,
    ) -> None:
        tag_count = len(document.values)
        prefix_count = sum(
            len(value.properties.address_prefixes) for value in document.values
        )
        if (
            metadata.change_number != document.change_number
            or metadata.tag_count != tag_count
            or metadata.prefix_count != prefix_count
        ):
            raise CacheLoadError(
                CacheLoadErrorReason.METADATA_CONTENT_MISMATCH
            )
