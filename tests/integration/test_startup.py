"""Integration tests for startup cache states and refresh coordination."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

import pytest

from azure_ipranges_api.config import Settings
from azure_ipranges_api.data.cache import CacheStore
from azure_ipranges_api.data.downloader import DownloadedServiceTags
from azure_ipranges_api.data.loader import CacheLoader, CacheLoadErrorReason
from azure_ipranges_api.data.metadata import build_cache_metadata
from azure_ipranges_api.data.refresh import (
    InitializationResult,
    InitializationStatus,
    RefreshCoordinator,
)
from azure_ipranges_api.errors import DataUnavailableReason
from azure_ipranges_api.models import CacheMetadata, ServiceTagsDocument


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "service-tags-sample.json"
NOW = datetime(2026, 8, 6, 19, 30, tzinfo=timezone.utc)
CACHE_TTL = 3600


class FakeDownloader:
    """Injected downloader for testing without HTTP."""

    def __init__(
        self,
        content: bytes,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.content = content
        self.failure = failure
        self.calls = 0

    async def download(self) -> DownloadedServiceTags:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return DownloadedServiceTags(
            source_url=(
                "https://download.microsoft.com/"
                "ServiceTags_Public_20260804.json"
            ),
            filename="ServiceTags_Public_20260804.json",
            content=self.content,
        )


def _settings(tmp_path: Path) -> Settings:
    """Create settings with temporary data directory and fixed TTL."""
    return Settings(data_dir=tmp_path, cache_ttl_seconds=CACHE_TTL)


def _persist_cache(
    store: CacheStore,
    fetched_at: datetime,
    raw_json: bytes | None = None,
) -> CacheMetadata:
    """Persist a valid cache with specified fetched_at time."""
    if raw_json is None:
        raw_json = FIXTURE_PATH.read_bytes()
    document = ServiceTagsDocument.model_validate(json.loads(raw_json))
    metadata = build_cache_metadata(
        document,
        "https://download.microsoft.com/ServiceTags_Public_20260804.json",
        "ServiceTags_Public_20260804.json",
        raw_json,
        utc_now=lambda: fetched_at,
    )
    store.write(raw_json, metadata)
    return metadata


class TestMissingCacheHandling:
    """Tests for missing cache scenarios."""

    @pytest.mark.asyncio
    async def test_missing_cache_downloads_and_becomes_available(
        self, tmp_path: Path
    ) -> None:
        """Missing cache triggers download and becomes available."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        downloader = FakeDownloader(FIXTURE_PATH.read_bytes())

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert result.status is InitializationStatus.AVAILABLE
        assert result.available
        assert result.cache is not None
        assert result.error is None
        assert result.cache.metadata.fetched_at == NOW
        assert store.exists()
        assert downloader.calls == 1

    @pytest.mark.asyncio
    async def test_missing_cache_download_failure_returns_unavailable(
        self, tmp_path: Path
    ) -> None:
        """Download failure with missing cache returns unavailable."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        downloader = FakeDownloader(
            b"", failure=RuntimeError("download failed")
        )

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert not result.available
        assert result.status is InitializationStatus.UNAVAILABLE
        assert result.cache is None
        assert result.error is not None
        assert result.error.reason is DataUnavailableReason.CACHE_MISSING
        assert result.error.retryable
        # Error message must not expose download failure details
        assert "download failed" not in str(result.error)


class TestFreshCacheHandling:
    """Tests for fresh valid cache scenarios."""

    @pytest.mark.asyncio
    async def test_fresh_cache_skips_download(
        self, tmp_path: Path
    ) -> None:
        """Fresh cache is returned without HTTP request."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        _persist_cache(store, NOW - timedelta(minutes=30))
        downloader = FakeDownloader(FIXTURE_PATH.read_bytes())

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert result.status is InitializationStatus.AVAILABLE
        assert result.cache is not None
        assert result.error is None
        assert downloader.calls == 0

    @pytest.mark.asyncio
    async def test_fresh_cache_includes_metadata_and_document(
        self, tmp_path: Path
    ) -> None:
        """Available result includes both metadata and document."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        persisted_metadata = _persist_cache(store, NOW - timedelta(minutes=5))
        downloader = FakeDownloader(FIXTURE_PATH.read_bytes())

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert result.available
        assert result.cache is not None
        assert result.cache.metadata is not None
        assert result.cache.document is not None
        assert result.cache.raw_json is not None
        assert result.cache.metadata.change_number == 100
        assert len(result.cache.document.values) == 3


class TestStaleCacheHandling:
    """Tests for stale cache scenarios."""

    @pytest.mark.asyncio
    async def test_stale_cache_triggers_download(
        self, tmp_path: Path
    ) -> None:
        """Stale cache triggers a refresh download."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        old_time = NOW - timedelta(seconds=CACHE_TTL + 100)
        _persist_cache(store, old_time)
        downloader = FakeDownloader(FIXTURE_PATH.read_bytes())

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert result.available
        assert downloader.calls == 1
        # New metadata has current fetched_at
        assert result.cache.metadata.fetched_at == NOW

    @pytest.mark.asyncio
    async def test_stale_cache_update_failure_returns_unavailable(
        self, tmp_path: Path
    ) -> None:
        """Stale cache with download failure returns unavailable, no stale fallback."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        old_time = NOW - timedelta(seconds=CACHE_TTL + 100)
        _persist_cache(store, old_time)
        downloader = FakeDownloader(
            b"", failure=RuntimeError("download error")
        )

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert not result.available
        assert result.error is not None
        assert result.error.reason is DataUnavailableReason.CACHE_EXPIRED
        assert result.error.retryable
        assert result.cache is None


class TestInvalidCacheHandling:
    """Tests for invalid cache scenarios."""

    @pytest.mark.asyncio
    async def test_invalid_json_triggers_refresh(
        self, tmp_path: Path
    ) -> None:
        """Invalid JSON in cache triggers download."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        # Write invalid JSON
        store.ensure_directory()
        store.service_tags_path.write_bytes(b"{invalid json")
        # Write matching metadata (checksum will mismatch during load)
        metadata = CacheMetadata(
            source_url="https://download.microsoft.com/test.json",
            filename="test.json",
            fetched_at=NOW,
            sha256="a" * 64,
            change_number=100,
            tag_count=3,
            prefix_count=6,
        )
        store.metadata_path.write_bytes(
            metadata.model_dump_json().encode("utf-8")
        )
        downloader = FakeDownloader(FIXTURE_PATH.read_bytes())

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert result.available
        assert downloader.calls == 1

    @pytest.mark.asyncio
    async def test_invalid_schema_triggers_refresh(
        self, tmp_path: Path
    ) -> None:
        """Invalid document schema triggers download."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        # Create valid JSON but invalid schema (missing required fields)
        invalid_json = b'{"changeNumber": 100}'
        store.ensure_directory()
        store.service_tags_path.write_bytes(invalid_json)
        # Write metadata with mismatched checksum
        metadata = CacheMetadata(
            source_url="https://download.microsoft.com/test.json",
            filename="test.json",
            fetched_at=NOW,
            sha256="a" * 64,
            change_number=100,
            tag_count=0,
            prefix_count=0,
        )
        store.metadata_path.write_bytes(
            metadata.model_dump_json().encode("utf-8")
        )
        downloader = FakeDownloader(FIXTURE_PATH.read_bytes())

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert result.available
        assert downloader.calls == 1

    @pytest.mark.asyncio
    async def test_checksum_mismatch_triggers_refresh(
        self, tmp_path: Path
    ) -> None:
        """Metadata checksum mismatch triggers download."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        raw_json = FIXTURE_PATH.read_bytes()
        document = ServiceTagsDocument.model_validate(json.loads(raw_json))
        # Create metadata with wrong checksum
        metadata = CacheMetadata(
            source_url="https://download.microsoft.com/ServiceTags_Public_20260804.json",
            filename="ServiceTags_Public_20260804.json",
            fetched_at=NOW,
            sha256="a" * 64,  # Wrong checksum
            change_number=100,
            tag_count=3,
            prefix_count=6,
        )
        store.ensure_directory()
        store.service_tags_path.write_bytes(raw_json)
        store.metadata_path.write_bytes(
            metadata.model_dump_json().encode("utf-8")
        )
        downloader = FakeDownloader(raw_json)

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert result.available
        assert downloader.calls == 1

    @pytest.mark.asyncio
    async def test_metadata_content_mismatch_triggers_refresh(
        self, tmp_path: Path
    ) -> None:
        """Metadata-document content mismatch triggers download."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        raw_json = FIXTURE_PATH.read_bytes()
        document = ServiceTagsDocument.model_validate(json.loads(raw_json))
        # Create metadata with wrong tag count
        metadata = CacheMetadata(
            source_url="https://download.microsoft.com/ServiceTags_Public_20260804.json",
            filename="ServiceTags_Public_20260804.json",
            fetched_at=NOW,
            sha256=hashlib.sha256(raw_json).hexdigest(),
            change_number=100,
            tag_count=999,  # Wrong count
            prefix_count=6,
        )
        store.ensure_directory()
        store.service_tags_path.write_bytes(raw_json)
        store.metadata_path.write_bytes(
            metadata.model_dump_json().encode("utf-8")
        )
        downloader = FakeDownloader(raw_json)

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert result.available
        assert downloader.calls == 1

    @pytest.mark.asyncio
    async def test_invalid_cache_failure_to_refresh_returns_unavailable(
        self, tmp_path: Path
    ) -> None:
        """Invalid cache with refresh failure returns unavailable."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        # Write invalid cache
        store.ensure_directory()
        store.service_tags_path.write_bytes(b"invalid")
        store.metadata_path.write_bytes(b"{}")
        downloader = FakeDownloader(
            b"", failure=RuntimeError("download failed")
        )

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert not result.available
        assert result.error is not None
        assert result.error.reason is DataUnavailableReason.CACHE_INVALID
        assert result.error.retryable  # Invalid cache with refresh failure is retryable


