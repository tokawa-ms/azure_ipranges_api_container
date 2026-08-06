"""Cache-first initialization and refresh coordination."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Protocol

from azure_ipranges_api.config import Settings
from azure_ipranges_api.data.cache import CacheStore
from azure_ipranges_api.data.downloader import (
    DownloadedServiceTags,
    ServiceTagsDownloader,
)
from azure_ipranges_api.data.loader import (
    CacheLoadError,
    CacheLoadErrorReason,
    CacheLoader,
    LoadedCache,
)
from azure_ipranges_api.data.metadata import UtcClock, write_downloaded_cache
from azure_ipranges_api.errors import DataUnavailableError, DataUnavailableReason
from azure_ipranges_api.models import CacheMetadata, ServiceTagsDocument


logger = logging.getLogger(__name__)
_FUTURE_CLOCK_SKEW = timedelta(minutes=5)


class DownloaderProtocol(Protocol):
    async def download(self) -> DownloadedServiceTags: ...


class RefreshCacheProtocol(Protocol):
    def read_json(self) -> bytes | str | None: ...

    def read_metadata(self) -> bytes | str | None: ...

    def write(self, validated_json: bytes, metadata: CacheMetadata) -> None: ...

    def update_lock(self, timeout: float = -1) -> Iterator[None]: ...


class InitializationStatus(StrEnum):
    """Whether validated data is available to application services."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class InitializationResult:
    """Typed startup result consumed by catalog and health services."""

    status: InitializationStatus
    cache: LoadedCache | None = None
    error: DataUnavailableError | None = None

    def __post_init__(self) -> None:
        if self.status is InitializationStatus.AVAILABLE:
            if self.cache is None or self.error is not None:
                raise ValueError("available results require only a loaded cache")
        elif self.cache is not None or self.error is None:
            raise ValueError("unavailable results require only an error")

    @property
    def available(self) -> bool:
        return self.status is InitializationStatus.AVAILABLE

    @classmethod
    def from_cache(cls, cache: LoadedCache) -> InitializationResult:
        return cls(status=InitializationStatus.AVAILABLE, cache=cache)

    @classmethod
    def unavailable(
        cls,
        reason: DataUnavailableReason,
        *,
        retryable: bool = True,
    ) -> InitializationResult:
        return cls(
            status=InitializationStatus.UNAVAILABLE,
            error=DataUnavailableError(reason, retryable=retryable),
        )


class _CacheCondition(StrEnum):
    MISSING = "missing"
    INVALID = "invalid"
    STALE = "stale"


class RefreshCoordinator:
    """Initialize application data without ever serving an invalid fallback."""

    def __init__(
        self,
        settings: Settings,
        *,
        downloader: DownloaderProtocol | None = None,
        cache: RefreshCacheProtocol | None = None,
        utc_now: UtcClock | None = None,
    ) -> None:
        self._settings = settings
        self._downloader = downloader or ServiceTagsDownloader(settings)
        self._cache = cache or CacheStore(settings)
        self._loader = CacheLoader(self._cache)
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._initialization_lock = asyncio.Lock()

    async def initialize(self) -> InitializationResult:
        """Load a fresh cache or refresh it while holding the update lock."""

        async with self._initialization_lock:
            try:
                loaded, condition = self._try_load_fresh()
            except Exception:
                logger.exception("Refresh initialization clock check failed")
                return InitializationResult.unavailable(
                    DataUnavailableReason.INITIALIZATION_FAILED,
                    retryable=False,
                )

            if loaded is not None:
                return InitializationResult.from_cache(loaded)

            try:
                with self._cache.update_lock():
                    loaded, condition = self._try_load_fresh()
                    if loaded is not None:
                        return InitializationResult.from_cache(loaded)

                    downloaded = await self._downloader.download()
                    document = self._validate_download(downloaded)
                    write_downloaded_cache(
                        self._cache,
                        document,
                        downloaded,
                        utc_now=self._utc_now,
                    )
                    reloaded = self._loader.load()
                    if not self._is_fresh(reloaded):
                        raise ValueError(
                            "newly persisted cache is not fresh"
                        )
                    return InitializationResult.from_cache(reloaded)
            except Exception:
                logger.exception(
                    "Azure Service Tags refresh failed",
                    extra={"cache_condition": condition.value},
                )
                return InitializationResult.unavailable(
                    self._unavailable_reason(condition)
                )

    def _try_load_fresh(
        self,
    ) -> tuple[LoadedCache | None, _CacheCondition]:
        try:
            loaded = self._loader.load()
        except CacheLoadError as exc:
            condition = (
                _CacheCondition.MISSING
                if exc.reason is CacheLoadErrorReason.MISSING_DATA
                else _CacheCondition.INVALID
            )
            return None, condition
        except Exception:
            logger.exception("Unexpected cache load failure")
            return None, _CacheCondition.INVALID

        if self._is_fresh(loaded):
            return loaded, _CacheCondition.STALE
        return None, self._freshness_failure(loaded)

    def _is_fresh(self, loaded: LoadedCache) -> bool:
        return self._freshness_failure(loaded) is None

    def _freshness_failure(
        self, loaded: LoadedCache
    ) -> _CacheCondition | None:
        now = self._aware_utc_now()
        fetched_at = loaded.metadata.fetched_at
        if fetched_at > now + _FUTURE_CLOCK_SKEW:
            return _CacheCondition.INVALID
        if now - fetched_at > timedelta(
            seconds=self._settings.cache_ttl_seconds
        ):
            return _CacheCondition.STALE
        return None

    def _aware_utc_now(self) -> datetime:
        now = self._utc_now()
        if not isinstance(now, datetime):
            raise TypeError("utc_now must return a datetime")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("utc_now must return a timezone-aware datetime")
        return now.astimezone(timezone.utc)

    @staticmethod
    def _validate_download(
        downloaded: DownloadedServiceTags,
    ) -> ServiceTagsDocument:
        if not isinstance(downloaded, DownloadedServiceTags):
            raise TypeError("downloader returned an invalid result")
        return ServiceTagsDocument.model_validate_json(downloaded.content)

    @staticmethod
    def _unavailable_reason(
        condition: _CacheCondition,
    ) -> DataUnavailableReason:
        if condition is _CacheCondition.MISSING:
            return DataUnavailableReason.CACHE_MISSING
        if condition is _CacheCondition.INVALID:
            return DataUnavailableReason.CACHE_INVALID
        return DataUnavailableReason.CACHE_EXPIRED
