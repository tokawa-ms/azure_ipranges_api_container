"""Construction and persistence of cache metadata."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

from azure_ipranges_api.data.downloader import DownloadedServiceTags
from azure_ipranges_api.models import CacheMetadata, ServiceTagsDocument


UtcClock = Callable[[], datetime]


class CacheWriter(Protocol):
    """Write-side cache contract used by metadata persistence."""

    def write(self, validated_json: bytes, metadata: CacheMetadata) -> None: ...


def build_cache_metadata(
    document: ServiceTagsDocument,
    source_url: str,
    filename: str,
    raw_json: bytes,
    *,
    utc_now: UtcClock | None = None,
) -> CacheMetadata:
    """Derive metadata for validated Service Tags JSON bytes."""

    if not isinstance(document, ServiceTagsDocument):
        raise TypeError("document must be a ServiceTagsDocument")
    if not isinstance(raw_json, bytes):
        raise TypeError("raw_json must be bytes")

    fetched_at = (
        utc_now() if utc_now is not None else datetime.now(timezone.utc)
    )
    if not isinstance(fetched_at, datetime):
        raise TypeError("utc_now must return a datetime")
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("utc_now must return a timezone-aware datetime")

    return CacheMetadata(
        source_url=source_url,
        filename=filename,
        fetched_at=fetched_at.astimezone(timezone.utc),
        sha256=hashlib.sha256(raw_json).hexdigest(),
        change_number=document.change_number,
        tag_count=len(document.values),
        prefix_count=sum(
            len(value.properties.address_prefixes) for value in document.values
        ),
    )


def write_downloaded_cache(
    store: CacheWriter,
    document: ServiceTagsDocument,
    downloaded: DownloadedServiceTags,
    *,
    utc_now: UtcClock | None = None,
) -> CacheMetadata:
    """Construct metadata and persist it with the downloaded raw bytes."""

    if not isinstance(downloaded, DownloadedServiceTags):
        raise TypeError("downloaded must be a DownloadedServiceTags")

    metadata = build_cache_metadata(
        document,
        downloaded.source_url,
        downloaded.filename,
        downloaded.content,
        utc_now=utc_now,
    )
    store.write(downloaded.content, metadata)
    return metadata