class TestFutureClockSkew:
    """Tests for future-dated cache handling."""

    @pytest.mark.asyncio
    async def test_future_cache_beyond_clock_skew_triggers_refresh(
        self, tmp_path: Path
    ) -> None:
        """Cache with fetched_at far in future triggers refresh."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        # Create cache with fetched_at 10 minutes in future (exceeds 5 min skew)
        future_time = NOW + timedelta(minutes=10)
        _persist_cache(store, future_time)
        downloader = FakeDownloader(FIXTURE_PATH.read_bytes())

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert result.available
        assert downloader.calls == 1

    @pytest.mark.asyncio
    async def test_future_cache_within_clock_skew_returns_without_refresh(
        self, tmp_path: Path
    ) -> None:
        """Cache with fetched_at within clock skew is accepted."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        # Create cache with fetched_at 2 minutes in future (within 5 min skew)
        future_time = NOW + timedelta(minutes=2)
        _persist_cache(store, future_time)
        downloader = FakeDownloader(FIXTURE_PATH.read_bytes())

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert result.available
        assert downloader.calls == 0

    @pytest.mark.asyncio
    async def test_future_cache_beyond_skew_failure_returns_unavailable(
        self, tmp_path: Path
    ) -> None:
        """Future cache with refresh failure returns unavailable."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        future_time = NOW + timedelta(minutes=10)
        _persist_cache(store, future_time)
        downloader = FakeDownloader(
            b"", failure=RuntimeError("download error")
        )

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert not result.available
        assert result.error is not None
        assert result.error.reason is DataUnavailableReason.CACHE_INVALID


class TestLockRechecking:
    """Tests for cache recheck after lock acquisition."""

    @pytest.mark.asyncio
    async def test_lock_recheck_uses_cache_refreshed_by_another_process(
        self, tmp_path: Path
    ) -> None:
        """After acquiring lock, fresh cache populated by another process is used."""
        settings = _settings(tmp_path)
        real_store = CacheStore(settings)
        downloader = FakeDownloader(FIXTURE_PATH.read_bytes())

        class PopulatingStore:
            """Mock store that populates cache when lock is acquired."""

            def read_json(self) -> bytes:
                return real_store.read_json()

            def read_metadata(self) -> bytes:
                return real_store.read_metadata()

            def write(
                self, validated_json: bytes, metadata: CacheMetadata
            ) -> None:
                real_store.write(validated_json, metadata)

            @contextmanager
            def update_lock(self, timeout: float = -1) -> Iterator[None]:
                # Simulate another process populating cache while waiting
                _persist_cache(real_store, NOW)
                yield

        result = await RefreshCoordinator(
            settings,
            cache=PopulatingStore(),
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert result.available
        # Should not download because cache was populated after lock
        assert downloader.calls == 0


class TestInvalidDownloadHandling:
    """Tests for invalid downloaded content."""

    @pytest.mark.asyncio
    async def test_invalid_downloaded_json_does_not_replace_cache(
        self, tmp_path: Path
    ) -> None:
        """Invalid downloaded JSON does not replace prior cache."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        old_time = NOW - timedelta(seconds=CACHE_TTL + 100)
        old_metadata = _persist_cache(store, old_time)
        old_content = store.read_json()

        # Downloader returns invalid JSON
        downloader = FakeDownloader(b"{invalid json}")

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert not result.available
        # Prior cache files should not be modified
        assert store.read_json() == old_content
        persisted_metadata = json.loads(store.read_metadata())
        # Compare ISO format UTC datetime, handling both +00:00 and Z notation
        persisted_ts = persisted_metadata["fetched_at"]
        expected_ts = old_metadata.fetched_at.isoformat()
        # Normalize both to Z notation for comparison
        assert persisted_ts.replace("+00:00", "Z") == expected_ts.replace("+00:00", "Z")

    @pytest.mark.asyncio
    async def test_invalid_downloaded_schema_does_not_replace_cache(
        self, tmp_path: Path
    ) -> None:
        """Invalid downloaded schema does not replace prior cache."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        old_time = NOW - timedelta(seconds=CACHE_TTL + 100)
        old_metadata = _persist_cache(store, old_time)
        old_content = store.read_json()

        # Downloader returns JSON without required fields
        downloader = FakeDownloader(b'{"changeNumber": 100}')

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert not result.available
        # Prior cache files should not be modified
        assert store.read_json() == old_content


class TestUnavailableStateContract:
    """Tests for unavailable result contract."""

    @pytest.mark.asyncio
    async def test_unavailable_includes_reason_and_retryable_status(
        self, tmp_path: Path
    ) -> None:
        """Unavailable results include safe reason and retryable flag."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        downloader = FakeDownloader(
            b"", failure=RuntimeError("sensitive info")
        )

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert not result.available
        assert result.error is not None
        assert isinstance(result.error.reason, DataUnavailableReason)
        assert isinstance(result.error.retryable, bool)
        # Must not expose internal exception details
        assert "sensitive info" not in str(result.error)

    @pytest.mark.asyncio
    async def test_unavailable_result_has_no_cache_or_error_both_set(
        self, tmp_path: Path
    ) -> None:
        """Unavailable results cannot have both cache and error."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        downloader = FakeDownloader(
            b"", failure=RuntimeError("test")
        )

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert not result.available
        assert result.cache is None
        assert result.error is not None


class TestAvailableStateContract:
    """Tests for available result contract."""

    @pytest.mark.asyncio
    async def test_available_includes_metadata_document_and_json(
        self, tmp_path: Path
    ) -> None:
        """Available results include all cache components."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        downloader = FakeDownloader(FIXTURE_PATH.read_bytes())

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert result.available
        assert result.cache is not None
        assert result.cache.metadata is not None
        assert result.cache.document is not None
        assert result.cache.raw_json is not None
        assert isinstance(result.cache.raw_json, bytes)
        assert result.error is None

    @pytest.mark.asyncio
    async def test_available_result_cannot_have_error_set(
        self, tmp_path: Path
    ) -> None:
        """Available results cannot have error set."""
        settings = _settings(tmp_path)
        store = CacheStore(settings)
        downloader = FakeDownloader(FIXTURE_PATH.read_bytes())

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert result.available
        assert result.cache is not None
        assert result.error is None


