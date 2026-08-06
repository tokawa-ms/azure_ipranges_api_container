"""Shared fixtures for API tests."""

from __future__ import annotations

# Import fixtures from parent unit tests conftest
from pathlib import Path

import pytest

from azure_ipranges_api.models import CacheMetadata

from datetime import datetime, timezone


@pytest.fixture
def sample_service_tags_json() -> bytes:
    """Load the sample service tags JSON from fixtures."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "service-tags-sample.json"
    return fixture_path.read_bytes()


@pytest.fixture
def sample_cache_metadata(sample_service_tags_json: bytes) -> CacheMetadata:
    """Create sample cache metadata for the sample service tags."""
    import hashlib

    sha256_digest = hashlib.sha256(sample_service_tags_json).hexdigest()
    return CacheMetadata(
        source_url="https://download.microsoft.com/download/test/ServiceTags_Public_20240101.json",
        filename="ServiceTags_Public_20240101.json",
        fetched_at=datetime.now(timezone.utc),
        sha256=sha256_digest,
        change_number=100,
        tag_count=3,
        prefix_count=6,
    )
