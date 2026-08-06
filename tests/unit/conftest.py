"""Shared fixtures for unit tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from azure_ipranges_api.config import Settings
from azure_ipranges_api.models import CacheMetadata


@pytest.fixture
def temp_data_dir(tmp_path: Path) -> Path:
    """Provide a temporary data directory for cache tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def settings_with_temp_dir(temp_data_dir: Path) -> Settings:
    """Provide a Settings instance configured with a temporary data directory."""
    return Settings(data_dir=temp_data_dir)


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
