from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest

from azure_ipranges_api.config import Settings
from azure_ipranges_api.data import (
    CacheStore,
    DownloadedServiceTags,
    InitializationStatus,
    RefreshCoordinator,
)
from azure_ipranges_api.data.metadata import build_cache_metadata
from azure_ipranges_api.errors import DataUnavailableReason
from azure_ipranges_api.models import CacheMetadata, ServiceTagsDocument


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "service-tags-sample.json"
NOW = datetime(2026, 8, 6, 19, 30, tzinfo=timezone.utc)


class StubDownloader:
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
    return Settings(DATA_DIR=tmp_path, CACHE_TTL_SECONDS=3600)


def _persist(store: CacheStore, fetched_at: datetime) -> None:
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


@pytest.mark.asyncio
async def test_fresh_cache_returns_without_downloading(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = CacheStore(settings)
    _persist(store, NOW - timedelta(minutes=30))
    downloader = StubDownloader(FIXTURE_PATH.read_bytes())

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
async def test_missing_cache_downloads_persists_and_reloads(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = CacheStore(settings)
    downloader = StubDownloader(FIXTURE_PATH.read_bytes())

    result = await RefreshCoordinator(
        settings,
        cache=store,
        downloader=downloader,
        utc_now=lambda: NOW,
    ).initialize()

    assert result.available
    assert result.cache is not None
    assert result.cache.metadata.fetched_at == NOW
    assert store.exists()
    assert downloader.calls == 1


@pytest.mark.asyncio
async def test_stale_cache_is_not_returned_when_refresh_fails(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = CacheStore(settings)
    _persist(store, NOW - timedelta(hours=2))
    downloader = StubDownloader(
        FIXTURE_PATH.read_bytes(), failure=RuntimeError("sensitive cause")
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
    assert result.error.reason is DataUnavailableReason.CACHE_EXPIRED
    assert result.error.retryable
    assert "sensitive cause" not in result.error.reason.value


@pytest.mark.asyncio
async def test_future_cache_is_invalid_and_not_returned_on_failure(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = CacheStore(settings)
    _persist(store, NOW + timedelta(minutes=6))
    downloader = StubDownloader(
        FIXTURE_PATH.read_bytes(), failure=RuntimeError("download failed")
    )

    result = await RefreshCoordinator(
        settings,
        cache=store,
        downloader=downloader,
        utc_now=lambda: NOW,
    ).initialize()

    assert result.error is not None
    assert result.error.reason is DataUnavailableReason.CACHE_INVALID


@pytest.mark.asyncio
async def test_cache_is_rechecked_after_acquiring_update_lock(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    real_store = CacheStore(settings)
    downloader = StubDownloader(FIXTURE_PATH.read_bytes())

    class PopulatingStore:
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
            del timeout
            _persist(real_store, NOW)
            yield

    result = await RefreshCoordinator(
        settings,
        cache=PopulatingStore(),
        downloader=downloader,
        utc_now=lambda: NOW,
    ).initialize()

    assert result.available
    assert downloader.calls == 0