class TestEdgeCases:
    """Tests for edge cases and integration scenarios."""

    @pytest.mark.asyncio
    async def test_empty_cache_dir_is_created(
        self, tmp_path: Path
    ) -> None:
        """Cache directory is created if it does not exist."""
        settings = _settings(tmp_path / "nonexistent")
        store = CacheStore(settings)
        downloader = FakeDownloader(FIXTURE_PATH.read_bytes())

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert result.available
        assert settings.data_dir.exists()

    @pytest.mark.asyncio
    async def test_concurrent_lock_ensures_single_download(
        self, tmp_path: Path
    ) -> None:
        """Update lock ensures only one concurrent download attempt."""
        settings = _settings(tmp_path)
        real_store = CacheStore(settings)
        downloader = FakeDownloader(FIXTURE_PATH.read_bytes())

        class CountingStore:
            """Track lock acquisitions."""

            lock_count = 0

            def read_json(self) -> bytes:
                return real_store.read_json()

            def read_metadata(self) -> bytes:
                return real_store.read_metadata()

            def write(
                self, validated_json: bytes, metadata: CacheMetadata
            ) -> None:
                real_store.write(validated_json, metadata)

            @contextmanager
            def update_lock(self, timeout: float = -1) -> Iterator[None]:
                CountingStore.lock_count += 1
                with real_store.update_lock(timeout):
                    yield

        store = CountingStore()

        result = await RefreshCoordinator(
            settings,
            cache=store,
            downloader=downloader,
            utc_now=lambda: NOW,
        ).initialize()

        assert result.available
        # Lock should be acquired exactly once during initialization
        assert CountingStore.lock_count == 1
