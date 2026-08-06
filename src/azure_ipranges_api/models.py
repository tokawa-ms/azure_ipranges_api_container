"""Pydantic models for upstream Service Tags data and API responses."""

from __future__ import annotations

from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{64}$")]
IpVersion = Literal[4, 6]


class ApiModel(BaseModel):
    """Base configuration for models owned by this API."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class UpstreamModel(BaseModel):
    """Base configuration for forward-compatible upstream models."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class ServiceTagProperties(UpstreamModel):
    """Properties attached to one upstream Service Tag value."""

    change_number: int = Field(alias="changeNumber", ge=0)
    region: str
    platform: str
    system_service: str = Field(alias="systemService")
    address_prefixes: list[str] = Field(alias="addressPrefixes")

    @field_validator("address_prefixes")
    @classmethod
    def validate_address_prefixes(cls, prefixes: list[str]) -> list[str]:
        for prefix in prefixes:
            if not prefix or prefix != prefix.strip():
                raise ValueError("address prefixes must be non-empty CIDR strings")
            try:
                ip_network(prefix, strict=True)
            except ValueError as exc:
                raise ValueError(f"invalid address prefix: {prefix}") from exc
        return prefixes


class ServiceTagValue(UpstreamModel):
    """One value entry in an upstream Service Tags document."""

    name: NonEmptyStr
    id: NonEmptyStr
    properties: ServiceTagProperties


class ServiceTagsDocument(UpstreamModel):
    """The validated portion of an Azure Service Tags distribution document."""

    change_number: int = Field(alias="changeNumber", ge=0)
    values: list[ServiceTagValue]


class CacheMetadata(ApiModel):
    """Metadata persisted alongside a validated Service Tags document."""

    source_url: NonEmptyStr
    filename: NonEmptyStr
    fetched_at: datetime
    sha256: Sha256
    change_number: int = Field(ge=0)
    tag_count: int = Field(ge=0)
    prefix_count: int = Field(ge=0)

    @field_validator("fetched_at")
    @classmethod
    def require_utc_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("sha256")
    @classmethod
    def normalize_sha256(cls, value: str) -> str:
        return value.lower()


class ServiceTagResult(ApiModel):
    """Service Tag attributes shared by forward and reverse results."""

    name: NonEmptyStr
    id: NonEmptyStr
    change_number: int = Field(ge=0)
    region: str
    platform: str
    system_service: str


class RangeLookupItem(ServiceTagResult):
    """A Service Tag and the CIDRs selected by a forward lookup."""

    address_prefixes: list[str]

    @field_validator("address_prefixes")
    @classmethod
    def validate_address_prefixes(cls, prefixes: list[str]) -> list[str]:
        for prefix in prefixes:
            try:
                ip_network(prefix, strict=True)
            except ValueError as exc:
                raise ValueError(f"invalid address prefix: {prefix}") from exc
        return prefixes


class RangeLookupResponse(ApiModel):
    """Response returned by the service/region ranges endpoint."""

    service: NonEmptyStr
    region: str | None = None
    ip_version: IpVersion | None = None
    items: list[RangeLookupItem] = Field(default_factory=list)


class IpLookupItem(ServiceTagResult):
    """One matching prefix and Service Tag record for an IP lookup."""

    prefix: str

    @field_validator("prefix")
    @classmethod
    def validate_prefix(cls, prefix: str) -> str:
        try:
            ip_network(prefix, strict=True)
        except ValueError as exc:
            raise ValueError(f"invalid address prefix: {prefix}") from exc
        return prefix


class IpLookupResponse(ApiModel):
    """Response containing every Service Tag prefix matching an IP."""

    ip_address: str
    items: list[IpLookupItem] = Field(default_factory=list)

    @field_validator("ip_address")
    @classmethod
    def validate_ip_address(cls, value: str) -> str:
        try:
            ip_address(value)
        except ValueError as exc:
            raise ValueError("ip_address must be a valid IPv4 or IPv6 address") from exc
        return value


class LivenessResponse(ApiModel):
    """Process liveness response."""

    status: Literal["ok"] = "ok"


class ReadinessResponse(ApiModel):
    """Data readiness response, including cache freshness diagnostics."""

    ready: bool
    reason: str | None = None
    fetched_at: datetime | None = None
    change_number: int | None = Field(default=None, ge=0)
    stale: bool

    @field_validator("fetched_at")
    @classmethod
    def require_utc_aware_datetime(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        return value.astimezone(timezone.utc)


class MetadataResponse(CacheMetadata):
    """Metadata for the currently loaded Service Tags document."""


class ErrorDetail(ApiModel):
    """Machine-readable details for a standardized API error."""

    code: NonEmptyStr
    message: NonEmptyStr
    reason: NonEmptyStr
    retryable: bool


class ErrorResponse(ApiModel):
    """Standard error envelope used by API exception handlers."""

    detail: ErrorDetail


# Concise compatibility names for callers that describe lookups by direction.
ForwardLookupItem = RangeLookupItem
ForwardLookupResponse = RangeLookupResponse
ReverseLookupItem = IpLookupItem
ReverseLookupResponse = IpLookupResponse
