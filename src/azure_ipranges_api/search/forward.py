"""Forward lookups over an immutable Service Tag index."""

from __future__ import annotations

from ipaddress import IPv4Network, IPv6Network
from typing import TypeAlias

from azure_ipranges_api.models import (
    ForwardLookupItem,
    ForwardLookupResponse,
    IpVersion,
)
from azure_ipranges_api.search.index import ServiceTagIndex, ServiceTagRecord


Network: TypeAlias = IPv4Network | IPv6Network


def _network_sort_key(network: Network) -> tuple[int, int, int]:
    return (
        network.version,
        int(network.network_address),
        network.prefixlen,
    )


def _record_sort_key(
    record: ServiceTagRecord, prefixes: tuple[Network, ...]
) -> tuple[object, ...]:
    return (
        record.name.casefold(),
        record.name,
        tuple(_network_sort_key(prefix) for prefix in prefixes),
        record.region.casefold(),
        record.region,
        record.id.casefold(),
        record.id,
        record.system_service.casefold(),
        record.system_service,
        record.platform.casefold(),
        record.platform,
        record.change_number,
    )


def lookup_forward(
    index: ServiceTagIndex,
    service: str,
    region: str | None = None,
    ip_version: IpVersion | None = None,
) -> ForwardLookupResponse:
    """Return tags whose full name or non-empty system service exactly matches."""

    if not isinstance(service, str) or not service.strip():
        raise ValueError("service must be a non-empty string")
    normalized_service = service.strip()

    if ip_version is not None and (
        isinstance(ip_version, bool)
        or not isinstance(ip_version, int)
        or ip_version not in (4, 6)
    ):
        raise ValueError("ip_version must be 4 or 6")

    service_key = normalized_service.casefold()
    records = set(index.full_name_index.get(service_key, ()))
    records.update(index.system_service_index.get(service_key, ()))
    if region is not None:
        region_key = region.casefold()
        records = {
            record
            for record in records
            if record.region.casefold() == region_key
        }

    selected: list[tuple[ServiceTagRecord, tuple[Network, ...]]] = []
    for record in records:
        prefixes = tuple(
            sorted(
                (
                    prefix
                    for prefix in record.address_prefixes
                    if ip_version is None or prefix.version == ip_version
                ),
                key=_network_sort_key,
            )
        )
        if prefixes:
            selected.append((record, prefixes))

    selected.sort(key=lambda value: _record_sort_key(*value))
    items = [
        ForwardLookupItem(
            name=record.name,
            id=record.id,
            change_number=record.change_number,
            region=record.region,
            platform=record.platform,
            system_service=record.system_service,
            address_prefixes=[str(prefix) for prefix in prefixes],
        )
        for record, prefixes in selected
    ]
    return ForwardLookupResponse(
        service=normalized_service,
        region=region,
        ip_version=ip_version,
        items=items,
    )


forward_lookup = lookup_forward
lookup_ranges = lookup_forward


__all__ = ["forward_lookup", "lookup_forward", "lookup_ranges"]
