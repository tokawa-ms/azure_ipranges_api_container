"""Read-only access to the initialized Azure Service Tags catalog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from azure_ipranges_api.data.refresh import InitializationResult
from azure_ipranges_api.errors import DataUnavailableError, DataUnavailableReason
from azure_ipranges_api.models import (
    ForwardLookupResponse,
    IpVersion,
    MetadataResponse,
    ReadinessResponse,
    ReverseLookupResponse,
)
from azure_ipranges_api.search.forward import lookup_forward
from azure_ipranges_api.search.index import ServiceTagIndex
from azure_ipranges_api.search.reverse import reverse_lookup


@dataclass(frozen=True, slots=True)
class _CatalogMetadata:
    source_url: str
    filename: str
    fetched_at: datetime
    sha256: str
    change_number: int
    tag_count: int
    prefix_count: int

    def response(self) -> MetadataResponse:
        return MetadataResponse(
            source_url=self.source_url,
            filename=self.filename,
            fetched_at=self.fetched_at,
            sha256=self.sha256,
            change_number=self.change_number,
            tag_count=self.tag_count,
            prefix_count=self.prefix_count,
        )


@dataclass(frozen=True, slots=True, init=False)
class CatalogService:
    """Immutable catalog state built once from refresh initialization."""

    _index: ServiceTagIndex | None
    _metadata: _CatalogMetadata | None
    _unavailable_reason: DataUnavailableReason | None
    _retryable: bool

    def __init__(self, result: InitializationResult) -> None:
        if not isinstance(result, InitializationResult):
            raise TypeError("result must be an InitializationResult")

        index: ServiceTagIndex | None = None
        metadata: _CatalogMetadata | None = None
        unavailable_reason: DataUnavailableReason | None = None
        retryable = False

        if result.available:
            cache = result.cache
            if cache is None:
                raise ValueError("available result is missing its loaded cache")
            try:
                index = ServiceTagIndex(cache.document)
                cache_metadata = cache.metadata
                metadata = _CatalogMetadata(
                    source_url=cache_metadata.source_url,
                    filename=cache_metadata.filename,
                    fetched_at=cache_metadata.fetched_at,
                    sha256=cache_metadata.sha256,
                    change_number=cache_metadata.change_number,
                    tag_count=cache_metadata.tag_count,
                    prefix_count=cache_metadata.prefix_count,
                )
            except Exception:
                index = None
                metadata = None
                unavailable_reason = (
                    DataUnavailableReason.INITIALIZATION_FAILED
                )
        else:
            error = result.error
            if error is None:
                raise ValueError("unavailable result is missing its error")
            unavailable_reason = error.reason
            retryable = error.retryable

        object.__setattr__(self, "_index", index)
        object.__setattr__(self, "_metadata", metadata)
        object.__setattr__(self, "_unavailable_reason", unavailable_reason)
        object.__setattr__(self, "_retryable", retryable)

    def forward_lookup(
        self,
        service: str,
        region: str | None = None,
        ip_version: IpVersion | None = None,
    ) -> ForwardLookupResponse:
        """Look up ranges by full tag name or system service."""

        return lookup_forward(
            self._require_index(),
            service,
            region=region,
            ip_version=ip_version,
        )

    def reverse_lookup(self, ip_address: str) -> ReverseLookupResponse:
        """Look up every indexed prefix containing an IP address."""

        items = reverse_lookup(self._require_index(), ip_address)
        return ReverseLookupResponse(ip_address=ip_address, items=items)

    def metadata(self) -> MetadataResponse:
        """Return metadata for the loaded catalog."""

        self._require_available()
        metadata = self._metadata
        if metadata is None:
            raise RuntimeError("available catalog is missing metadata")
        return metadata.response()

    def readiness(self) -> ReadinessResponse:
        """Return catalog readiness without raising for unavailable data."""

        metadata = self._metadata
        if self._index is not None and metadata is not None:
            return ReadinessResponse(
                ready=True,
                fetched_at=metadata.fetched_at,
                change_number=metadata.change_number,
                stale=False,
            )

        reason = self._unavailable_reason
        return ReadinessResponse(
            ready=False,
            reason=reason.value if reason is not None else None,
            stale=reason is DataUnavailableReason.CACHE_EXPIRED,
        )

    def _require_index(self) -> ServiceTagIndex:
        self._require_available()
        index = self._index
        if index is None:
            raise RuntimeError("available catalog is missing its index")
        return index

    def _require_available(self) -> None:
        reason = self._unavailable_reason
        if reason is not None:
            raise DataUnavailableError(reason, retryable=self._retryable)


__all__ = ["CatalogService"]
