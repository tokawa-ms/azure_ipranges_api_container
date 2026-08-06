import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from azure_ipranges_api.data.downloader import DownloadedServiceTags
from azure_ipranges_api.data.metadata import (
    build_cache_metadata,
    write_downloaded_cache,
)
from azure_ipranges_api.models import CacheMetadata, ServiceTagsDocument


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "service-tags-sample.json"


def _document_and_bytes() -> tuple[ServiceTagsDocument, bytes]:
    raw_json = FIXTURE_PATH.read_bytes()
    document = ServiceTagsDocument.model_validate(json.loads(raw_json))
    return document, raw_json


def test_build_cache_metadata_derives_content_values() -> None:
    document, raw_json = _document_and_bytes()
    fixed_time = datetime(
        2026,
        8,
        6,
        21,
        30,
        tzinfo=timezone(timedelta(hours=2)),
    )

    metadata = build_cache_metadata(
        document,
        "https://download.microsoft.com/ServiceTags_Public_20260804.json",
        "ServiceTags_Public_20260804.json",
        raw_json,
        utc_now=lambda: fixed_time,
    )

    assert metadata.change_number == 100
    assert metadata.fetched_at == datetime(
        2026, 8, 6, 19, 30, tzinfo=timezone.utc
    )
    assert metadata.sha256 == hashlib.sha256(raw_json).hexdigest()
    assert metadata.tag_count == 3
    assert metadata.prefix_count == 6


def test_build_cache_metadata_rejects_naive_clock() -> None:
    document, raw_json = _document_and_bytes()

    with pytest.raises(ValueError, match="timezone-aware"):
        build_cache_metadata(
            document,
            "https://download.microsoft.com/ServiceTags_Public_20260804.json",
            "ServiceTags_Public_20260804.json",
            raw_json,
            utc_now=lambda: datetime(2026, 8, 6),
        )


def test_write_downloaded_cache_persists_raw_bytes_and_metadata() -> None:
    document, raw_json = _document_and_bytes()
    downloaded = DownloadedServiceTags(
        source_url=(
            "https://download.microsoft.com/ServiceTags_Public_20260804.json"
        ),
        filename="ServiceTags_Public_20260804.json",
        content=raw_json,
    )
    writes: list[tuple[bytes, CacheMetadata]] = []

    class RecordingStore:
        def write(
            self, validated_json: bytes, metadata: CacheMetadata
        ) -> None:
            writes.append((validated_json, metadata))

    metadata = write_downloaded_cache(
        RecordingStore(),
        document,
        downloaded,
        utc_now=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc),
    )

    assert writes == [(raw_json, metadata)]
